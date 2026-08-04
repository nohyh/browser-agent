import {
  ArrowClockwise,
  ArrowUp,
  Browser,
  Browsers,
  CaretRight,
  Check,
  ClockCounterClockwise,
  Eye,
  EyeSlash,
  GearSix,
  Plus,
  Trash,
  X,
} from '@phosphor-icons/react';
import { FormEvent, ReactNode, RefObject, useEffect, useMemo, useRef, useState } from 'react';
import './App.css';

type View = 'chat' | 'settings';
type MessageRole = 'user' | 'assistant';
type RunPhase = 'idle' | 'starting' | 'running';
type StorageStatus = 'loading' | 'idle' | 'saving' | 'saved' | 'error';
type BrowserMode = 'current' | 'isolated';

interface Message {
  id: string;
  role: MessageRole;
  content: string;
  createdAt?: number;
  trace?: TraceEvent[];
}

interface TraceEvent {
  kind: string;
  status: 'running' | 'completed' | 'failed';
  title: string;
  detail?: string | null;
  timestamp?: string | null;
  step_id?: string | null;
}

interface Session {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: Message[];
  llmSelection?: ModelSelection;
  browserMode?: BrowserMode;
  browserTargetUrl?: string;
}

interface ModelConfig {
  apiUrl: string;
  apiKey: string;
  model: string;
}

interface ModelSelection {
  endpointId: string;
  model: string;
}

interface ModelEndpoint {
  id: string;
  name: string;
  apiUrl: string;
  apiKey: string;
  availableModels: string[];
  enabledModels: string[];
}

interface ModelSettings {
  endpoints: ModelEndpoint[];
  defaultSelection: ModelSelection | null;
}

interface LLMModelsResult {
  models: string[];
}

interface AgentResult {
  success: boolean;
  answer: string;
}

interface AgentRunPayload {
  message: string;
  conversation_id: string;
  browser_session_id: string;
  run_id: string;
  llm_endpoint_id?: string;
  llm_model?: string;
}

interface PageContext {
  url: string;
  title: string;
  content: string;
}

interface PageSuggestionsResult {
  suggestions: string[];
}

interface BrowserSessionResult {
  browser_session_id: string;
  mode: 'current' | 'isolated' | 'existing';
  ready: boolean;
  url: string | null;
}

interface FailedTask {
  conversationId: string;
  message: string;
  llmSelection: ModelSelection | null;
  browserMode: BrowserMode;
  browserTargetUrl?: string;
}

interface PendingTask {
  message: string;
  llmSelection: ModelSelection | null;
}

interface BrowserDialogState {
  sessionId: string | null;
  pendingTask?: PendingTask;
}

const MODEL_CONFIG_STORAGE_KEY = 'modelConfig';
const MODEL_SETTINGS_STORAGE_KEY = 'modelSettings';
const CHAT_SESSIONS_STORAGE_KEY = 'chatSessions';
const BACKEND_URL = (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const DEFAULT_MODEL_CONFIG: ModelConfig = {
  apiUrl: 'https://api.openai.com/v1',
  apiKey: '',
  model: 'gpt-5',
};
const DEFAULT_MODEL_SETTINGS: ModelSettings = {
  endpoints: [
    {
      id: 'default',
      name: 'OpenAI',
      apiUrl: DEFAULT_MODEL_CONFIG.apiUrl,
      apiKey: '',
      availableModels: [],
      enabledModels: [],
    },
  ],
  defaultSelection: null,
};

class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function makeId(prefix: string) {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function getChromeStorage() {
  if (typeof chrome === 'undefined' || !chrome.storage?.local) return null;
  return chrome.storage.local;
}

async function readStoredValue<T>(key: string): Promise<T | undefined> {
  const storage = getChromeStorage();
  if (storage) {
    const stored = await storage.get(key);
    return stored[key] as T | undefined;
  }

  const stored = localStorage.getItem(key);
  return stored ? (JSON.parse(stored) as T) : undefined;
}

async function writeStoredValue(key: string, value: unknown) {
  const storage = getChromeStorage();
  if (storage) {
    await storage.set({ [key]: value });
    return;
  }
  localStorage.setItem(key, JSON.stringify(value));
}

function isModelConfig(value: unknown): value is ModelConfig {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<ModelConfig>;
  return (
    typeof candidate.apiUrl === 'string' &&
    typeof candidate.apiKey === 'string' &&
    typeof candidate.model === 'string'
  );
}

function isModelSelection(value: unknown): value is ModelSelection {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<ModelSelection>;
  return typeof candidate.endpointId === 'string' && typeof candidate.model === 'string';
}

function isModelEndpoint(value: unknown): value is ModelEndpoint {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<ModelEndpoint>;
  return (
    typeof candidate.id === 'string' &&
    typeof candidate.name === 'string' &&
    typeof candidate.apiUrl === 'string' &&
    typeof candidate.apiKey === 'string' &&
    Array.isArray(candidate.availableModels) &&
    candidate.availableModels.every((model) => typeof model === 'string') &&
    Array.isArray(candidate.enabledModels) &&
    candidate.enabledModels.every((model) => typeof model === 'string')
  );
}

function isModelSettings(value: unknown): value is ModelSettings {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<ModelSettings>;
  return (
    Array.isArray(candidate.endpoints) &&
    candidate.endpoints.every(isModelEndpoint) &&
    (candidate.defaultSelection === null || isModelSelection(candidate.defaultSelection))
  );
}

function settingsFromLegacy(config: ModelConfig): ModelSettings {
  return {
    endpoints: [
      {
        id: 'default',
        name: 'OpenAI',
        apiUrl: config.apiUrl,
        apiKey: config.apiKey,
        availableModels: config.model ? [config.model] : [],
        enabledModels: config.model ? [config.model] : [],
      },
    ],
    defaultSelection: config.model ? { endpointId: 'default', model: config.model } : null,
  };
}

async function readModelSettings(): Promise<ModelSettings> {
  const settings = await readStoredValue<unknown>(MODEL_SETTINGS_STORAGE_KEY);
  if (isModelSettings(settings)) return settings;
  const legacy = await readStoredValue<unknown>(MODEL_CONFIG_STORAGE_KEY);
  return isModelConfig(legacy) ? settingsFromLegacy(legacy) : DEFAULT_MODEL_SETTINGS;
}

function enabledModelOptions(settings: ModelSettings) {
  return settings.endpoints.flatMap((endpoint) =>
    endpoint.enabledModels.map((model) => ({
      value: `${endpoint.id}::${model}`,
      label: `${endpoint.name} / ${model}`,
      selection: { endpointId: endpoint.id, model },
    })),
  );
}

function isBrowserMode(value: unknown): value is BrowserMode {
  return value === 'current' || value === 'isolated';
}

function isSession(value: unknown): value is Session {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<Session>;
  return (
    typeof candidate.id === 'string' &&
    typeof candidate.title === 'string' &&
    typeof candidate.createdAt === 'number' &&
    typeof candidate.updatedAt === 'number' &&
    Array.isArray(candidate.messages)
  );
}

function normalizeSession(value: unknown): Session | null {
  if (!isSession(value)) return null;
  const candidate = value as Session & {
    browserMode?: unknown;
    browserTargetUrl?: unknown;
  };
  const normalized: Session = {
    id: candidate.id,
    title: candidate.title,
    createdAt: candidate.createdAt,
    updatedAt: candidate.updatedAt,
    messages: candidate.messages,
    ...(candidate.llmSelection && { llmSelection: candidate.llmSelection }),
  };
  if (isBrowserMode(candidate.browserMode)) {
    normalized.browserMode = candidate.browserMode;
    if (
      candidate.browserMode === 'current' &&
      typeof candidate.browserTargetUrl === 'string' &&
      candidate.browserTargetUrl
    ) {
      normalized.browserTargetUrl = candidate.browserTargetUrl;
    }
  }
  return normalized;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BACKEND_URL}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  });
  const body = (await response.json().catch(() => null)) as T | null;
  if (!response.ok) {
    throw new ApiError(apiErrorMessage(body, response.status), response.status);
  }
  return body as T;
}

