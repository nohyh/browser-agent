import {
  ArrowUp,
  CaretRight,
  Check,
  ClockCounterClockwise,
  Eye,
  EyeSlash,
  GearSix,
  Paperclip,
  Plus,
  X,
} from '@phosphor-icons/react';
import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import './App.css';

type View = 'chat' | 'settings';
type MessageRole = 'user' | 'assistant';
type BrowserMode = 'isolated' | 'existing';

interface Message {
  id: string;
  role: MessageRole;
  content: string;
}

interface Session {
  id: string;
  title: string;
  preview: string;
  updatedAt: string;
  messages: Message[];
}

interface ModelConfig {
  apiUrl: string;
  apiKey: string;
  model: string;
}

interface BackendConfig {
  backendUrl: string;
  cdpUrl: string;
}

interface BrowserSessionResult {
  browser_session_id: string;
  mode: BrowserMode;
  ready: boolean;
  url: string | null;
}

interface AgentResult {
  success: boolean;
  answer: string;
  token_usage?: unknown;
}

type StorageStatus = 'loading' | 'idle' | 'saving' | 'saved' | 'error';

const MODEL_CONFIG_STORAGE_KEY = 'modelConfig';
const BACKEND_CONFIG_STORAGE_KEY = 'backendConfig';
const DEFAULT_MODEL_CONFIG: ModelConfig = {
  apiUrl: 'https://api.openai.com/v1',
  apiKey: '',
  model: 'gpt-5',
};
const DEFAULT_BACKEND_CONFIG: BackendConfig = {
  backendUrl: 'http://127.0.0.1:8000',
  cdpUrl: '9222',
};

function isModelConfig(value: unknown): value is ModelConfig {
  if (!value || typeof value !== 'object') return false;

  const candidate = value as Partial<ModelConfig>;
  return (
    typeof candidate.apiUrl === 'string' &&
    typeof candidate.apiKey === 'string' &&
    typeof candidate.model === 'string'
  );
}

function isBackendConfig(value: unknown): value is BackendConfig {
  if (!value || typeof value !== 'object') return false;

  const candidate = value as Partial<BackendConfig>;
  return (
    typeof candidate.backendUrl === 'string' &&
    typeof candidate.cdpUrl === 'string'
  );
}

function normalizeBackendUrl(value: string) {
  return value.trim().replace(/\/+$/, '') || DEFAULT_BACKEND_CONFIG.backendUrl;
}

