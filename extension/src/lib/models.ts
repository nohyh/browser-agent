export type View = 'chat' | 'settings';
export type MessageRole = 'user' | 'assistant';
export type RunPhase = 'idle' | 'starting' | 'running';
export type StorageStatus = 'loading' | 'idle' | 'saving' | 'saved' | 'error';
export type BrowserMode = 'current' | 'isolated';

export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  createdAt?: number;
  trace?: TraceEvent[];
}

export interface TraceEvent {
  kind: string;
  status: 'running' | 'completed' | 'failed';
  title: string;
  detail?: string | null;
  timestamp?: string | null;
  step_id?: string | null;
}

export interface Session {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: Message[];
  llmSelection?: ModelSelection;
  browserMode?: BrowserMode;
  browserTargetUrl?: string;
}

export interface ModelConfig {
  apiUrl: string;
  apiKey: string;
  model: string;
}

export interface ModelSelection {
  endpointId: string;
  model: string;
}

export interface ModelEndpoint {
  id: string;
  name: string;
  apiUrl: string;
  apiKey: string;
  availableModels: string[];
  enabledModels: string[];
}

export interface ModelSettings {
  endpoints: ModelEndpoint[];
  defaultSelection: ModelSelection | null;
}

export interface LLMModelsResult {
  models: string[];
}

export interface AgentResult {
  success: boolean;
  answer: string;
}

export interface AgentRunPayload {
  message: string;
  conversation_id: string;
  browser_session_id: string;
  run_id: string;
  llm_endpoint_id?: string;
  llm_model?: string;
}

export interface PageContext {
  url: string;
  title: string;
  content: string;
}

export interface PageSuggestionsResult {
  suggestions: string[];
}

export interface BrowserSessionResult {
  browser_session_id: string;
  mode: 'current' | 'isolated' | 'existing';
  ready: boolean;
  url: string | null;
}

export interface FailedTask {
  conversationId: string;
  message: string;
  llmSelection: ModelSelection | null;
  browserMode: BrowserMode;
  browserTargetUrl?: string;
}

export interface PendingTask {
  message: string;
  llmSelection: ModelSelection | null;
}

export interface BrowserDialogState {
  sessionId: string | null;
  pendingTask?: PendingTask;
}

export const MODEL_CONFIG_STORAGE_KEY = 'modelConfig';
export const MODEL_SETTINGS_STORAGE_KEY = 'modelSettings';
export const CHAT_SESSIONS_STORAGE_KEY = 'chatSessions';
export const BACKEND_URL = (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
export const DEFAULT_MODEL_CONFIG: ModelConfig = {
  apiUrl: 'https://api.openai.com/v1',
  apiKey: '',
  model: 'gpt-5',
};
export const DEFAULT_MODEL_SETTINGS: ModelSettings = {
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

export function makeId(prefix: string) {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

