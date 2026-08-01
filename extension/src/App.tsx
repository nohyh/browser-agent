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
import {
  AgentEvent,
  AgentStreamController,
  cancelAgent,
  discoverBrowser,
  runAgentStream,
  startBrowserSession,
} from './api';

type View = 'chat' | 'settings';
type MessageRole = 'user' | 'assistant' | 'system';
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

type StorageStatus = 'loading' | 'idle' | 'saving' | 'saved' | 'error';

const MODEL_CONFIG_STORAGE_KEY = 'modelConfig';
const DEFAULT_MODEL_CONFIG: ModelConfig = {
  apiUrl: 'https://api.openai.com/v1',
  apiKey: '',
  model: 'gpt-4o',
};

const BROWSER_SESSION_ID = 'extension-main';
const CONVERSATION_ID = 'default';

function isModelConfig(value: unknown): value is ModelConfig {
  if (!value || typeof value !== 'object') return false;

  const candidate = value as Partial<ModelConfig>;
  return (
    typeof candidate.apiUrl === 'string' &&
    typeof candidate.apiKey === 'string' &&
    typeof candidate.model === 'string'
  );
}

function getLocalStorageArea() {
  if (typeof chrome === 'undefined' || !chrome.storage?.local) return null;
  return chrome.storage.local;
}

// Mock sessions for history drawer (real sessions would come from backend)
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

function BrowserModeSelector({
  mode,
  onModeChange,
  discovering,
}: {
  mode: BrowserMode;
  onModeChange: (mode: BrowserMode) => void;
  discovering: boolean;
}) {
  return (
    <div className="browser-mode-selector">
      <label>
        <input
          type="radio"
          name="browser-mode"
          value="existing"
          checked={mode === 'existing'}
          disabled={discovering}
          onChange={(e) => onModeChange(e.target.value as BrowserMode)}
        />
        <span>使用当前浏览器</span>
      </label>
      <label>
        <input
          type="radio"
          name="browser-mode"
          value="isolated"
          checked={mode === 'isolated'}
          disabled={discovering}
          onChange={(e) => onModeChange(e.target.value as BrowserMode)}
        />
        <span>打开独立 Profile</span>
      </label>
    </div>
  );
}

function EmptyChat({
  mode,
  onModeChange,
  discovering,
}: {
  mode: BrowserMode;
  onModeChange: (mode: BrowserMode) => void;
  discovering: boolean;
}) {
  return (
    <div className="empty-chat">
      <h1>需要我做什么？</h1>
      <p>告诉我你想在网页中完成的任务。</p>
      <BrowserModeSelector mode={mode} onModeChange={onModeChange} discovering={discovering} />
    </div>
  );
}

function MessageThread({
  messages,
  running,
  currentGoal,
}: {
  messages: Message[];
  running: boolean;
  currentGoal: string;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, running, currentGoal]);

  return (
    <div className="message-thread" ref={scrollRef}>
      {messages.map((message) => (
        <div className={`message ${message.role}`} key={message.id}>
          {message.role === 'assistant' && (
            <span className="assistant-avatar" aria-hidden="true">
              <img src="/logo-bust.png" alt="" />
            </span>
          )}
          <div className="message-body">
            <span className="message-author">
              {message.role === 'user' ? '你' : message.role === 'system' ? '系统' : 'Browser Agent'}
            </span>
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
            <p className="running-copy">{currentGoal || '正在处理...'}</p>
          </div>
        </div>
      )}
    </div>
  );
}

function Composer({
  value,
  running,
  onChange,
  onSubmit,
  onStop,
  mode,
  onModeChange,
  discovering,
}: {
  value: string;
  running: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  mode: BrowserMode;
  onModeChange: (mode: BrowserMode) => void;
  discovering: boolean;
}) {
  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit();
  };

  return (
    <div className="composer-wrap">
      <BrowserModeSelector mode={mode} onModeChange={onModeChange} discovering={discovering} />
      <form className="composer" onSubmit={handleSubmit}>
        <textarea
          aria-label="任务内容"
          placeholder="描述你想在网页中完成的任务"
          rows={3}
          value={value}
          disabled={running || discovering}
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
            <button className="stop-button" type="button" onClick={onStop}>
              停止
            </button>
          ) : (
            <button
              className="send-button"
              type="submit"
              aria-label="发送任务"
              disabled={!value.trim() || discovering}>
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
  currentGoal,
  draft,
  onDraftChange,
  onSubmit,
  onStop,
  mode,
  onModeChange,
  discovering,
}: {
  activeSession: Session | null;
  messages: Message[];
  running: boolean;
  currentGoal: string;
  draft: string;
  onDraftChange: (value: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  mode: BrowserMode;
  onModeChange: (mode: BrowserMode) => void;
  discovering: boolean;
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
          <MessageThread messages={messages} running={running} currentGoal={currentGoal} />
        ) : (
          <EmptyChat mode={mode} onModeChange={onModeChange} discovering={discovering} />
        )}
      </div>
      <Composer
        value={draft}
        running={running}
        onChange={onDraftChange}
        onSubmit={onSubmit}
        onStop={onStop}
        mode={mode}
        onModeChange={onModeChange}
        discovering={discovering}
      />
    </main>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
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

