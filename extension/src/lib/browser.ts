import { ApiError, requestJson } from './api';
import type { BrowserMode, BrowserSessionResult, PageContext } from './models';

function extractPageContext(): PageContext {
  const normalize = (value: string) => value.replace(/\s+/g, ' ').trim();
  const description = document.querySelector<HTMLMetaElement>('meta[name="description"]')?.content || '';
  const headings = Array.from(document.querySelectorAll<HTMLElement>('h1, h2, h3'))
    .map((element) => normalize(element.innerText))
    .filter(Boolean)
    .slice(0, 12);
  const controls = Array.from(document.querySelectorAll<HTMLElement>('button, a[href]'))
    .map((element) => normalize(element.innerText))
    .filter(Boolean)
    .slice(0, 24);
  const root =
    document.querySelector<HTMLElement>('main, article, [role="main"]') || document.body;
  const bodyText = normalize(root.innerText || '').slice(0, 9_000);
  const content = [
    description && `页面描述：${normalize(description)}`,
    headings.length && `页面标题：${headings.join(' / ')}`,
    bodyText && `页面正文：${bodyText}`,
    controls.length && `可见操作：${controls.join(' / ')}`,
  ]
    .filter(Boolean)
    .join('\n')
    .slice(0, 12_000);

  return {
    url: location.href,
    title: document.title,
    content: content || document.title || location.href,
  };
}

async function readCurrentTabTarget(): Promise<{ tabId: number; url: string } | null> {
  if (typeof chrome === 'undefined' || !chrome.tabs?.query) return null;
  try {
    let [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (!tab?.id) {
      [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    }
    if (!tab?.id) return null;

    let url = tab.url || tab.pendingUrl || '';
    if (!/^https?:\/\//i.test(url) && chrome.scripting?.executeScript) {
      // tabs/activeTab 某些上下文仍会隐藏 URL，用页面自身 location 做只读兜底。
      const [result] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => location.href,
      });
      url = typeof result?.result === 'string' ? result.result : '';
    }
    return /^https?:\/\//i.test(url) ? { tabId: tab.id, url } : null;
  } catch {
    return null;
  }
}

export async function readCurrentPage(): Promise<PageContext | null> {
  if (typeof chrome === 'undefined' || !chrome.scripting?.executeScript) return null;
  const target = await readCurrentTabTarget();
  if (!target) return null;

  const [result] = await chrome.scripting.executeScript({
    target: { tabId: target.tabId },
    func: extractPageContext,
  });
  return result?.result || null;
}

export async function readCurrentTabUrl(): Promise<string | null> {
  return (await readCurrentTabTarget())?.url || null;
}

export function browserSessionId(conversationId: string) {
  const safeConversationId = conversationId.replace(/[^A-Za-z0-9._-]/g, '-').slice(0, 105);
  return `browser-agent-${safeConversationId}`;
}

export async function ensureBrowserSession(
  signal: AbortSignal,
  conversationId: string,
  mode: BrowserMode,
  expectedUrl?: string,
) {
  const sessionId = browserSessionId(conversationId);
  try {
    const current = await requestJson<BrowserSessionResult>(
      `/browser/sessions/${sessionId}`,
      { signal },
    );
    if (current.ready && current.mode === mode) return current;
    await requestJson<BrowserSessionResult>(`/browser/sessions/${sessionId}`, {
      method: 'DELETE',
      signal,
    });
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 404) throw error;
  }

  // URL 只用于选择初始标签页，不限制会话可访问的页面集合。
  const startupExpectedUrl =
    mode === 'current' ? (await readCurrentTabUrl()) || expectedUrl : undefined;
  try {
    return await requestJson<BrowserSessionResult>('/browser/session/start', {
      method: 'POST',
      signal,
      body: JSON.stringify({
        browser_session_id: sessionId,
        mode,
        ...(mode === 'current' && startupExpectedUrl && { expected_url: startupExpectedUrl }),
      }),
    });
  } catch (error) {
    if (mode === 'current' && error instanceof ApiError) {
      throw new ApiError('无法连接当前浏览器。请确认 Chrome 已允许连接；本对话不会自动改用独立浏览器。', error.status);
    }
    throw error;
  }
}