function createId(prefix: string) {
  const randomId =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${randomId}`;
}

function isAbortError(error: unknown) {
  return (
    (error instanceof DOMException && error.name === 'AbortError') ||
    (error instanceof Error && error.name === 'AbortError')
  );
}

async function requestBackend<T>(
  backendUrl: string,
  path: string,
  init: RequestInit,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${normalizeBackendUrl(backendUrl)}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(init.headers ?? {}),
      },
    });
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new Error('无法连接后端，请确认 Browser Agent 服务已启动。');
  }

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      payload && typeof payload === 'object' && 'detail' in payload
        ? (payload as { detail?: unknown }).detail
        : null;
    const message =
      typeof detail === 'string'
        ? detail
        : `后端请求失败（${response.status}）`;
    throw new Error(message);
  }
  return payload as T;
}

function getLocalStorageArea() {
  // 普通网页预览没有扩展 API，实际扩展环境会使用 chrome.storage.local。
  if (typeof chrome === 'undefined' || !chrome.storage?.local) return null;
  return chrome.storage.local;
}

const sessions: Session[] = [
  {
    id: 'research',
    title: '整理本周研究资料',
    preview: '已汇总 6 个页面，并按主题完成分类',
    updatedAt: '12 分钟前',
    messages: [
      {
        id: 'research-user',
        role: 'user',
        content: '整理我这周打开的 Agent 相关资料，按架构、评测和产品设计分类。',
      },
      {
        id: 'research-assistant',
        role: 'assistant',
        content: '已整理 6 个页面。架构类 3 篇，评测类 2 篇，产品设计类 1 篇。',
      },
    ],
  },
  {
    id: 'flight',
    title: '比较上海到东京的航班',
    preview: '找到 4 个直飞选项，等待确认日期',
    updatedAt: '昨天',
    messages: [
      {
        id: 'flight-user',
        role: 'user',
        content: '比较下个月第一个周末上海到东京的直飞航班，优先早去晚回。',
      },
      {
        id: 'flight-assistant',
        role: 'assistant',
        content: '找到 4 个符合时间偏好的直飞选项。需要你确认是否接受周六早班。',
      },
    ],
  },
  {
    id: 'docs',
    title: '提取 API 文档要点',
    preview: '完成鉴权、限流和错误码摘要',
    updatedAt: '7 月 26 日',
    messages: [
      {
        id: 'docs-user',
        role: 'user',
        content: '阅读当前页面的 API 文档，提取鉴权、限流和常见错误。',
      },
      {
        id: 'docs-assistant',
        role: 'assistant',
        content: '已整理鉴权流程、限流规则和常见错误码。',
      },
    ],
  },
];

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
      <div className="header-actions">
        <IconButton label="新建会话" onClick={onNewChat}>
          <Plus size={19} />
        </IconButton>
        <IconButton label="打开会话" onClick={onOpenSessions}>
          <ClockCounterClockwise size={19} />
        </IconButton>
        <IconButton label="打开设置" onClick={onOpenSettings} active={settingsActive}>
          <GearSix size={19} />
        </IconButton>
      </div>
    </header>
  );
}

function SessionDrawer({
  open,
  activeSessionId,
  onClose,
  onSelect,
  onNewChat,
}: {
  open: boolean;
  activeSessionId: string | null;
  onClose: () => void;
  onSelect: (session: Session) => void;
  onNewChat: () => void;
}) {
  const [query, setQuery] = useState('');
  const filteredSessions = useMemo(
    () => sessions.filter((session) => session.title.toLowerCase().includes(query.trim().toLowerCase())),
    [query],
  );

  if (!open) return null;

  return (
    <div className="drawer-layer">
      <button className="drawer-backdrop" type="button" aria-label="关闭会话列表" onClick={onClose} />
      <aside className="session-drawer" role="dialog" aria-modal="true" aria-label="会话列表">
        <div className="drawer-header">
          <div>
            <h2>会话</h2>
            <p>继续之前的任务</p>
          </div>
          <IconButton label="关闭" onClick={onClose}>
            <X size={19} />
          </IconButton>
        </div>
        <button className="new-session-button" type="button" onClick={onNewChat}>
          <Plus size={18} />
          新建会话
        </button>
        <label className="search-field">
          <span className="sr-only">搜索会话</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索会话" />
        </label>
        <div className="session-list">
          {filteredSessions.length ? (
            filteredSessions.map((session) => (
              <button
                className={`session-row${activeSessionId === session.id ? ' is-active' : ''}`}
                type="button"
                key={session.id}
                aria-label={`${session.title}，${session.updatedAt}`}
                onClick={() => onSelect(session)}>
                <span className="session-copy">
                  <strong>{session.title}</strong>
                  <small>{session.preview}</small>
                  <time>{session.updatedAt}</time>
                </span>
                <CaretRight size={17} />
              </button>
            ))
          ) : (
            <div className="drawer-empty">没有匹配的会话</div>
          )}
        </div>
      </aside>
    </div>
  );
}

function EmptyChat() {
  return (
    <div className="empty-chat">
      <h1>需要我做什么？</h1>
      <p>告诉我你想在当前网页完成的任务。</p>
    </div>
  );
}

function MessageThread({
  messages,
  running,
  runningLabel,
}: {
  messages: Message[];
  running: boolean;
  runningLabel: string;
}) {
  return (
    <div className="message-thread">
      {messages.map((message) => (
        <div className={`message ${message.role}`} key={message.id}>
          {message.role === 'assistant' && (
            <span className="assistant-avatar" aria-hidden="true">
              <img src="/logo-bust.png" alt="" />
            </span>
          )}
          <div className="message-body">
            <span className="message-author">{message.role === 'user' ? '你' : 'Browser Agent'}</span>
            <p>{message.content}</p>
          </div>
        </div>
      ))}
      {running && (
        <div className="message assistant">
          <span className="assistant-avatar" aria-hidden="true">
            <img src="/logo-bust.png" alt="" />
          </span>
          <div className="message-body">
            <span className="message-author">Browser Agent</span>
            <p className="running-copy">{runningLabel}</p>
          </div>
        </div>
      )}
    </div>
  );
}

function BrowserModePicker({
  mode,
  cdpUrl,
  disabled,
  onModeChange,
  onCdpUrlChange,
}: {
  mode: BrowserMode;
  cdpUrl: string;
  disabled: boolean;
  onModeChange: (mode: BrowserMode) => void;
  onCdpUrlChange: (value: string) => void;
}) {
  return (
    <section className="browser-mode" aria-labelledby="browser-mode-title">
      <div className="browser-mode-heading">
        <span id="browser-mode-title">浏览器</span>
        <small>{mode === 'isolated' ? '使用独立 profile' : '接管当前浏览器'}</small>
      </div>
      <div className="mode-options" role="radiogroup" aria-label="浏览器模式">
        <label className={`mode-option${mode === 'isolated' ? ' is-selected' : ''}`}>
          <input
            type="radio"
            name="browser-mode"
            value="isolated"
            aria-label="独立 profile"
            checked={mode === 'isolated'}
            disabled={disabled}
            onChange={() => onModeChange('isolated')}
          />
          <span>
            <strong>独立 profile</strong>
            <small>新开一个干净浏览器</small>
          </span>
        </label>
        <label className={`mode-option${mode === 'existing' ? ' is-selected' : ''}`}>
          <input
            type="radio"
            name="browser-mode"
            value="existing"
            aria-label="当前浏览器"
            checked={mode === 'existing'}
            disabled={disabled}
            onChange={() => onModeChange('existing')}
          />
          <span>
            <strong>当前浏览器</strong>
            <small>保留登录态和已打开页面</small>
          </span>
        </label>
      </div>
      {mode === 'existing' && (
        <label className="cdp-field">
          <span>连接地址</span>
          <input
            aria-label="当前浏览器连接地址"
            value={cdpUrl}
            disabled={disabled}
            onChange={(event) => onCdpUrlChange(event.target.value)}
            placeholder="9222 或 http://127.0.0.1:9222"
          />
        </label>
      )}
    </section>
  );
}

function Composer({
  value,
  running,
  browserMode,
  cdpUrl,
  onBrowserModeChange,
  onCdpUrlChange,
  onChange,
  onSubmit,
  onStop,
}: {
  value: string;
  running: boolean;
  browserMode: BrowserMode;
  cdpUrl: string;
  onBrowserModeChange: (mode: BrowserMode) => void;
  onCdpUrlChange: (value: string) => void;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onStop: () => void;
}) {
  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit();
  };

  return (
    <div className="composer-wrap">
      <BrowserModePicker
        mode={browserMode}
        cdpUrl={cdpUrl}
        disabled={running}
        onModeChange={onBrowserModeChange}
        onCdpUrlChange={onCdpUrlChange}
      />
      <form className="composer" onSubmit={handleSubmit}>
        <textarea
          aria-label="任务内容"
          placeholder="描述你想在当前网页完成的任务"
          rows={3}
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
          <IconButton label="添加附件" onClick={() => undefined}>
            <Paperclip size={18} />
          </IconButton>
          {running ? (
            <button
              className="stop-button"
              type="button"
              aria-label="停止任务"
              onClick={onStop}>
              停止
            </button>
          ) : (
            <button className="send-button" type="submit" aria-label="发送任务" disabled={!value.trim()}>
              <ArrowUp size={18} weight="bold" />
            </button>
          )}
        </div>
      </form>
    </div>
  );
}

function ChatView({
  activeSession,
  messages,
  running,
  runningLabel,
  draft,
  browserMode,
  cdpUrl,
  onBrowserModeChange,
  onCdpUrlChange,
  onDraftChange,
  onSubmit,
  onStop,
}: {
  activeSession: Session | null;
  messages: Message[];
  running: boolean;
  runningLabel: string;
  draft: string;
  browserMode: BrowserMode;
  cdpUrl: string;
  onBrowserModeChange: (mode: BrowserMode) => void;
  onCdpUrlChange: (value: string) => void;
  onDraftChange: (value: string) => void;
  onSubmit: () => void;
  onStop: () => void;
}) {
  return (
    <main className="view chat-view">
      {activeSession && (
        <div className="conversation-title">
          <strong>{activeSession.title}</strong>
        </div>
      )}
      <div className="chat-scroll">
        {messages.length || running ? (
          <MessageThread messages={messages} running={running} runningLabel={runningLabel} />
        ) : (
          <EmptyChat />
        )}
      </div>
      <Composer
        value={draft}
        running={running}
        browserMode={browserMode}
        cdpUrl={cdpUrl}
        onBrowserModeChange={onBrowserModeChange}
        onCdpUrlChange={onCdpUrlChange}
        onChange={onDraftChange}
        onSubmit={onSubmit}
        onStop={onStop}
      />
    </main>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label>
        <span>{label}</span>
        {children}
      </label>
      {hint && <small>{hint}</small>}
    </div>
  );
}

function SettingsView({
  modelConfig,
  backendConfig,
  storageStatus,
  onModelConfigChange,
  onBackendConfigChange,
  onSave,
}: {
  modelConfig: ModelConfig;
  backendConfig: BackendConfig;
  storageStatus: StorageStatus;
  onModelConfigChange: (key: keyof ModelConfig, value: string) => void;
  onBackendConfigChange: (key: keyof BackendConfig, value: string) => void;
  onSave: () => void;
}) {
  const [apiKeyVisible, setApiKeyVisible] = useState(false);

  const statusText = {
    loading: '正在读取本地配置',
    idle: '配置保存在当前 Chrome 配置文件中',
    saving: '正在保存',
    saved: '已保存到 Chrome 本地存储',
    error: '保存失败，请重试',
  }[storageStatus];

  return (
    <main className="view settings-view">
      <div className="page-heading">
        <h1>设置</h1>
        <p>使用 OpenAI API 规范连接模型服务。</p>
      </div>
      <section className="model-form" aria-labelledby="model-settings-title">
        <div className="form-heading">
          <div>
            <h2 id="model-settings-title">模型配置</h2>
            <p>请求格式固定为 OpenAI 标准。</p>
          </div>
          <span>OpenAI API</span>
        </div>
        <Field label="API 地址" hint="填写包含 /v1 的完整接口地址">
          <input
            aria-label="API 地址"
            inputMode="url"
            value={modelConfig.apiUrl}
            disabled={storageStatus === 'loading'}
            onChange={(event) => onModelConfigChange('apiUrl', event.target.value)}
          />
        </Field>
        <Field label="API Key">
          <div className="input-with-action">
            <input
              aria-label="API Key"
              type={apiKeyVisible ? 'text' : 'password'}
              autoComplete="off"
              value={modelConfig.apiKey}
              disabled={storageStatus === 'loading'}
              onChange={(event) => onModelConfigChange('apiKey', event.target.value)}
            />
            <button
              type="button"
              aria-label={apiKeyVisible ? '隐藏 API Key' : '显示 API Key'}
              onClick={() => setApiKeyVisible((visible) => !visible)}>
              {apiKeyVisible ? <EyeSlash size={18} /> : <Eye size={18} />}
            </button>
          </div>
        </Field>
        <Field label="模型" hint="填写 OpenAI 模型名称">
          <input
            aria-label="模型"
            value={modelConfig.model}
            disabled={storageStatus === 'loading'}
            onChange={(event) => onModelConfigChange('model', event.target.value)}
          />
        </Field>
        <Field label="后端地址" hint="本地服务默认是 http://127.0.0.1:8000">
          <input
            aria-label="后端地址"
            inputMode="url"
            value={backendConfig.backendUrl}
            disabled={storageStatus === 'loading'}
            onChange={(event) => onBackendConfigChange('backendUrl', event.target.value)}
          />
        </Field>
        <Field label="当前浏览器连接地址" hint="只有选择当前浏览器时需要，例如 9222">
          <input
            aria-label="设置中的当前浏览器连接地址"
            value={backendConfig.cdpUrl}
            disabled={storageStatus === 'loading'}
            onChange={(event) => onBackendConfigChange('cdpUrl', event.target.value)}
          />
        </Field>
      </section>
      <div className="settings-footer">
        <span className={storageStatus === 'error' ? 'is-error' : ''} aria-live="polite">
          {statusText}
        </span>
        <button
          type="button"
          disabled={storageStatus === 'loading' || storageStatus === 'saving'}
          onClick={onSave}>
          {storageStatus === 'saved' && <Check size={17} weight="bold" />}
          {storageStatus === 'saving' ? '保存中' : storageStatus === 'saved' ? '已保存' : '保存配置'}
        </button>
      </div>
    </main>
  );
}

export default function App() {
  const [activeView, setActiveView] = useState<View>('chat');
  const [activeSession, setActiveSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState('');
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const [runningLabel, setRunningLabel] = useState('正在准备浏览器...');
  const [browserMode, setBrowserMode] = useState<BrowserMode>('isolated');
  const [modelConfig, setModelConfig] = useState<ModelConfig>(DEFAULT_MODEL_CONFIG);
  const [backendConfig, setBackendConfig] = useState<BackendConfig>(DEFAULT_BACKEND_CONFIG);
  const [storageStatus, setStorageStatus] = useState<StorageStatus>('loading');
  const [conversationId, setConversationId] = useState(() => createId('conversation'));
  const [browserSessionId, setBrowserSessionId] = useState(() => createId('browser-agent'));
  const abortController = useRef<AbortController | null>(null);
  const stopRequested = useRef(false);

  useEffect(() => {
    let mounted = true;
    const restoreConfig = async () => {
      const storage = getLocalStorageArea();
      if (!storage) {
        if (mounted) setStorageStatus('idle');
        return;
      }

      try {
        const storedModel = await storage.get(MODEL_CONFIG_STORAGE_KEY);
        const storedBackend = await storage.get(BACKEND_CONFIG_STORAGE_KEY);
        if (mounted && isModelConfig(storedModel[MODEL_CONFIG_STORAGE_KEY])) {
          setModelConfig(storedModel[MODEL_CONFIG_STORAGE_KEY]);
        }
        if (mounted && isBackendConfig(storedBackend[BACKEND_CONFIG_STORAGE_KEY])) {
          setBackendConfig(storedBackend[BACKEND_CONFIG_STORAGE_KEY]);
        }
        if (mounted) setStorageStatus('idle');
      } catch {
        if (mounted) setStorageStatus('error');
      }
    };

    void restoreConfig();
    return () => {
      mounted = false;
      abortController.current?.abort();
    };
  }, []);

  const updateModelConfig = (key: keyof ModelConfig, value: string) => {
    setModelConfig((current) => ({ ...current, [key]: value }));
    if (storageStatus === 'saved' || storageStatus === 'error') setStorageStatus('idle');
  };

  const updateBackendConfig = (key: keyof BackendConfig, value: string) => {
    setBackendConfig((current) => ({ ...current, [key]: value }));
    if (storageStatus === 'saved' || storageStatus === 'error') setStorageStatus('idle');
  };

  const saveConfig = async () => {
    const storage = getLocalStorageArea();
    if (!storage) {
      setStorageStatus('error');
      return;
    }

    setStorageStatus('saving');
    try {
      await storage.set({ [MODEL_CONFIG_STORAGE_KEY]: modelConfig });
      await storage.set({ [BACKEND_CONFIG_STORAGE_KEY]: backendConfig });
      setStorageStatus('saved');
    } catch {
      setStorageStatus('error');
    }
  };

  const resetBrowserSession = () => {
    setBrowserSessionId(createId('browser-agent'));
  };

  const startNewChat = () => {
    stopRequested.current = true;
    abortController.current?.abort();
    setActiveView('chat');
    setActiveSession(null);
    setMessages([]);
    setDraft('');
    setRunning(false);
    setRunningLabel('正在准备浏览器...');
    setConversationId(createId('conversation'));
    resetBrowserSession();
    setSessionsOpen(false);
  };

  const selectSession = (session: Session) => {
    stopRequested.current = true;
    abortController.current?.abort();
    setActiveSession(session);
    setMessages(session.messages);
    setRunning(false);
    setConversationId(session.id);
    resetBrowserSession();
    setActiveView('chat');
    setSessionsOpen(false);
  };

  const submitTask = () => {
    const content = draft.trim();
    if (!content || running) return;

    const userMessage: Message = {
      id: `user-${Date.now()}`,
      role: 'user',
      content,
    };

    setMessages((current) => [...current, userMessage]);
    setDraft('');
    setRunning(true);
    stopRequested.current = false;
    const controller = new AbortController();
    abortController.current = controller;
    void (async () => {
      try {
        setRunningLabel('正在连接浏览器...');
        const session = await requestBackend<BrowserSessionResult>(
          backendConfig.backendUrl,
          '/browser/session/start',
          {
            method: 'POST',
            body: JSON.stringify({
              browser_session_id: browserSessionId,
              mode: browserMode,
              ...(browserMode === 'existing' ? { cdp_url: backendConfig.cdpUrl.trim() } : {}),
            }),
            signal: controller.signal,
          },
        );
        if (controller.signal.aborted) throw new DOMException('Aborted', 'AbortError');
        if (!session.ready) throw new Error('浏览器会话没有准备好，请重试。');
        setRunningLabel('Agent 正在操作当前页面...');

        const result = await requestBackend<AgentResult>(
          backendConfig.backendUrl,
          '/agent/run',
          {
            method: 'POST',
            body: JSON.stringify({
              message: content,
              conversation_id: conversationId,
              browser_session_id: browserSessionId,
              llm_config: {
                api_url: modelConfig.apiUrl,
                api_key: modelConfig.apiKey,
                model: modelConfig.model,
              },
            }),
            signal: controller.signal,
          },
        );
        if (controller.signal.aborted) throw new DOMException('Aborted', 'AbortError');
        const answer = result.answer?.trim() || '任务已完成。';
        const assistantMessage: Message = {
          id: `assistant-${Date.now()}`,
          role: 'assistant',
          content: answer,
        };
        setMessages((current) => [...current, assistantMessage]);
        setActiveSession((current) => ({
          id: conversationId,
          title: current?.title || content.slice(0, 32),
          preview: answer,
          updatedAt: '刚刚',
          messages: [...(current?.messages || [userMessage]), assistantMessage],
        }));
      } catch (error) {
        if (isAbortError(error) && stopRequested.current) return;
        const message = isAbortError(error)
          ? '任务已停止'
          : `执行失败：${error instanceof Error ? error.message : '未知错误'}`;
        setMessages((current) => [
          ...current,
          {
            id: `assistant-${Date.now()}`,
            role: 'assistant',
            content: message,
          },
        ]);
      } finally {
        if (abortController.current === controller) abortController.current = null;
        setRunning(false);
        setRunningLabel('正在准备浏览器...');
      }
    })();
  };

  const stopTask = () => {
    if (!running) return;
    stopRequested.current = true;
    abortController.current?.abort();
    setMessages((current) => [
      ...current,
      {
        id: `assistant-${Date.now()}`,
        role: 'assistant',
        content: '任务已停止',
      },
    ]);
    setRunning(false);
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
            messages={messages}
            running={running}
            runningLabel={runningLabel}
            draft={draft}
            browserMode={browserMode}
            cdpUrl={backendConfig.cdpUrl}
            onBrowserModeChange={setBrowserMode}
            onCdpUrlChange={(value) => updateBackendConfig('cdpUrl', value)}
            onDraftChange={setDraft}
            onSubmit={submitTask}
            onStop={stopTask}
          />
        ) : (
          <SettingsView
            modelConfig={modelConfig}
            backendConfig={backendConfig}
            storageStatus={storageStatus}
            onModelConfigChange={updateModelConfig}
            onBackendConfigChange={updateBackendConfig}
            onSave={() => void saveConfig()}
          />
        )}
      </div>
      <SessionDrawer
        open={sessionsOpen}
        activeSessionId={activeSession?.id ?? null}
        onClose={() => setSessionsOpen(false)}
        onSelect={selectSession}
        onNewChat={startNewChat}
      />
    </div>
  );
}