async function streamAgentTask(
  payload: AgentRunPayload,
  signal: AbortSignal,
  onTrace: (event: TraceEvent) => void,
): Promise<AgentResult> {
  const response = await fetch(`${BACKEND_URL}/agent/run/stream`, {
    method: 'POST',
    signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(apiErrorMessage(body, response.status), response.status);
  }
  if (!response.body) throw new ApiError('后端没有返回任务流', 502);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let result: AgentResult | null = null;

  const processLine = (line: string) => {
    if (!line.trim()) return;
    const event = JSON.parse(line) as {
      type: string;
      event?: TraceEvent;
      result?: AgentResult;
      status?: number;
      detail?: unknown;
    };
    if (event.type === 'trace' && event.event) onTrace(event.event);
    if (event.type === 'result' && event.result) result = event.result;
    if (event.type === 'error') {
      throw new ApiError(apiErrorMessage({ detail: event.detail }, event.status || 500), event.status || 500);
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';
    lines.forEach(processLine);
    if (done) break;
  }
  processLine(buffer);
  if (!result) throw new ApiError('任务流结束但没有最终结果', 502);
  return result;
}

function apiErrorMessage(body: unknown, status: number) {
  if (!body || typeof body !== 'object' || !('detail' in body)) {
    return `请求失败 (${status})`;
  }

  const detail = body.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const message = detail.message;
    if (typeof message === 'string') return message;
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item && typeof item === 'object' && 'msg' in item ? item.msg : null))
      .filter((message): message is string => typeof message === 'string');
    if (messages.length) return messages.join('；');
  }
  return `请求失败 (${status})`;
}

async function syncModelSettings(settings: ModelSettings, signal?: AbortSignal) {
  return requestJson<{ configured: boolean }>('/llm/configs', {
    method: 'PUT',
    signal,
    body: JSON.stringify({
      endpoints: settings.endpoints
        .filter((endpoint) => endpoint.apiUrl.trim() && endpoint.apiKey.trim() && endpoint.enabledModels.length)
        .map((endpoint) => ({
          id: endpoint.id,
          name: endpoint.name.trim() || endpoint.id,
          api_url: endpoint.apiUrl.trim().replace(/\/$/, ''),
          api_key: endpoint.apiKey.trim(),
          models: endpoint.enabledModels,
        })),
    }),
  });
}

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

async function readCurrentPage(): Promise<PageContext | null> {
  if (typeof chrome === 'undefined' || !chrome.scripting?.executeScript) return null;
  const target = await readCurrentTabTarget();
  if (!target) return null;

  const [result] = await chrome.scripting.executeScript({
    target: { tabId: target.tabId },
    func: extractPageContext,
  });
  return result?.result || null;
}

async function readCurrentTabUrl(): Promise<string | null> {
  return (await readCurrentTabTarget())?.url || null;
}

function browserSessionId(conversationId: string) {
  const safeConversationId = conversationId.replace(/[^A-Za-z0-9._-]/g, '-').slice(0, 105);
  return `browser-agent-${safeConversationId}`;
}

async function ensureBrowserSession(
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

function friendlyError(error: unknown) {
  if (
    error instanceof ApiError &&
    (error.status < 500 || error.message.startsWith('无法连接当前浏览器'))
  ) {
    return error.message;
  }
  return '无法连接本地服务，请确认后端已经启动。';
}

function sessionTitle(message: string) {
  const normalized = message.replace(/\s+/g, ' ').trim();
  return normalized.length > 28 ? `${normalized.slice(0, 28)}...` : normalized;
}

function formatSessionTime(timestamp: number) {
  const date = new Date(timestamp);
  const now = new Date();
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  }
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return '昨天';
  return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
}

function IconButton({
  label,
  children,
  onClick,
  active = false,
}: {
  label: string;
  children: ReactNode;
  onClick: () => void;
  active?: boolean;
}) {
  return (
    <button
      className={`icon-button${active ? ' is-active' : ''}`}
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}>
      {children}
    </button>
  );
}

