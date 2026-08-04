import { ArrowClockwise, Check, Eye, EyeSlash, Plus, Trash } from '@phosphor-icons/react';
import { type FormEvent, type ReactNode, useEffect, useState } from 'react';
import { friendlyError, requestJson, syncModelSettings } from '../lib/api';
import {
  MODEL_SETTINGS_STORAGE_KEY,
  makeId,
  type LLMModelsResult,
  type ModelEndpoint,
  type ModelSettings,
  type StorageStatus,
} from '../lib/models';
import { writeStoredValue } from '../lib/storage';

function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className={`field${error ? ' has-error' : ''}`}>
      <label>
        <span>{label}</span>
        {children}
      </label>
      {error ? <small className="field-error">{error}</small> : hint ? <small>{hint}</small> : null}
    </div>
  );
}


export function SettingsView({
  settings,
  onSaved,
}: {
  settings: ModelSettings;
  onSaved: (settings: ModelSettings) => void;
}) {
  const [endpoints, setEndpoints] = useState<ModelEndpoint[]>(settings.endpoints);
  const [storageStatus, setStorageStatus] = useState<StorageStatus>('idle');
  const [visibleKeys, setVisibleKeys] = useState<Record<string, boolean>>({});
  const [loadingModels, setLoadingModels] = useState<Record<string, boolean>>({});
  const [endpointErrors, setEndpointErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    setEndpoints(settings.endpoints);
  }, [settings]);

  const updateEndpoint = (endpointId: string, update: Partial<ModelEndpoint>) => {
    setEndpoints((current) =>
      current.map((endpoint) => endpoint.id === endpointId ? { ...endpoint, ...update } : endpoint),
    );
    if (storageStatus === 'saved' || storageStatus === 'error') setStorageStatus('idle');
  };

  const addEndpoint = () => {
    setEndpoints((current) => [
      ...current,
      {
        id: makeId('endpoint'),
        name: '',
        apiUrl: '',
        apiKey: '',
        availableModels: [],
        enabledModels: [],
      },
    ]);
  };

  const discoverModels = async (endpoint: ModelEndpoint) => {
    if (!endpoint.apiUrl.trim() || !endpoint.apiKey.trim()) {
      setEndpointErrors((current) => ({ ...current, [endpoint.id]: '请先填写 API 地址和 API Key' }));
      return;
    }
    setLoadingModels((current) => ({ ...current, [endpoint.id]: true }));
    setEndpointErrors((current) => ({ ...current, [endpoint.id]: '' }));
    try {
      const result = await requestJson<LLMModelsResult>('/llm/models', {
        method: 'POST',
        body: JSON.stringify({
          api_url: endpoint.apiUrl.trim().replace(/\/$/, ''),
          api_key: endpoint.apiKey.trim(),
        }),
      });
      // 自动获取后默认全选，用户只需取消不希望在对话中出现的模型。
      updateEndpoint(endpoint.id, {
        availableModels: result.models,
        enabledModels: result.models,
      });
    } catch (error) {
      setEndpointErrors((current) => ({ ...current, [endpoint.id]: friendlyError(error) }));
    } finally {
      setLoadingModels((current) => ({ ...current, [endpoint.id]: false }));
    }
  };

  const toggleModel = (endpoint: ModelEndpoint, model: string) => {
    updateEndpoint(endpoint.id, {
      enabledModels: endpoint.enabledModels.includes(model)
        ? endpoint.enabledModels.filter((candidate) => candidate !== model)
        : [...endpoint.enabledModels, model],
    });
  };

  const formValid = endpoints.length > 0 && endpoints.every((endpoint) => {
    try {
      const url = new URL(endpoint.apiUrl);
      return (
        Boolean(endpoint.name.trim() && endpoint.apiKey.trim() && endpoint.enabledModels.length) &&
        ['http:', 'https:'].includes(url.protocol)
      );
    } catch {
      return false;
    }
  });

  const saveSettings = async (event: FormEvent) => {
    event.preventDefault();
    if (!formValid) {
      setStorageStatus('error');
      return;
    }
    setStorageStatus('saving');
    const normalizedEndpoints = endpoints.map((endpoint) => ({
      ...endpoint,
      name: endpoint.name.trim(),
      apiUrl: endpoint.apiUrl.trim().replace(/\/$/, ''),
      apiKey: endpoint.apiKey.trim(),
    }));
    const availableSelections = normalizedEndpoints.flatMap((endpoint) =>
      endpoint.enabledModels.map((model) => ({ endpointId: endpoint.id, model })),
    );
    const currentDefault = settings.defaultSelection;
    const defaultSelection =
      currentDefault &&
      availableSelections.some(
        (selection) =>
          selection.endpointId === currentDefault.endpointId && selection.model === currentDefault.model,
      )
        ? currentDefault
        : availableSelections[0] || null;
    const nextSettings = { endpoints: normalizedEndpoints, defaultSelection };
    try {
      await syncModelSettings(nextSettings);
      await writeStoredValue(MODEL_SETTINGS_STORAGE_KEY, nextSettings);
      onSaved(nextSettings);
      setStorageStatus('saved');
    } catch {
      setStorageStatus('error');
    }
  };

  const statusText = {
    loading: '正在读取本地配置',
    idle: '保存后会同步到本地后端',
    saving: '正在保存',
    saved: '已保存并应用到本地后端',
    error: formValid ? '保存失败，请重试' : '请完整配置每个调用方并至少勾选一个模型',
  }[storageStatus];

  return (
    <main className="view settings-view">
      <div className="page-heading">
        <h1>模型配置</h1>
        <p>一个调用方可启用多个模型，并在每个对话中随时切换。</p>
      </div>
      <form className="model-form" aria-label="模型配置" onSubmit={saveSettings}>
        <div className="form-heading">
          <h2>调用方</h2>
          <button className="add-endpoint-button" type="button" onClick={addEndpoint}>
            <Plus size={14} />
            添加调用方
          </button>
        </div>
        <div className="endpoint-list">
          {endpoints.map((endpoint, index) => (
            <section className="endpoint-card" key={endpoint.id}>
              <div className="endpoint-card-heading">
                <strong>{endpoint.name.trim() || `调用方 ${index + 1}`}</strong>
                {endpoints.length > 1 && (
                  <button
                    type="button"
                    aria-label={`删除调用方 ${endpoint.name || index + 1}`}
                    onClick={() => setEndpoints((current) => current.filter((item) => item.id !== endpoint.id))}>
                    <Trash size={15} />
                  </button>
                )}
              </div>
              <Field label="调用方名称">
                <input
                  aria-label="调用方名称"
                  value={endpoint.name}
                  placeholder="例如 DeepSeek"
                  onChange={(event) => updateEndpoint(endpoint.id, { name: event.target.value })}
                />
              </Field>
              <Field label="API 地址" hint="填写兼容 OpenAI API 的 /v1 地址">
                <input
                  aria-label="API 地址"
                  inputMode="url"
                  value={endpoint.apiUrl}
                  placeholder="https://api.example.com/v1"
                  onChange={(event) => updateEndpoint(endpoint.id, { apiUrl: event.target.value })}
                />
              </Field>
              <Field label="API Key" hint="密钥只保存在当前 Chrome，并发送给本地后端">
                <div className="input-with-action">
                  <input
                    aria-label="API Key"
                    type={visibleKeys[endpoint.id] ? 'text' : 'password'}
                    autoComplete="off"
                    value={endpoint.apiKey}
                    onChange={(event) => updateEndpoint(endpoint.id, { apiKey: event.target.value })}
                  />
                  <button
                    type="button"
                    aria-label={visibleKeys[endpoint.id] ? '隐藏 API Key' : '显示 API Key'}
                    onClick={() =>
                      setVisibleKeys((current) => ({ ...current, [endpoint.id]: !current[endpoint.id] }))
                    }>
                    {visibleKeys[endpoint.id] ? <EyeSlash size={17} /> : <Eye size={17} />}
                  </button>
                </div>
              </Field>
              <div className="model-discovery">
                <button
                  type="button"
                  disabled={loadingModels[endpoint.id]}
                  onClick={() => void discoverModels(endpoint)}>
                  <ArrowClockwise size={15} />
                  {loadingModels[endpoint.id] ? '获取中' : '获取模型'}
                </button>
                <span>{endpoint.enabledModels.length ? `已选 ${endpoint.enabledModels.length} 个` : '尚未选择模型'}</span>
              </div>
              {endpointErrors[endpoint.id] && (
                <p className="endpoint-error" role="alert">{endpointErrors[endpoint.id]}</p>
              )}
              {endpoint.availableModels.length > 0 && (
                <div className="model-checklist" aria-label={`${endpoint.name || '调用方'}模型`}>
                  {endpoint.availableModels.map((model) => (
                    <label key={model}>
                      <input
                        type="checkbox"
                        checked={endpoint.enabledModels.includes(model)}
                        onChange={() => toggleModel(endpoint, model)}
                      />
                      <span>{model}</span>
                    </label>
                  ))}
                </div>
              )}
            </section>
          ))}
        </div>
        <div className="settings-footer">
          <span className={storageStatus === 'error' ? 'is-error' : ''} aria-live="polite">
            {statusText}
          </span>
          <button type="submit" aria-label="保存配置" disabled={!formValid || storageStatus === 'saving'}>
            {storageStatus === 'saved' && <Check size={16} weight="bold" />}
            {storageStatus === 'saving' ? '保存中' : storageStatus === 'saved' ? '已保存' : '保存配置'}
          </button>
        </div>
      </form>
    </main>
  );
}

