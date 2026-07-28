import {
  ArrowUp,
  CaretRight,
  Check,
  ClockCounterClockwise,
  GearSix,
  Paperclip,
  Plus,
  X,
} from '@phosphor-icons/react';
import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import './App.css';

type View = 'chat' | 'settings';
type MessageRole = 'user' | 'assistant';

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

function MessageThread({ messages, running }: { messages: Message[]; running: boolean }) {
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
            <p className="running-copy">正在处理当前页面...</p>
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
}: {
  value: string;
  running: boolean;
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
            <button className="stop-button" type="button" onClick={onStop}>
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
  draft,
  onDraftChange,
  onSubmit,
  onStop,
}: {
  activeSession: Session | null;
  messages: Message[];
  running: boolean;
  draft: string;
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
        {messages.length || running ? <MessageThread messages={messages} running={running} /> : <EmptyChat />}
      </div>
      <Composer
        value={draft}
        running={running}
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

function SettingsView() {
  const [apiKeyVisible, setApiKeyVisible] = useState(false);
  const [tested, setTested] = useState(false);
  const [saved, setSaved] = useState(false);

  return (
    <main className="view settings-view">
      <div className="page-heading">
        <h1>设置</h1>
        <p>配置用于浏览任务的模型。</p>
      </div>
      <section className="model-form" aria-labelledby="model-settings-title">
        <div className="form-heading">
          <div>
            <h2 id="model-settings-title">模型配置</h2>
            <p>支持 OpenAI 及兼容接口。</p>
          </div>
          <span>默认</span>
        </div>
        <Field label="服务商">
          <select defaultValue="openai">
            <option value="openai">OpenAI</option>
            <option value="compatible">OpenAI Compatible</option>
            <option value="ollama">Ollama</option>
          </select>
        </Field>
        <Field label="API 地址" hint="填写完整的版本路径">
          <input aria-label="API 地址" defaultValue="https://api.openai.com/v1" />
        </Field>
        <Field label="API Key">
          <div className="input-with-action">
            <input
              aria-label="API Key"
              type={apiKeyVisible ? 'text' : 'password'}
              defaultValue="sk-proj-browser-agent"
            />
            <button type="button" onClick={() => setApiKeyVisible((visible) => !visible)}>
              {apiKeyVisible ? '隐藏' : '显示'}
            </button>
          </div>
        </Field>
        <Field label="模型">
          <input aria-label="模型" defaultValue="gpt-5" />
        </Field>
        <div className="parameter-fields">
          <Field label="Temperature">
            <input type="number" min="0" max="2" step="0.1" defaultValue="0.2" />
          </Field>
          <Field label="最大输出">
            <input type="number" min="512" step="512" defaultValue="4096" />
          </Field>
        </div>
        <button
          className={`test-connection${tested ? ' is-success' : ''}`}
          type="button"
          onClick={() => setTested(true)}>
          {tested && <Check size={17} weight="bold" />}
          {tested ? '连接正常' : '测试连接'}
        </button>
      </section>
      <div className="settings-footer">
        <span>{saved ? '配置已保存在本地' : '配置仅保存在当前扩展中'}</span>
        <button type="button" onClick={() => setSaved(true)}>
          {saved && <Check size={17} weight="bold" />}
          {saved ? '已保存' : '保存配置'}
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
  const completionTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (completionTimer.current) clearTimeout(completionTimer.current);
    },
    [],
  );

  const startNewChat = () => {
    if (completionTimer.current) clearTimeout(completionTimer.current);
    setActiveView('chat');
    setActiveSession(null);
    setMessages([]);
    setDraft('');
    setRunning(false);
    setSessionsOpen(false);
  };

  const selectSession = (session: Session) => {
    if (completionTimer.current) clearTimeout(completionTimer.current);
    setActiveSession(session);
    setMessages(session.messages);
    setRunning(false);
    setActiveView('chat');
    setSessionsOpen(false);
  };

  const submitTask = () => {
    const content = draft.trim();
    if (!content || running) return;

    setMessages((current) => [
      ...current,
      {
        id: `user-${Date.now()}`,
        role: 'user',
        content,
      },
    ]);
    setDraft('');
    setRunning(true);

    // 后端尚未接线，暂时用短反馈展示完整聊天状态。
    completionTimer.current = setTimeout(() => {
      setMessages((current) => [
        ...current,
        {
          id: `assistant-${Date.now()}`,
          role: 'assistant',
          content: '已收到任务。接入后端后，这里会显示真实的执行结果。',
        },
      ]);
      setRunning(false);
    }, 900);
  };

  const stopTask = () => {
    if (completionTimer.current) clearTimeout(completionTimer.current);
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
            draft={draft}
            onDraftChange={setDraft}
            onSubmit={submitTask}
            onStop={stopTask}
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
