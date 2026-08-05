import {
  BACKEND_URL,
  type AgentResult,
  type AgentRunPayload,
  type ModelSettings,
  type TraceEvent,
} from './models';

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
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

export async function streamAgentTask(
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

export async function syncModelSettings(settings: ModelSettings, signal?: AbortSignal) {
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

export function friendlyError(error: unknown) {
  if (
    error instanceof ApiError &&
    (error.status < 500 || error.message.startsWith('无法连接当前浏览器'))
  ) {
    return error.message;
  }
  return '无法连接本地服务，请确认后端已经启动。';
}