function SettingsView() {
  const [apiKeyVisible, setApiKeyVisible] = useState(false);
  const [config, setConfig] = useState<ModelConfig>(DEFAULT_MODEL_CONFIG);
  const [storageStatus, setStorageStatus] = useState<StorageStatus>('loading');

  useEffect(() => {
    let mounted = true;

    const restoreConfig = async () => {
      const storage = getLocalStorageArea();
      if (!storage) {
        if (mounted) setStorageStatus('idle');
        return;
      }

      try {
        const stored = await storage.get(MODEL_CONFIG_STORAGE_KEY);
        if (mounted && isModelConfig(stored[MODEL_CONFIG_STORAGE_KEY])) {
          setConfig(stored[MODEL_CONFIG_STORAGE_KEY]);
        }
        if (mounted) setStorageStatus('idle');
      } catch {
        if (mounted) setStorageStatus('error');
      }
    };

    void restoreConfig();
    return () => {
      mounted = false;
    };
  }, []);

  const updateConfig = (key: keyof ModelConfig, value: string) => {
    setConfig((current) => ({ ...current, [key]: value }));
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
      await storage.set({ [MODEL_CONFIG_STORAGE_KEY]: config });
      setStorageStatus('saved');
    } catch {
      setStorageStatus('error');
    }
  };

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
            value={config.apiUrl}
            disabled={storageStatus === 'loading'}
            onChange={(event) => updateConfig('apiUrl', event.target.value)}
          />
        </Field>
        <Field label="API Key">
          <div className="input-with-action">
            <input
              aria-label="API Key"
              type={apiKeyVisible ? 'text' : 'password'}
              autoComplete="off"
              value={config.apiKey}
              disabled={storageStatus === 'loading'}
              onChange={(event) => updateConfig('apiKey', event.target.value)}
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
            value={config.model}
            disabled={storageStatus === 'loading'}
            onChange={(event) => updateConfig('model', event.target.value)}
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
          onClick={() => void saveConfig()}>
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
  const [currentGoal, setCurrentGoal] = useState('');
  const [browserMode, setBrowserMode] = useState<BrowserMode>('isolated');
  const [discovering, setDiscovering] = useState(false);
  const [sessionReady, setSessionReady] = useState(false);
  const streamController = useRef<AgentStreamController | null>(null);

  const startNewChat = () => {
    if (streamController.current) {
      streamController.current.abort();
      streamController.current = null;
    }
    setActiveView('chat');
    setActiveSession(null);
    setMessages([]);
    setDraft('');
    setRunning(false);
    setCurrentGoal('');
    setSessionsOpen(false);
    setSessionReady(false);
  };

  const selectSession = (session: Session) => {
    if (streamController.current) {
      streamController.current.abort();
      streamController.current = null;
    }
    setActiveSession(session);
    setMessages(session.messages);
    setRunning(false);
    setCurrentGoal('');
    setActiveView('chat');
    setSessionsOpen(false);
  };

  const ensureBrowserSession = async (): Promise<boolean> => {
    if (sessionReady) return true;

    try {
      let cdp_url: string | undefined;

      if (browserMode === 'existing') {
        setDiscovering(true);
        const result = await discoverBrowser();
        setDiscovering(false);

        if (!result.cdp_url) {
          setMessages((current) => [
            ...current,
            {
              id: `system-${Date.now()}`,
              role: 'system',
              content:
                '未找到开启调试端口的 Chrome 浏览器。请使用 --remote-debugging-port=9222 启动 Chrome，或切换到"独立 Profile"模式。',
            },
          ]);
          return false;
        }

        cdp_url = result.cdp_url;
      }

      await startBrowserSession(BROWSER_SESSION_ID, browserMode, cdp_url);
      setSessionReady(true);
      return true;
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: `system-${Date.now()}`,
          role: 'system',
          content: `浏览器会话启动失败: ${error instanceof Error ? error.message : String(error)}`,
        },
      ]);
      return false;
    }
  };

  const submitTask = async () => {
    const content = draft.trim();
    if (!content || running) return;

    const ready = await ensureBrowserSession();
    if (!ready) return;

    const userMessage: Message = {
      id: `user-${Date.now()}`,
      role: 'user',
      content,
    };

    setMessages((current) => [...current, userMessage]);
    setDraft('');
    setRunning(true);
    setCurrentGoal('正在启动...');

    const controller = runAgentStream(
      {
        message: content,
        conversation_id: CONVERSATION_ID,
        browser_session_id: BROWSER_SESSION_ID,
      },
      {
        onEvent: (event: AgentEvent) => {
          if (event.type === 'progress') {
            setCurrentGoal(event.next_goal || '处理中...');
          } else if (event.type === 'action') {
            setCurrentGoal(`正在执行: ${event.name}`);
          } else if (event.type === 'step') {
            const action = event.action === 'observe' ? '观察页面' : '思考决策';
            setCurrentGoal(`步骤 ${event.step + 1}: ${action}`);
          }
        },
        onDone: (event) => {
          setRunning(false);
          setCurrentGoal('');
          setMessages((current) => [
            ...current,
            {
              id: `assistant-${Date.now()}`,
              role: 'assistant',
              content: event.answer || '任务完成',
            },
          ]);
        },
        onError: (error) => {
          setRunning(false);
          setCurrentGoal('');
          setMessages((current) => [
            ...current,
            {
              id: `system-${Date.now()}`,
              role: 'system',
              content: `错误: ${error.message}`,
            },
          ]);
        },
      },
    );

    streamController.current = controller;
  };

  const stopTask = async () => {
    if (streamController.current) {
      streamController.current.abort();
      streamController.current = null;
    }

    try {
      await cancelAgent(CONVERSATION_ID);
    } catch {
      // Ignore cancel errors (404 if already finished)
    }

    setRunning(false);
    setCurrentGoal('');
    setMessages((current) => [
      ...current,
      {
        id: `system-${Date.now()}`,
        role: 'system',
        content: '任务已取消',
      },
    ]);
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
            currentGoal={currentGoal}
            draft={draft}
            onDraftChange={setDraft}
            onSubmit={() => void submitTask()}
            onStop={() => void stopTask()}
            mode={browserMode}
            onModeChange={setBrowserMode}
            discovering={discovering}
          />
        ) : (
          <SettingsView />
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