function AppHeader({
  onOpenChat,
  onNewChat,
  onOpenSessions,
  onOpenSettings,
  settingsActive,
}: {
  onOpenChat: () => void;
  onNewChat: () => void;
  onOpenSessions: () => void;
  onOpenSettings: () => void;
  settingsActive: boolean;
}) {
  return (
    <header className="app-header">
      <button className="brand-button" type="button" aria-label="返回对话" onClick={onOpenChat}>
        <span className="brand-logo">
          <img src="/logo-bust.png" alt="Browser Agent" />
        </span>
        <strong>Browser Agent</strong>
      </button>
      <nav className="header-actions" aria-label="主要操作">
        <IconButton label="新建会话" onClick={onNewChat}>
          <Plus size={18} />
        </IconButton>
        <IconButton label="打开会话" onClick={onOpenSessions}>
          <ClockCounterClockwise size={18} />
        </IconButton>
        <IconButton label="打开设置" onClick={onOpenSettings} active={settingsActive}>
          <GearSix size={18} />
        </IconButton>
      </nav>
    </header>
  );
}

function SessionDrawer({
  open,
  sessions,
  activeSessionId,
  onClose,
  onSelect,
  onDelete,
  onNewChat,
}: {
  open: boolean;
  sessions: Session[];
  activeSessionId: string | null;
  onClose: () => void;
  onSelect: (session: Session) => void;
  onDelete: (sessionId: string) => void;
  onNewChat: () => void;
}) {
  const [query, setQuery] = useState('');
  const filteredSessions = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return [...sessions]
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .filter((session) => session.title.toLowerCase().includes(normalizedQuery));
  }, [query, sessions]);

  useEffect(() => {
    if (!open) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', closeOnEscape);
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, [onClose, open]);

  if (!open) return null;

  return (
    <div className="drawer-layer">
      <button className="drawer-backdrop" type="button" aria-label="关闭会话列表" onClick={onClose} />
      <aside className="session-drawer" role="dialog" aria-modal="true" aria-label="会话列表">
        <div className="drawer-header">
          <div>
            <h2>会话</h2>
            <p>{sessions.length ? `${sessions.length} 个本地会话` : '暂无历史记录'}</p>
          </div>
          <IconButton label="关闭" onClick={onClose}>
            <X size={18} />
          </IconButton>
        </div>
        <button
          className="new-session-button"
          type="button"
          aria-label="从历史新建会话"
          onClick={onNewChat}>
          <Plus size={17} />
          新建会话
        </button>
        {sessions.length > 0 && (
          <label className="search-field">
            <span className="sr-only">搜索会话</span>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索会话" />
          </label>
        )}
        <div className="session-list">
          {filteredSessions.length ? (
            filteredSessions.map((session) => {
              const lastMessage = session.messages.at(-1)?.content || '尚无消息';
              return (
                <article
                  className={`session-row${activeSessionId === session.id ? ' is-active' : ''}`}
                  key={session.id}>
                  <button className="session-select" type="button" onClick={() => onSelect(session)}>
                    <strong>{session.title}</strong>
                    <small>{lastMessage}</small>
                    <time dateTime={new Date(session.updatedAt).toISOString()}>
                      {formatSessionTime(session.updatedAt)}
                    </time>
                  </button>
                  <button
                    className="session-delete"
                    type="button"
                    aria-label={`删除会话：${session.title}`}
                    title="删除会话"
                    onClick={() => onDelete(session.id)}>
                    <Trash size={15} />
                  </button>
                </article>
              );
            })
          ) : (
            <div className="drawer-empty">
              <ClockCounterClockwise size={22} aria-hidden="true" />
              <strong>{sessions.length ? '没有匹配的会话' : '还没有会话'}</strong>
              <span>{sessions.length ? '换个关键词试试' : '发送第一个任务后会保存在这里'}</span>
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}

function BrowserChoiceDialog({
  open,
  existingSession,
  willRunTask,
  busy,
  error,
  onClose,
  onChoose,
}: {
  open: boolean;
  existingSession: boolean;
  willRunTask: boolean;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onChoose: (mode: BrowserMode) => void;
}) {
  useEffect(() => {
    if (!open || busy) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', closeOnEscape);
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, [busy, onClose, open]);

  if (!open) return null;

  return (
    <div className="browser-choice-layer">
      <button
        className="browser-choice-backdrop"
        type="button"
        aria-label="取消选择浏览器"
        disabled={busy}
        onClick={onClose}
      />
      <section className="browser-choice-dialog" role="dialog" aria-modal="true" aria-label="选择浏览器">
        <div className="browser-choice-heading">
          <div>
            <h2>{existingSession ? '绑定这个历史对话' : '新建对话'}</h2>
            <p>选择本对话使用的浏览器，创建后不可切换。</p>
          </div>
          <IconButton label="关闭" onClick={onClose}>
            <X size={18} />
          </IconButton>
        </div>
        <div className="browser-choice-options">
          <button type="button" disabled={busy} aria-label="使用当前浏览器" onClick={() => onChoose('current')}>
            <Browser size={22} aria-hidden="true" />
            <span>
              <strong>当前浏览器</strong>
              <small>连接当前 Chrome，可操作已有页面并打开新页面。</small>
            </span>
          </button>
          <button type="button" disabled={busy} aria-label="使用独立浏览器" onClick={() => onChoose('isolated')}>
            <Browsers size={22} aria-hidden="true" />
            <span>
              <strong>独立浏览器</strong>
              <small>启动一个由后端管理的独立 Chrome。</small>
            </span>
          </button>
        </div>
        <div className={`browser-choice-status${error ? ' is-error' : ''}`} role={error ? 'alert' : 'status'}>
          {error || (busy
            ? '正在确认当前页面'
            : (willRunTask ? '选择后将立即执行这条任务。' : '需要另一种浏览器时，请新建对话。'))}
        </div>
      </section>
    </div>
  );
}

function EmptyChat({
  suggestions,
  loading,
  onSelect,
}: {
  suggestions: string[];
  loading: boolean;
  onSelect: (suggestion: string) => void;
}) {
  return (
    <div className="empty-chat">
      <div className="empty-chat-content">
        <h1>今天想完成什么？</h1>
        {loading && (
          <div className="suggestion-skeleton" role="status" aria-label="正在分析当前页面">
            <span className="sr-only">正在分析当前页面</span>
            <i />
            <i />
            <i />
          </div>
        )}
        {!loading && suggestions.length > 0 && (
          <div className="page-suggestions" aria-label="基于当前页面的建议" aria-live="polite">
            {suggestions.map((suggestion) => (
              <button type="button" key={suggestion} onClick={() => onSelect(suggestion)}>
                {suggestion}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function TaskTrajectory({
  events,
  completed = true,
}: {
  events: TraceEvent[];
  completed?: boolean;
}) {
  if (!events.length) return null;

  // 直接复用轨迹时间戳，避免为纯展示额外启动计时器。
  const timestamps = events
    .map((event) => Date.parse(event.timestamp || ''))
    .filter(Number.isFinite);
  const elapsedSeconds = timestamps.length > 1
    ? Math.max(0, timestamps.at(-1)! - timestamps[0]) / 1000
    : 0;
  const duration = elapsedSeconds < 10 ? elapsedSeconds.toFixed(1) : Math.round(elapsedSeconds).toString();

  return (
    <details className="trajectory">
      <summary aria-label={completed ? undefined : '展开执行轨迹'}>
        {completed && <span>操作了 {duration}s</span>}
        <CaretRight size={11} weight="bold" aria-hidden="true" />
      </summary>
      <ol>
        {events.map((event, index) => (
          <li className={`is-${event.status}`} key={`${event.step_id || event.kind}-${index}`}>
            <span>{event.title}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

function MessageThread({
  messages,
  phase,
  liveTrace,
}: {
  messages: Message[];
  phase: RunPhase;
  liveTrace: TraceEvent[];
}) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (typeof endRef.current?.scrollIntoView === 'function') {
      endRef.current.scrollIntoView({ block: 'end', behavior: phase === 'idle' ? 'smooth' : 'auto' });
    }
  }, [messages, phase]);

  return (
    <div className="message-thread" aria-live="polite">
      {messages.map((message) => (
        <article className={`message ${message.role}`} key={message.id}>
          {message.role === 'assistant' && (
            <span className="assistant-avatar" aria-hidden="true">
              <img src="/logo-bust.png" alt="" />
            </span>
          )}
          <div className="message-body">
            <span className="message-author">{message.role === 'user' ? '你' : 'Browser Agent'}</span>
            <p>{message.content}</p>
            {message.role === 'assistant' && message.trace && <TaskTrajectory events={message.trace} />}
          </div>
        </article>
      ))}
      {phase !== 'idle' && (
        <article className="message assistant is-running">
          <span className="assistant-avatar" aria-hidden="true">
            <img src="/logo-bust.png" alt="" />
          </span>
          <div className="message-body">
            <span className="message-author">Browser Agent</span>
            <p>{phase === 'starting' ? '正在准备浏览器' : '正在执行任务'}</p>
            <TaskTrajectory events={liveTrace} completed={false} />
            <span className="activity-line" aria-hidden="true" />
          </div>
        </article>
      )}
      <div ref={endRef} />
    </div>
  );
}

function Composer({
  value,
  phase,
  modelOptions,
  selectedModel,
  textareaRef,
  onChange,
  onModelChange,
  onSubmit,
  onStop,
}: {
  value: string;
  phase: RunPhase;
  modelOptions: ReturnType<typeof enabledModelOptions>;
  selectedModel: ModelSelection | null;
  textareaRef: RefObject<HTMLTextAreaElement>;
  onChange: (value: string) => void;
  onModelChange: (selection: ModelSelection) => void;
  onSubmit: () => void;
  onStop: () => void;
}) {
  const running = phase !== 'idle';

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 148)}px`;
  }, [value]);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit();
  };

  return (
    <div className="composer-wrap">
      <form className="composer" onSubmit={handleSubmit}>
        <textarea
          ref={textareaRef}
          aria-label="任务内容"
          placeholder="描述你想在浏览器中完成的任务"
          rows={1}
          value={value}
          disabled={running}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              onSubmit();
            }
          }}
        />
        <div className="composer-actions">
          {modelOptions.length > 0 && (
            <select
              aria-label="选择模型"
              value={selectedModel ? `${selectedModel.endpointId}::${selectedModel.model}` : ''}
              disabled={running}
              onChange={(event) => {
                const option = modelOptions.find((candidate) => candidate.value === event.target.value);
                if (option) onModelChange(option.selection);
              }}>
              {modelOptions.map((option) => (
                <option value={option.value} key={option.value}>{option.label}</option>
              ))}
            </select>
          )}
          <span>
            {running
              ? (phase === 'starting' ? '正在连接' : '任务进行中')
              : 'Enter 发送'}
          </span>
          {running ? (
            <button className="stop-button" type="button" onClick={onStop}>
              停止
            </button>
          ) : (
            <button className="send-button" type="submit" aria-label="发送任务" disabled={!value.trim()}>
              <ArrowUp size={17} weight="bold" />
            </button>
          )}
        </div>
      </form>
    </div>
  );
}

function ChatView({
  activeSession,
  phase,
  draft,
  suggestions,
  suggestionsLoading,
  error,
  notice,
  liveTrace,
  modelOptions,
  selectedModel,
  onDraftChange,
  onModelChange,
  onSuggestionSelect,
  onSubmit,
  onStop,
  onRetry,
}: {
  activeSession: Session | null;
  phase: RunPhase;
  draft: string;
  suggestions: string[];
  suggestionsLoading: boolean;
  error: string | null;
  notice: string | null;
  liveTrace: TraceEvent[];
  modelOptions: ReturnType<typeof enabledModelOptions>;
  selectedModel: ModelSelection | null;
  onDraftChange: (value: string) => void;
  onModelChange: (selection: ModelSelection) => void;
  onSuggestionSelect: (suggestion: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  onRetry: () => void;
}) {
  const messages = activeSession?.messages ?? [];
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const selectSuggestion = (suggestion: string) => {
    onSuggestionSelect(suggestion);
    composerRef.current?.focus();
  };
  return (
    <main className="view chat-view">
      {activeSession && (
        <div className="conversation-title">
          <strong>{activeSession.title}</strong>
          {activeSession.browserMode && (
            <span>{activeSession.browserMode === 'current' ? '当前浏览器' : '独立浏览器'}</span>
          )}
        </div>
      )}
      <div className="chat-scroll">
        {messages.length || phase !== 'idle' ? (
          <MessageThread messages={messages} phase={phase} liveTrace={liveTrace} />
        ) : (
          <EmptyChat
            suggestions={suggestions}
            loading={suggestionsLoading}
            onSelect={selectSuggestion}
          />
        )}
      </div>
      {(error || notice) && (
        <div className={error ? 'task-feedback is-error' : 'task-feedback'} role={error ? 'alert' : 'status'}>
          <span>{error || notice}</span>
          {error && (
            <button type="button" onClick={onRetry}>
              <ArrowClockwise size={14} />
              重试
            </button>
          )}
        </div>
      )}
      <Composer
        value={draft}
        phase={phase}
        modelOptions={modelOptions}
        selectedModel={selectedModel}
        textareaRef={composerRef}
        onChange={onDraftChange}
        onModelChange={onModelChange}
        onSubmit={onSubmit}
        onStop={onStop}
      />
    </main>
  );
}

function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className={`field${error ? ' has-error' : ''}`}>
      <label>
        <span>{label}</span>
        {children}
      </label>
      {error ? <small className="field-error">{error}</small> : hint ? <small>{hint}</small> : null}
    </div>
  );
}


function SettingsView({
  settings,
  onSaved,
}: {
  settings: ModelSettings;
  onSaved: (settings: ModelSettings) => void;
}) {
  const [endpoints, setEndpoints] = useState<ModelEndpoint[]>(settings.endpoints);
  const [storageStatus, setStorageStatus] = useState<StorageStatus>('idle');
  const [visibleKeys, setVisibleKeys] = useState<Record<string, boolean>>({});
  const [loadingModels, setLoadingModels] = useState<Record<string, boolean>>({});
  const [endpointErrors, setEndpointErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    setEndpoints(settings.endpoints);
  }, [settings]);

  const updateEndpoint = (endpointId: string, update: Partial<ModelEndpoint>) => {
    setEndpoints((current) =>
      current.map((endpoint) => endpoint.id === endpointId ? { ...endpoint, ...update } : endpoint),
    );
    if (storageStatus === 'saved' || storageStatus === 'error') setStorageStatus('idle');
  };

  const addEndpoint = () => {
    setEndpoints((current) => [
      ...current,
      {
        id: makeId('endpoint'),
        name: '',
        apiUrl: '',
        apiKey: '',
        availableModels: [],
        enabledModels: [],
      },
    ]);
  };

  const discoverModels = async (endpoint: ModelEndpoint) => {
    if (!endpoint.apiUrl.trim() || !endpoint.apiKey.trim()) {
      setEndpointErrors((current) => ({ ...current, [endpoint.id]: '请先填写 API 地址和 API Key' }));
      return;
    }
    setLoadingModels((current) => ({ ...current, [endpoint.id]: true }));
    setEndpointErrors((current) => ({ ...current, [endpoint.id]: '' }));
    try {
      const result = await requestJson<LLMModelsResult>('/llm/models', {
        method: 'POST',
        body: JSON.stringify({
          api_url: endpoint.apiUrl.trim().replace(/\/$/, ''),
          api_key: endpoint.apiKey.trim(),
        }),
      });
      // 自动获取后默认全选，用户只需取消不希望在对话中出现的模型。
      updateEndpoint(endpoint.id, {
        availableModels: result.models,
        enabledModels: result.models,
      });
    } catch (error) {
      setEndpointErrors((current) => ({ ...current, [endpoint.id]: friendlyError(error) }));
    } finally {
      setLoadingModels((current) => ({ ...current, [endpoint.id]: false }));
    }
  };

  const toggleModel = (endpoint: ModelEndpoint, model: string) => {
    updateEndpoint(endpoint.id, {
      enabledModels: endpoint.enabledModels.includes(model)
        ? endpoint.enabledModels.filter((candidate) => candidate !== model)
        : [...endpoint.enabledModels, model],
    });
  };

  const formValid = endpoints.length > 0 && endpoints.every((endpoint) => {
    try {
      const url = new URL(endpoint.apiUrl);
      return (
        Boolean(endpoint.name.trim() && endpoint.apiKey.trim() && endpoint.enabledModels.length) &&
        ['http:', 'https:'].includes(url.protocol)
      );
    } catch {
      return false;
    }
  });

  const saveSettings = async (event: FormEvent) => {
    event.preventDefault();
    if (!formValid) {
      setStorageStatus('error');
      return;
    }
    setStorageStatus('saving');
    const normalizedEndpoints = endpoints.map((endpoint) => ({
      ...endpoint,
      name: endpoint.name.trim(),
      apiUrl: endpoint.apiUrl.trim().replace(/\/$/, ''),
      apiKey: endpoint.apiKey.trim(),
    }));
    const availableSelections = normalizedEndpoints.flatMap((endpoint) =>
      endpoint.enabledModels.map((model) => ({ endpointId: endpoint.id, model })),
    );
    const currentDefault = settings.defaultSelection;
    const defaultSelection =
      currentDefault &&
      availableSelections.some(
        (selection) =>
          selection.endpointId === currentDefault.endpointId && selection.model === currentDefault.model,
      )
        ? currentDefault
        : availableSelections[0] || null;
    const nextSettings = { endpoints: normalizedEndpoints, defaultSelection };
    try {
      await syncModelSettings(nextSettings);
      await writeStoredValue(MODEL_SETTINGS_STORAGE_KEY, nextSettings);
      onSaved(nextSettings);
      setStorageStatus('saved');
    } catch {
      setStorageStatus('error');
    }
  };

  const statusText = {
    loading: '正在读取本地配置',
    idle: '保存后会同步到本地后端',
    saving: '正在保存',
    saved: '已保存并应用到本地后端',
    error: formValid ? '保存失败，请重试' : '请完整配置每个调用方并至少勾选一个模型',
  }[storageStatus];

  return (
    <main className="view settings-view">
      <div className="page-heading">
        <h1>模型配置</h1>
        <p>一个调用方可启用多个模型，并在每个对话中随时切换。</p>
      </div>
      <form className="model-form" aria-label="模型配置" onSubmit={saveSettings}>
        <div className="form-heading">
          <h2>调用方</h2>
          <button className="add-endpoint-button" type="button" onClick={addEndpoint}>
            <Plus size={14} />
            添加调用方
          </button>
        </div>
        <div className="endpoint-list">
          {endpoints.map((endpoint, index) => (
            <section className="endpoint-card" key={endpoint.id}>
              <div className="endpoint-card-heading">
                <strong>{endpoint.name.trim() || `调用方 ${index + 1}`}</strong>
                {endpoints.length > 1 && (
                  <button
                    type="button"
                    aria-label={`删除调用方 ${endpoint.name || index + 1}`}
                    onClick={() => setEndpoints((current) => current.filter((item) => item.id !== endpoint.id))}>
                    <Trash size={15} />
                  </button>
                )}
              </div>
              <Field label="调用方名称">
                <input
                  aria-label="调用方名称"
                  value={endpoint.name}
                  placeholder="例如 DeepSeek"
                  onChange={(event) => updateEndpoint(endpoint.id, { name: event.target.value })}
                />
              </Field>
              <Field label="API 地址" hint="填写兼容 OpenAI API 的 /v1 地址">
                <input
                  aria-label="API 地址"
                  inputMode="url"
                  value={endpoint.apiUrl}
                  placeholder="https://api.example.com/v1"
                  onChange={(event) => updateEndpoint(endpoint.id, { apiUrl: event.target.value })}
                />
              </Field>
              <Field label="API Key" hint="密钥只保存在当前 Chrome，并发送给本地后端">
                <div className="input-with-action">
                  <input
                    aria-label="API Key"
                    type={visibleKeys[endpoint.id] ? 'text' : 'password'}
                    autoComplete="off"
                    value={endpoint.apiKey}
                    onChange={(event) => updateEndpoint(endpoint.id, { apiKey: event.target.value })}
                  />
                  <button
                    type="button"
                    aria-label={visibleKeys[endpoint.id] ? '隐藏 API Key' : '显示 API Key'}
                    onClick={() =>
                      setVisibleKeys((current) => ({ ...current, [endpoint.id]: !current[endpoint.id] }))
                    }>
                    {visibleKeys[endpoint.id] ? <EyeSlash size={17} /> : <Eye size={17} />}
                  </button>
                </div>
              </Field>
              <div className="model-discovery">
                <button
                  type="button"
                  disabled={loadingModels[endpoint.id]}
                  onClick={() => void discoverModels(endpoint)}>
                  <ArrowClockwise size={15} />
                  {loadingModels[endpoint.id] ? '获取中' : '获取模型'}
                </button>
                <span>{endpoint.enabledModels.length ? `已选 ${endpoint.enabledModels.length} 个` : '尚未选择模型'}</span>
              </div>
              {endpointErrors[endpoint.id] && (
                <p className="endpoint-error" role="alert">{endpointErrors[endpoint.id]}</p>
              )}
              {endpoint.availableModels.length > 0 && (
                <div className="model-checklist" aria-label={`${endpoint.name || '调用方'}模型`}>
                  {endpoint.availableModels.map((model) => (
                    <label key={model}>
                      <input
                        type="checkbox"
                        checked={endpoint.enabledModels.includes(model)}
                        onChange={() => toggleModel(endpoint, model)}
                      />
                      <span>{model}</span>
                    </label>
                  ))}
                </div>
              )}
            </section>
          ))}
        </div>
        <div className="settings-footer">
          <span className={storageStatus === 'error' ? 'is-error' : ''} aria-live="polite">
            {statusText}
          </span>
          <button type="submit" aria-label="保存配置" disabled={!formValid || storageStatus === 'saving'}>
            {storageStatus === 'saved' && <Check size={16} weight="bold" />}
            {storageStatus === 'saving' ? '保存中' : storageStatus === 'saved' ? '已保存' : '保存配置'}
          </button>
        </div>
      </form>
    </main>
  );
}

export default function App() {
  const [activeView, setActiveView] = useState<View>('chat');
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [storageReady, setStorageReady] = useState(false);
  const [phase, setPhase] = useState<RunPhase>('idle');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [failedTask, setFailedTask] = useState<FailedTask | null>(null);
  const [modelSettings, setModelSettings] = useState<ModelSettings>(DEFAULT_MODEL_SETTINGS);
  const [draftSelection, setDraftSelection] = useState<ModelSelection | null>(null);
  const [liveTrace, setLiveTrace] = useState<TraceEvent[]>([]);
  const [browserDialog, setBrowserDialog] = useState<BrowserDialogState | null>(null);
  const [browserBindingBusy, setBrowserBindingBusy] = useState(false);
  const [browserBindingError, setBrowserBindingError] = useState<string | null>(null);
  const requestController = useRef<AbortController | null>(null);
  const activeRunId = useRef<string | null>(null);

  const activeSession = useMemo(
    () => sessions.find((session) => session.id === activeSessionId) ?? null,
    [activeSessionId, sessions],
  );
  const modelOptions = useMemo(() => enabledModelOptions(modelSettings), [modelSettings]);
  const selectedModel = activeSession?.llmSelection || draftSelection || modelSettings.defaultSelection;

  useEffect(() => {
    let mounted = true;
    const restoreSessions = async () => {
      try {
        const [stored, storedModelSettings] = await Promise.all([
          readStoredValue<unknown>(CHAT_SESSIONS_STORAGE_KEY),
          readModelSettings(),
        ]);
        if (mounted && Array.isArray(stored)) {
          setSessions(stored.map(normalizeSession).filter((session): session is Session => session !== null));
        }
        if (mounted) {
          setModelSettings(storedModelSettings);
          setDraftSelection(storedModelSettings.defaultSelection);
        }
      } finally {
        if (mounted) setStorageReady(true);
      }
    };
    void restoreSessions();
    void requestJson<{ status: string }>('/health').catch(() => undefined);
    return () => {
      mounted = false;
      requestController.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (!storageReady) return undefined;
    let mounted = true;
    const controller = new AbortController();
    const loadPageSuggestions = async () => {
      if (!enabledModelOptions(modelSettings).length) {
        setSuggestions([]);
        setSuggestionsLoading(false);
        return;
      }
      if (mounted) {
        setSuggestions([]);
        setSuggestionsLoading(true);
      }
      try {
        const page = await readCurrentPage();
        if (!page) return;

        await syncModelSettings(modelSettings, controller.signal);
        const result = await requestJson<PageSuggestionsResult>('/page/suggestions', {
          method: 'POST',
          signal: controller.signal,
          body: JSON.stringify({
            ...page,
            locale: 'zh-CN',
            limit: 3,
          }),
        });
        if (mounted) {
          setSuggestions(
            result.suggestions
              .filter((suggestion) => typeof suggestion === 'string' && suggestion.trim())
              .slice(0, 3),
          );
        }
      } catch {
        // 首页建议失败时保持原有空状态，不阻塞用户直接输入任务。
      } finally {
        if (mounted) setSuggestionsLoading(false);
      }
    };

    void loadPageSuggestions();
    return () => {
      mounted = false;
      controller.abort();
    };
  }, [modelSettings, storageReady]);

  useEffect(() => {
    if (!storageReady) return;
    void writeStoredValue(CHAT_SESSIONS_STORAGE_KEY, sessions).catch(() => undefined);
  }, [sessions, storageReady]);

  const updateSession = (sessionId: string, update: (session: Session) => Session) => {
    setSessions((current) => current.map((session) => (session.id === sessionId ? update(session) : session)));
  };

  const startNewChat = () => {
    setActiveView('chat');
    setSessionsOpen(false);
    setBrowserBindingError(null);
    setBrowserDialog({ sessionId: null });
  };

  const selectSession = (session: Session) => {
    if (!session.browserMode) {
      setActiveView('chat');
      setSessionsOpen(false);
      setBrowserBindingError(null);
      setBrowserDialog({ sessionId: session.id });
      return;
    }
    requestController.current?.abort();
    setActiveSessionId(session.id);
    setPhase('idle');
    setError(null);
    setNotice(null);
    setFailedTask(null);
    setActiveView('chat');
    setSessionsOpen(false);
  };

  const closeBrowserDialog = () => {
    if (browserBindingBusy) return;
    setBrowserDialog(null);
    setBrowserBindingError(null);
  };

  const bindBrowser = async (mode: BrowserMode) => {
    if (!browserDialog || browserBindingBusy) return;
    const dialogState = browserDialog;
    setBrowserBindingBusy(true);
    setBrowserBindingError(null);
    try {
      const targetUrl = mode === 'current' ? await readCurrentTabUrl() : null;

      requestController.current?.abort();
      const now = Date.now();
      const existingSessionId = dialogState.sessionId;
      const pendingTask = dialogState.pendingTask;
      const conversationId = existingSessionId || makeId('conversation');
      const userMessage: Message | null = pendingTask
        ? {
            id: makeId('user'),
            role: 'user',
            content: pendingTask.message,
            createdAt: now,
          }
        : null;
      if (existingSessionId) {
        updateSession(existingSessionId, (session) => ({
          ...session,
          browserMode: mode,
          ...(targetUrl ? { browserTargetUrl: targetUrl } : {}),
          ...(userMessage && {
            title: session.messages.length ? session.title : sessionTitle(userMessage.content),
            updatedAt: now,
            messages: [...session.messages, userMessage],
          }),
        }));
      } else {
        const initialSelection = pendingTask?.llmSelection || draftSelection || modelSettings.defaultSelection;
        setSessions((current) => [
          {
            id: conversationId,
            title: userMessage ? sessionTitle(userMessage.content) : '新对话',
            createdAt: now,
            updatedAt: now,
            messages: userMessage ? [userMessage] : [],
            browserMode: mode,
            ...(targetUrl ? { browserTargetUrl: targetUrl } : {}),
            ...(initialSelection && { llmSelection: initialSelection }),
          },
          ...current,
        ]);
      }

      setActiveSessionId(conversationId);
      setDraft('');
      setPhase('idle');
      setError(null);
      setNotice(null);
      setFailedTask(null);
      setActiveView('chat');
      setBrowserDialog(null);
      if (pendingTask) {
        void executeTask(
          conversationId,
          pendingTask.message,
          pendingTask.llmSelection,
          mode,
          targetUrl || undefined,
        );
      }
    } catch (caught) {
      setBrowserBindingError(caught instanceof Error ? caught.message : '无法读取当前标签页。');
    } finally {
      setBrowserBindingBusy(false);
    }
  };

  const deleteSession = async (sessionId: string) => {
    const deletingActiveSession = activeSessionId === sessionId;
    const runId = deletingActiveSession ? activeRunId.current : null;
    const controller = deletingActiveSession ? requestController.current : null;
    setSessions((current) => current.filter((session) => session.id !== sessionId));
    if (deletingActiveSession) setActiveSessionId(null);

    // 先等待 Agent 的 finally 移除页面覆盖层，再断开浏览器 runtime。
    if (runId) {
      await fetch(`${BACKEND_URL}/agent/runs/${runId}`, { method: 'DELETE' }).catch(() => undefined);
    }
    if (deletingActiveSession) {
      controller?.abort();
      if (requestController.current === controller) {
        requestController.current = null;
        activeRunId.current = null;
        setLiveTrace([]);
        setPhase('idle');
        setError(null);
        setFailedTask(null);
      }
    }

    // 删除对话时同步回收它独占的浏览器 runtime，避免历史会话越积越多。
    await fetch(`${BACKEND_URL}/browser/sessions/${browserSessionId(sessionId)}`, {
      method: 'DELETE',
    }).catch(() => undefined);
  };

  const selectModel = (selection: ModelSelection) => {
    if (activeSessionId) {
      updateSession(activeSessionId, (session) => ({ ...session, llmSelection: selection }));
    } else {
      setDraftSelection(selection);
    }
  };

  const executeTask = async (
    conversationId: string,
    message: string,
    llmSelection: ModelSelection | null,
    browserMode: BrowserMode,
    browserTargetUrl?: string,
  ) => {
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    setError(null);
    setNotice(null);
    setFailedTask(null);
    setLiveTrace([]);
    setPhase('starting');

    try {
      if (modelOptions.length) {
        await syncModelSettings(modelSettings, controller.signal);
      }
      await ensureBrowserSession(controller.signal, conversationId, browserMode, browserTargetUrl);
      setPhase('running');
      const runId = makeId('run');
      activeRunId.current = runId;
      const runPayload: AgentRunPayload = {
        message,
        conversation_id: conversationId,
        browser_session_id: browserSessionId(conversationId),
        run_id: runId,
        ...(llmSelection && {
          llm_endpoint_id: llmSelection.endpointId,
          llm_model: llmSelection.model,
        }),
      };
      const trace: TraceEvent[] = [];
      let result: AgentResult;
      try {
        result = await streamAgentTask(runPayload, controller.signal, (event) => {
          trace.push(event);
          setLiveTrace([...trace]);
        });
      } catch (streamError) {
        if (!(streamError instanceof ApiError) || streamError.status !== 404) throw streamError;
        // 兼容尚未升级流式接口的本地后端。
        result = await requestJson<AgentResult>('/agent/run', {
          method: 'POST',
          signal: controller.signal,
          body: JSON.stringify(runPayload),
        });
      }
      const now = Date.now();
      updateSession(conversationId, (session) => ({
        ...session,
        updatedAt: now,
        messages: [
          ...session.messages,
          {
            id: makeId('assistant'),
            role: 'assistant',
            content: result.answer,
            createdAt: now,
            trace,
          },
        ],
      }));
      if (!result.success) setNotice('任务没有完整完成，请查看返回结果。');
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return;
      setError(friendlyError(caught));
      setFailedTask({ conversationId, message, llmSelection, browserMode, browserTargetUrl });
    } finally {
      if (requestController.current === controller) {
        requestController.current = null;
        activeRunId.current = null;
        setLiveTrace([]);
        setPhase('idle');
      }
    }
  };

  const submitTask = () => {
    const content = draft.trim();
    if (!content || phase !== 'idle') return;
    if (!activeSession?.browserMode) {
      setBrowserBindingError(null);
      setBrowserDialog({
        sessionId: activeSession?.id || null,
        pendingTask: { message: content, llmSelection: selectedModel },
      });
      return;
    }

    const now = Date.now();
    const userMessage: Message = {
      id: makeId('user'),
      role: 'user',
      content,
      createdAt: now,
    };
    const conversationId = activeSession.id;
    updateSession(conversationId, (session) => ({
      ...session,
      title: session.messages.length ? session.title : sessionTitle(content),
      updatedAt: now,
      messages: [...session.messages, userMessage],
    }));
    setDraft('');
    void executeTask(
      conversationId,
      content,
      selectedModel,
      activeSession.browserMode,
      activeSession.browserTargetUrl,
    );
  };

  const stopTask = () => {
    if (activeRunId.current) {
      void fetch(`${BACKEND_URL}/agent/runs/${activeRunId.current}`, { method: 'DELETE' }).catch(
        () => undefined,
      );
    }
    requestController.current?.abort();
    requestController.current = null;
    setPhase('idle');
    setError(null);
    setFailedTask(null);
    setNotice('已停止等待本次任务结果。');
  };

  const retryTask = () => {
    if (!failedTask) return;
    void executeTask(
      failedTask.conversationId,
      failedTask.message,
      failedTask.llmSelection,
      failedTask.browserMode,
      failedTask.browserTargetUrl,
    );
  };

  return (
    <div className="app-shell">
      <AppHeader
        onOpenChat={() => setActiveView('chat')}
        onNewChat={startNewChat}
        onOpenSessions={() => setSessionsOpen(true)}
        onOpenSettings={() => setActiveView('settings')}
        settingsActive={activeView === 'settings'}
      />
      <div className="view-stack">
        {activeView === 'chat' ? (
          <ChatView
            activeSession={activeSession}
            phase={phase}
            draft={draft}
            suggestions={suggestions}
            suggestionsLoading={suggestionsLoading}
            error={error}
            notice={notice}
            liveTrace={liveTrace}
            modelOptions={modelOptions}
            selectedModel={selectedModel}
            onDraftChange={setDraft}
            onModelChange={selectModel}
            onSuggestionSelect={(suggestion) => {
              setDraft(suggestion);
            }}
            onSubmit={submitTask}
            onStop={stopTask}
            onRetry={retryTask}
          />
        ) : (
          <SettingsView
            settings={modelSettings}
            onSaved={(settings) => {
              setModelSettings(settings);
              setDraftSelection(settings.defaultSelection);
            }}
          />
        )}
      </div>
      <SessionDrawer
        open={sessionsOpen}
        sessions={sessions}
        activeSessionId={activeSessionId}
        onClose={() => setSessionsOpen(false)}
        onSelect={selectSession}
        onDelete={deleteSession}
        onNewChat={startNewChat}
      />
      <BrowserChoiceDialog
        open={browserDialog !== null}
        existingSession={Boolean(browserDialog?.sessionId)}
        willRunTask={Boolean(browserDialog?.pendingTask)}
        busy={browserBindingBusy}
        error={browserBindingError}
        onClose={closeBrowserDialog}
        onChoose={(mode) => void bindBrowser(mode)}
      />
    </div>
  );
}
