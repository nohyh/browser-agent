import { useEffect, useMemo, useRef, useState } from 'react';
import { AppHeader, BrowserChoiceDialog, SessionDrawer } from './components/AppShell';
import { ChatView } from './components/ChatView';
import { SettingsView } from './components/SettingsView';
import { ApiError, friendlyError, requestJson, streamAgentTask, syncModelSettings } from './lib/api';
import { browserSessionId, ensureBrowserSession, readCurrentPage, readCurrentTabUrl } from './lib/browser';
import { sessionTitle } from './lib/format';
import {
  BACKEND_URL,
  CHAT_SESSIONS_STORAGE_KEY,
  DEFAULT_MODEL_SETTINGS,
  makeId,
  type AgentResult,
  type AgentRunPayload,
  type BrowserDialogState,
  type BrowserMode,
  type FailedTask,
  type Message,
  type ModelSelection,
  type ModelSettings,
  type PageSuggestionsResult,
  type RunPhase,
  type Session,
  type TraceEvent,
  type View,
} from './lib/models';
import { enabledModelOptions, normalizeSession, readModelSettings, readStoredValue, writeStoredValue } from './lib/storage';
import './App.css';

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
  const historySessions = useMemo(
    () => sessions.filter((session) => session.messages.length > 0),
    [sessions],
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
          setSessions(
            stored
              .map(normalizeSession)
              .filter(
                (session): session is Session =>
                  session !== null && session.messages.length > 0,
              ),
          );
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
    void writeStoredValue(CHAT_SESSIONS_STORAGE_KEY, historySessions).catch(() => undefined);
  }, [historySessions, storageReady]);

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
        sessions={historySessions}
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
