import { ArrowClockwise, ArrowUp, CaretRight } from '@phosphor-icons/react';
import { type FormEvent, type RefObject, useEffect, useRef } from 'react';
import type { Message, ModelSelection, RunPhase, Session, TraceEvent } from '../lib/models';
import { enabledModelOptions } from '../lib/storage';

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
        {completed ? (
          <span className="trajectory-summary-content">
            操作了 {duration}s
            <CaretRight size={11} weight="bold" aria-hidden="true" />
          </span>
        ) : (
          <CaretRight size={11} weight="bold" aria-hidden="true" />
        )}
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

export function ChatView({
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
