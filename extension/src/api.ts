/**
 * Typed API client for the Browser Agent backend with SSE streaming support.
 */

const API_BASE = 'http://localhost:8000';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface BrowserSession {
  browser_session_id: string;
  mode: 'isolated' | 'existing';
  ready: boolean;
  url: string | null;
}

export interface AgentRunRequest {
  message: string;
  conversation_id: string;
  browser_session_id: string;
}

export interface AgentResult {
  success: boolean;
  answer: string;
  token_usage?: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
}

export type AgentEvent =
  | { type: 'step'; step: number; action: 'observe' | 'think' }
  | { type: 'action'; name: string; arguments: Record<string, unknown> }
  | { type: 'progress'; memory: string; next_goal: string }
  | {
      type: 'done';
      status: 'completed' | 'blocked' | 'cancelled' | 'failed';
      answer: string;
      success: boolean;
    }
  | { type: 'error'; detail: string };

export interface DiscoverResult {
  cdp_url: string | null;
}

// ---------------------------------------------------------------------------
// Browser Session API
// ---------------------------------------------------------------------------

export async function discoverBrowser(): Promise<DiscoverResult> {
  const response = await fetch(`${API_BASE}/browser/discover`, {
    method: 'GET',
  });
  if (!response.ok) {
    throw new Error(`Discover failed: ${response.status} ${response.statusText}`);
  }
  return response.json();
}

export async function startBrowserSession(
  browser_session_id: string,
  mode: 'isolated' | 'existing',
  cdp_url?: string
): Promise<BrowserSession> {
  const payload: { browser_session_id: string; mode: string; cdp_url?: string } = {
    browser_session_id,
    mode,
  };
  if (cdp_url) {
    payload.cdp_url = cdp_url;
  }

  const response = await fetch(`${API_BASE}/browser/session/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Start session failed: ${response.status} ${errorText}`);
  }

  return response.json();
}

export async function listBrowserSessions(): Promise<BrowserSession[]> {
  const response = await fetch(`${API_BASE}/browser/sessions`, {
    method: 'GET',
  });
  if (!response.ok) {
    throw new Error(`List sessions failed: ${response.status} ${response.statusText}`);
  }
  return response.json();
}

export async function closeBrowserSession(
  browser_session_id: string
): Promise<BrowserSession> {
  const response = await fetch(`${API_BASE}/browser/sessions/${browser_session_id}`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    throw new Error(`Close session failed: ${response.status} ${response.statusText}`);
  }
  return response.json();
}

// ---------------------------------------------------------------------------
// Agent API — Streaming (SSE)
// ---------------------------------------------------------------------------

export interface StreamCallbacks {
  onEvent: (event: AgentEvent) => void;
  onDone?: (event: AgentEvent & { type: 'done' }) => void;
  onError?: (error: Error) => void;
}

export class AgentStreamController {
  private abortController: AbortController;
  private reader: ReadableStreamDefaultReader<Uint8Array> | null = null;

  constructor(abortController: AbortController) {
    this.abortController = abortController;
  }

  abort(): void {
    this.abortController.abort();
    if (this.reader) {
      this.reader.cancel().catch(() => {
        // Ignore reader cancel errors
      });
    }
  }

  setReader(reader: ReadableStreamDefaultReader<Uint8Array>): void {
    this.reader = reader;
  }
}

/**
 * 运行 Agent 并通过 SSE 接收实时事件流。
 * 返回 AgentStreamController 用于中途取消。
 */
export function runAgentStream(
  request: AgentRunRequest,
  callbacks: StreamCallbacks
): AgentStreamController {
  const abortController = new AbortController();
  const controller = new AgentStreamController(abortController);

  (async () => {
    try {
      const response = await fetch(`${API_BASE}/agent/run/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
        signal: abortController.signal,
      });

      if (!response.ok) {
        const errorText = await response.text().catch(() => response.statusText);
        throw new Error(`Agent stream failed: ${response.status} ${errorText}`);
      }

      if (!response.body) {
        throw new Error('Response body is null');
      }

      const reader = response.body.getReader();
      controller.setReader(reader);
      const decoder = new TextDecoder('utf-8');
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // SSE 格式: "data: <json>\n\n"
        const lines = buffer.split('\n\n');
        buffer = lines.pop() || ''; // 保留最后不完整的行

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || trimmed.startsWith(':')) {
            // 忽略空行和心跳注释
            continue;
          }

          if (trimmed.startsWith('data: ')) {
            const data = trimmed.slice(6);
            if (data === '[DONE]') {
              return;
            }

            try {
              const event = JSON.parse(data) as AgentEvent;
              callbacks.onEvent(event);

              if (event.type === 'done') {
                callbacks.onDone?.(event);
                return;
              }

              if (event.type === 'error') {
                throw new Error(event.detail);
              }
            } catch (err) {
              throw new Error(`Failed to parse SSE event: ${err}`);
            }
          }
        }
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        // 用户主动取消，不算错误
        return;
      }
      callbacks.onError?.(err as Error);
    }
  })();

  return controller;
}

// ---------------------------------------------------------------------------
// Agent API — Cancel
// ---------------------------------------------------------------------------

export async function cancelAgent(conversation_id: string): Promise<void> {
  const response = await fetch(`${API_BASE}/agent/${conversation_id}`, {
    method: 'DELETE',
  });

  if (!response.ok && response.status !== 404) {
    throw new Error(`Cancel agent failed: ${response.status} ${response.statusText}`);
  }
}

// ---------------------------------------------------------------------------
// Agent API — Non-streaming (fallback)
// ---------------------------------------------------------------------------

export async function runAgent(request: AgentRunRequest): Promise<AgentResult> {
  const response = await fetch(`${API_BASE}/agent/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`Agent run failed: ${response.status} ${errorText}`);
  }

  return response.json();
}
