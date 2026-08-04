import {
  Browser,
  Browsers,
  ClockCounterClockwise,
  GearSix,
  Plus,
  Trash,
  X,
} from '@phosphor-icons/react';
import { type ReactNode, useEffect, useMemo, useState } from 'react';
import type { BrowserMode, Session } from '../lib/models';
import { formatSessionTime } from '../lib/format';

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

export function AppHeader({
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

export function SessionDrawer({
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

export function BrowserChoiceDialog({
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

