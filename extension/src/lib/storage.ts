import {
  DEFAULT_MODEL_SETTINGS,
  MODEL_CONFIG_STORAGE_KEY,
  MODEL_SETTINGS_STORAGE_KEY,
  type BrowserMode,
  type ModelConfig,
  type ModelEndpoint,
  type ModelSelection,
  type ModelSettings,
  type Session,
} from './models';

function getChromeStorage() {
  if (typeof chrome === 'undefined' || !chrome.storage?.local) return null;
  return chrome.storage.local;
}

export async function readStoredValue<T>(key: string): Promise<T | undefined> {
  const storage = getChromeStorage();
  if (storage) {
    const stored = await storage.get(key);
    return stored[key] as T | undefined;
  }

  const stored = localStorage.getItem(key);
  return stored ? (JSON.parse(stored) as T) : undefined;
}

export async function writeStoredValue(key: string, value: unknown) {
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

export async function readModelSettings(): Promise<ModelSettings> {
  const settings = await readStoredValue<unknown>(MODEL_SETTINGS_STORAGE_KEY);
  if (isModelSettings(settings)) return settings;
  const legacy = await readStoredValue<unknown>(MODEL_CONFIG_STORAGE_KEY);
  return isModelConfig(legacy) ? settingsFromLegacy(legacy) : DEFAULT_MODEL_SETTINGS;
}

export function enabledModelOptions(settings: ModelSettings) {
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

export function normalizeSession(value: unknown): Session | null {
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
