// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import manifest from '../public/manifest.json';
import App from './App';

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

type StoredValues = Record<string, unknown>;

function mockChromeStorage(initialValues: StoredValues = {}) {
  const values = { ...initialValues };
  const get = vi.fn(async (key: string) => (key in values ? { [key]: values[key] } : {}));
  const set = vi.fn(async (nextValues: StoredValues) => {
    Object.assign(values, nextValues);
  });

  vi.stubGlobal('chrome', {
    storage: {
      local: { get, set },
    },
  });

  return { get, set, values };
}

function mockCurrentPage(initialValues: StoredValues = {}) {
  const storage = mockChromeStorage(initialValues);
  const query = vi.fn(async () => [
    { id: 23, url: 'https://example.com/guide', title: '使用指南' },
  ]);
  const executeScript = vi.fn(async () => [
    {
      result: {
        url: 'https://example.com/guide',
        title: '使用指南',
        content: '这是一篇介绍浏览器自动化工作流的使用指南。',
      },
    },
  ]);

  vi.stubGlobal('chrome', {
    storage: {
      local: { get: storage.get, set: storage.set },
    },
    tabs: { query },
    scripting: { executeScript },
  });

  return { ...storage, query, executeScript };
}

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  );
}

function mockHealthyBackend(answer = '任务已经完成。') {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/health')) return jsonResponse({ status: 'ok' });
    if (url.endsWith('/llm/config')) {
      return jsonResponse({
        configured: true,
        api_url: 'https://gateway.example.com/v1',
        model: 'gpt-5-mini',
      });
    }
    if (url.endsWith('/llm/configs')) {
      return jsonResponse({ configured: true, endpoints: [] });
    }
    if (url.endsWith('/llm/models')) {
      return jsonResponse({ models: ['gpt-5.1'] });
    }
    if (url.endsWith('/page/suggestions')) {
      return jsonResponse({
        suggestions: ['总结当前页面', '提取关键步骤', '整理成操作清单'],
      });
    }
    if (url.includes('/browser/sessions/') && !init?.method) {
      return jsonResponse({ detail: 'Browser session not found' }, 404);
    }
    if (url.endsWith('/browser/session/start')) {
      const body = JSON.parse(String(init?.body));
      return jsonResponse({
        browser_session_id: body.browser_session_id,
        mode: body.mode,
        ready: true,
        url: body.expected_url || 'about:blank',
      });
    }
    if (url.endsWith('/agent/run')) {
      return jsonResponse({ success: true, answer });
    }
    return jsonResponse({ detail: 'Not found' }, 404);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

async function createConversation(
  user: ReturnType<typeof userEvent.setup>,
  mode: 'current' | 'isolated' = 'isolated',
) {
  await user.click(screen.getByRole('button', { name: '新建会话' }));
  const dialog = screen.getByRole('dialog', { name: '选择浏览器' });
  await user.click(
    within(dialog).getByRole('button', {
      name: mode === 'current' ? '使用当前浏览器' : '使用独立浏览器',
    }),
  );
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '选择浏览器' })).not.toBeInTheDocument());
}

describe('Browser Agent 侧边栏', () => {
  it('把领域模型、服务和页面组件拆到独立模块', async () => {
    const [{ ApiError }, models, storage, chat, settings] = await Promise.all([
      import('./lib/api'),
      import('./lib/models'),
      import('./lib/storage'),
      import('./components/ChatView'),
      import('./components/SettingsView'),
    ]);

    expect(ApiError).toBeTypeOf('function');
    expect(models.DEFAULT_MODEL_SETTINGS).toBeDefined();
    expect(storage.readModelSettings).toBeTypeOf('function');
    expect(chat.ChatView).toBeTypeOf('function');
    expect(settings.SettingsView).toBeTypeOf('function');
  });

  it('打开后分析当前页面并把点击的建议填入聊天框', async () => {
    const user = userEvent.setup();
    const chromeMock = mockCurrentPage({
      modelSettings: {
        endpoints: [{
          id: 'default',
          name: 'OpenAI',
          apiUrl: 'https://gateway.example.com/v1',
          apiKey: 'saved-key',
          availableModels: ['gpt-5-mini'],
          enabledModels: ['gpt-5-mini'],
        }],
        defaultSelection: { endpointId: 'default', model: 'gpt-5-mini' },
      },
    });
    const fetchMock = mockHealthyBackend();

    render(<App />);

    const suggestion = await screen.findByRole('button', { name: '总结当前页面' });
    expect(chromeMock.query).toHaveBeenCalledOnce();
    expect(chromeMock.executeScript).toHaveBeenCalledWith(
      expect.objectContaining({ target: { tabId: 23 } }),
    );
    const suggestionCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith('/page/suggestions'),
    );
    expect(JSON.parse(String(suggestionCall?.[1]?.body))).toEqual({
      url: 'https://example.com/guide',
      title: '使用指南',
      content: '这是一篇介绍浏览器自动化工作流的使用指南。',
      locale: 'zh-CN',
      limit: 3,
    });
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/llm/configs'))).toBe(true);

    await user.click(suggestion);

    expect(screen.getByLabelText('任务内容')).toHaveValue('总结当前页面');
    expect(screen.getByLabelText('任务内容')).toHaveFocus();
    expect(screen.queryByRole('dialog', { name: '选择浏览器' })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/agent/run'))).toBe(false);
    expect(manifest.permissions).toEqual(expect.arrayContaining(['activeTab', 'scripting', 'tabs']));
  });

  it('首页允许先输入任务，发送后再选择浏览器并继续执行', async () => {
    const user = userEvent.setup();
    mockChromeStorage();
    mockHealthyBackend();
    render(<App />);

    expect(screen.getByText('Browser Agent')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Browser Agent' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '今天想完成什么？' })).toBeInTheDocument();
    const textarea = screen.getByPlaceholderText('描述你想在浏览器中完成的任务');
    expect(textarea).toBeEnabled();
    expect(screen.getByRole('button', { name: '发送任务' })).toBeDisabled();
    expect(screen.queryByText('快速开始')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '添加附件' })).not.toBeInTheDocument();

    await user.type(textarea, '整理首页信息');
    await user.click(screen.getByRole('button', { name: '发送任务' }));
    const dialog = screen.getByRole('dialog', { name: '选择浏览器' });
    expect(textarea).toHaveValue('整理首页信息');
    await user.click(within(dialog).getByRole('button', { name: '使用独立浏览器' }));

    expect(await screen.findByText('任务已经完成。')).toBeInTheDocument();
    expect(textarea).toHaveValue('');
  });

  it('当前标签页 URL 被 Chrome 隐藏时通过页面脚本读取并完成绑定', async () => {
    const user = userEvent.setup();
    const storage = mockChromeStorage();
    const query = vi.fn(async () => [{ id: 23, title: '隐藏 URL 的页面' }]);
    const executeScript = vi.fn(async (details: { func?: { name?: string } }) => {
      if (details.func?.name === 'extractPageContext') {
        return [{
          result: {
            url: 'https://example.com/hidden-url',
            title: '隐藏 URL 的页面',
            content: '当前页面内容',
          },
        }];
      }
      return [{ result: 'https://example.com/hidden-url' }];
    });
    vi.stubGlobal('chrome', {
      storage: { local: { get: storage.get, set: storage.set } },
      tabs: { query },
      scripting: { executeScript },
    });
    const fetchMock = mockHealthyBackend();
    render(<App />);

    await user.type(screen.getByLabelText('任务内容'), '总结这个页面');
    await user.click(screen.getByRole('button', { name: '发送任务' }));
    const dialog = screen.getByRole('dialog', { name: '选择浏览器' });
    await user.click(within(dialog).getByRole('button', { name: '使用当前浏览器' }));

    expect(await screen.findByText('任务已经完成。')).toBeInTheDocument();
    const startCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith('/browser/session/start'),
    );
    expect(JSON.parse(String(startCall?.[1]?.body))).toMatchObject({
      mode: 'current',
      expected_url: 'https://example.com/hidden-url',
    });
  });

  it('连接浏览器会话并把任务发送给后端', async () => {
    const user = userEvent.setup();
    mockChromeStorage({
      modelConfig: {
        apiUrl: 'https://gateway.example.com/v1',
        apiKey: 'saved-key',
        model: 'gpt-5-mini',
      },
    });
    const fetchMock = mockHealthyBackend('已经整理好当前页面。');
    render(<App />);

    await createConversation(user);
    await user.type(screen.getByLabelText('任务内容'), '整理当前页面');
    await user.click(screen.getByRole('button', { name: '发送任务' }));

    expect(await screen.findByText('已经整理好当前页面。')).toBeInTheDocument();
    const runCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith('/agent/run'));
    expect(runCall).toBeDefined();
    expect(JSON.parse(String(runCall?.[1]?.body))).toMatchObject({
      message: '整理当前页面',
      browser_session_id: expect.stringMatching(/^browser-agent-conversation-/),
    });
    const configCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith('/llm/configs'));
    expect(configCall?.[1]?.method).toBe('PUT');
    expect(JSON.parse(String(configCall?.[1]?.body))).toEqual({
      endpoints: [{
        id: 'default',
        name: 'OpenAI',
        api_url: 'https://gateway.example.com/v1',
        api_key: 'saved-key',
        models: ['gpt-5-mini'],
      }],
    });
    expect(fetchMock.mock.calls.indexOf(configCall!)).toBeLessThan(fetchMock.mock.calls.indexOf(runCall!));
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/browser/session/start'))).toBe(true);
  });

  it('新建会话时一次性选择当前浏览器且输入框不再显示模式选择', async () => {
    const user = userEvent.setup();
    mockCurrentPage();
    const fetchMock = mockHealthyBackend();
    render(<App />);

    expect(screen.queryByLabelText('浏览器模式')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '新建会话' }));
    const dialog = screen.getByRole('dialog', { name: '选择浏览器' });
    expect(within(dialog).getByText(/创建后不可切换/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: '使用当前浏览器' }));

    await user.type(screen.getByLabelText('任务内容'), '总结当前页面');
    await user.click(screen.getByRole('button', { name: '发送任务' }));
    await screen.findByText('任务已经完成。');

    const startCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith('/browser/session/start'),
    );
    expect(JSON.parse(String(startCall?.[1]?.body))).toMatchObject({
      mode: 'current',
      expected_url: 'https://example.com/guide',
    });
    expect(screen.getByText('当前浏览器')).toBeInTheDocument();
  });

  it('当前标签页导航后启动会话时使用最新 URL', async () => {
    const user = userEvent.setup();
    const chromeMock = mockCurrentPage();
    const fetchMock = mockHealthyBackend();
    render(<App />);

    await createConversation(user, 'current');
    chromeMock.query.mockResolvedValue([
      { id: 23, url: 'https://example.com/after-navigation', title: '新页面' },
    ]);
    await user.type(screen.getByLabelText('任务内容'), '总结导航后的页面');
    await user.click(screen.getByRole('button', { name: '发送任务' }));
    await screen.findByText('任务已经完成。');

    const startCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith('/browser/session/start'),
    );
    expect(JSON.parse(String(startCall?.[1]?.body))).toMatchObject({
      mode: 'current',
      expected_url: 'https://example.com/after-navigation',
    });
  });

  it('当前页面不是 HTTP 页面时仍可绑定整个 Chrome', async () => {
    const user = userEvent.setup();
    const storage = mockChromeStorage();
    vi.stubGlobal('chrome', {
      storage: { local: { get: storage.get, set: storage.set } },
      tabs: {
        query: vi.fn(async () => [
          { id: 23, url: 'chrome://settings/', title: '设置' },
        ]),
      },
      scripting: {
        executeScript: vi.fn(async () => [{ result: 'chrome://settings/' }]),
      },
    });
    const fetchMock = mockHealthyBackend();
    render(<App />);

    await createConversation(user, 'current');
    await user.type(screen.getByLabelText('任务内容'), '查看其他已打开页面');
    await user.click(screen.getByRole('button', { name: '发送任务' }));
    await screen.findByText('任务已经完成。');

    const startCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith('/browser/session/start'),
    );
    const body = JSON.parse(String(startCall?.[1]?.body));
    expect(body.mode).toBe('current');
    expect(body).not.toHaveProperty('expected_url');
  });

  it('独立浏览器会话不会因为任务措辞自动改绑当前页面', async () => {
    const user = userEvent.setup();
    mockCurrentPage();
    const fetchMock = mockHealthyBackend();
    render(<App />);

    await user.click(screen.getByRole('button', { name: '新建会话' }));
    const dialog = screen.getByRole('dialog', { name: '选择浏览器' });
    await user.click(within(dialog).getByRole('button', { name: '使用独立浏览器' }));
    await user.type(screen.getByLabelText('任务内容'), '总结当前页面');
    await user.click(screen.getByRole('button', { name: '发送任务' }));
    await screen.findByText('任务已经完成。');

    const startCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith('/browser/session/start'),
    );
    const body = JSON.parse(String(startCall?.[1]?.body));
    expect(body).toMatchObject({ mode: 'isolated' });
    expect(body).not.toHaveProperty('expected_url');
    expect(body.browser_session_id).not.toBe('browser-agent-main');
    expect(screen.getByText('独立浏览器')).toBeInTheDocument();
  });

  it('保存会话并能从历史抽屉恢复', async () => {
    const user = userEvent.setup();
    const storage = mockChromeStorage();
    mockHealthyBackend('结果已返回');
    render(<App />);

    await createConversation(user);
    await user.type(screen.getByLabelText('任务内容'), '提取当前页面标题');
    await user.click(screen.getByRole('button', { name: '发送任务' }));
    await screen.findByText('结果已返回');

    await waitFor(() =>
      expect(storage.set).toHaveBeenCalledWith(
        expect.objectContaining({
          chatSessions: expect.arrayContaining([
            expect.objectContaining({ title: '提取当前页面标题' }),
          ]),
        }),
      ),
    );

    await user.click(screen.getByRole('button', { name: '打开会话' }));
    const dialog = screen.getByRole('dialog', { name: '会话列表' });
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText('提取当前页面标题')).toBeInTheDocument();
  });

  it('后端不可用时显示可操作的错误状态', async () => {
    const user = userEvent.setup();
    mockChromeStorage();
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        if (String(input).endsWith('/health')) return Promise.reject(new TypeError('Failed to fetch'));
        return jsonResponse({ detail: 'Backend unavailable' }, 503);
      }),
    );
    render(<App />);

    await createConversation(user);
    await user.type(screen.getByLabelText('任务内容'), '读取页面');
    await user.click(screen.getByRole('button', { name: '发送任务' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('无法连接本地服务');
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
    expect(screen.getByLabelText('任务内容')).toBeEnabled();
  });

  it('后端未配置模型时显示设置引导', async () => {
    const user = userEvent.setup();
    mockChromeStorage();
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith('/health')) return jsonResponse({ status: 'ok' });
        if (url.includes('/browser/sessions/')) {
          return jsonResponse({ detail: 'Browser session not found' }, 404);
        }
        if (url.endsWith('/browser/session/start')) {
          const body = JSON.parse(String(init?.body));
          return jsonResponse({ browser_session_id: body.browser_session_id, mode: body.mode, ready: true });
        }
        if (url.endsWith('/agent/run')) {
          return jsonResponse(
            {
              detail: {
                code: 'llm_not_configured',
                message: '尚未配置 LLM，请先在设置中保存模型配置。',
              },
            },
            409,
          );
        }
        return jsonResponse({ detail: 'Not found' }, 404);
      }),
    );
    render(<App />);

    await createConversation(user);
    await user.type(screen.getByLabelText('任务内容'), '读取页面');
    await user.click(screen.getByRole('button', { name: '发送任务' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      '尚未配置 LLM，请先在设置中保存模型配置。',
    );
  });

  it('设置页把模型配置提交给后端后再保存到 chrome.storage.local', async () => {
    const user = userEvent.setup();
    const storage = mockChromeStorage({
      modelConfig: {
        apiUrl: 'https://gateway.example.com/v1',
        apiKey: 'saved-key',
        model: 'gpt-5-mini',
      },
    });
    const fetchMock = mockHealthyBackend();
    render(<App />);

    await user.click(screen.getByRole('button', { name: '打开设置' }));

    expect(await screen.findByDisplayValue('https://gateway.example.com/v1')).toBeInTheDocument();
    expect(screen.getByLabelText('API Key')).toHaveValue('saved-key');
    expect(screen.getByLabelText('gpt-5-mini')).toBeChecked();
    expect(screen.queryByLabelText('服务商')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Temperature')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('最大输出')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '获取模型' }));
    expect(await screen.findByLabelText('gpt-5.1')).toBeChecked();
    await user.click(screen.getByRole('button', { name: '保存配置' }));
    await waitFor(() =>
      expect(storage.set).toHaveBeenCalledWith({
        modelSettings: expect.objectContaining({
          endpoints: [expect.objectContaining({ enabledModels: ['gpt-5.1'] })],
        }),
      }),
    );
    const configCall = fetchMock.mock.calls
      .filter(([input]) => String(input).endsWith('/llm/configs'))
      .at(-1);
    expect(configCall?.[1]?.method).toBe('PUT');
    expect(JSON.parse(String(configCall?.[1]?.body))).toEqual({
      endpoints: [{
        id: 'default',
        name: 'OpenAI',
        api_url: 'https://gateway.example.com/v1',
        api_key: 'saved-key',
        models: ['gpt-5.1'],
      }],
    });
    expect(screen.getByText('已保存并应用到本地后端')).toBeInTheDocument();
  });

  it('后端拒绝模型配置时不覆盖 Chrome 中的配置', async () => {
    const user = userEvent.setup();
    const storage = mockChromeStorage({
      modelConfig: {
        apiUrl: 'https://gateway.example.com/v1',
        apiKey: 'saved-key',
        model: 'old-model',
      },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        if (String(input).endsWith('/health')) return jsonResponse({ status: 'ok' });
        if (String(input).endsWith('/llm/configs')) {
          return jsonResponse({ detail: '配置不可用' }, 400);
        }
        if (String(input).endsWith('/llm/models')) {
          return jsonResponse({ models: ['new-model'] });
        }
        return jsonResponse({ detail: 'Not found' }, 404);
      }),
    );
    render(<App />);

    await user.click(screen.getByRole('button', { name: '打开设置' }));
    await screen.findByLabelText('old-model');
    await user.click(screen.getByRole('button', { name: '获取模型' }));
    await screen.findByLabelText('new-model');
    await user.click(screen.getByRole('button', { name: '保存配置' }));

    expect(await screen.findByText('保存失败，请重试')).toBeInTheDocument();
    expect(storage.set).not.toHaveBeenCalledWith(
      expect.objectContaining({ modelSettings: expect.anything() }),
    );
    expect(storage.values.modelConfig).toEqual({
      apiUrl: 'https://gateway.example.com/v1',
      apiKey: 'saved-key',
      model: 'old-model',
    });
  });

  it('自动获取模型并允许勾选多个模型和添加多个调用方', async () => {
    const user = userEvent.setup();
    const storage = mockChromeStorage();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/health')) return jsonResponse({ status: 'ok' });
      if (url.endsWith('/llm/models')) {
        const body = JSON.parse(String(init?.body));
        return jsonResponse({
          models: body.api_url.includes('deepseek')
            ? ['deepseek-flash', 'deepseek-pro']
            : ['qwen-fast'],
        });
      }
      if (url.endsWith('/llm/configs')) {
        return jsonResponse({ configured: true, endpoints: [] });
      }
      return jsonResponse({ detail: 'Not found' }, 404);
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    await user.click(screen.getByRole('button', { name: '打开设置' }));
    await user.clear(screen.getByLabelText('调用方名称'));
    await user.type(screen.getByLabelText('调用方名称'), 'DeepSeek');
    await user.clear(screen.getByLabelText('API 地址'));
    await user.type(screen.getByLabelText('API 地址'), 'https://deepseek.example.com/v1');
    await user.type(screen.getByLabelText('API Key'), 'deepseek-key');
    await user.click(screen.getByRole('button', { name: '获取模型' }));

    expect(await screen.findByLabelText('deepseek-flash')).toBeChecked();
    expect(screen.getByLabelText('deepseek-pro')).toBeChecked();
    await user.click(screen.getByLabelText('deepseek-pro'));
    expect(screen.getByLabelText('deepseek-pro')).not.toBeChecked();

    await user.click(screen.getByRole('button', { name: '添加调用方' }));
    expect(screen.getAllByLabelText('调用方名称')).toHaveLength(2);
    await user.type(screen.getAllByLabelText('调用方名称')[1], '本地模型');
    await user.type(screen.getAllByLabelText('API 地址')[1], 'http://127.0.0.1:4000/v1');
    await user.type(screen.getAllByLabelText('API Key')[1], 'local-key');
    await user.click(screen.getAllByRole('button', { name: '获取模型' })[1]);
    expect(await screen.findByLabelText('qwen-fast')).toBeChecked();
    await user.click(screen.getByRole('button', { name: '保存配置' }));

    const configCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith('/llm/configs'),
    );
    expect(JSON.parse(String(configCall?.[1]?.body))).toMatchObject({
      endpoints: [
        {
          name: 'DeepSeek',
          api_url: 'https://deepseek.example.com/v1',
          api_key: 'deepseek-key',
          models: ['deepseek-flash'],
        },
        {
          name: '本地模型',
          api_url: 'http://127.0.0.1:4000/v1',
          api_key: 'local-key',
          models: ['qwen-fast'],
        },
      ],
    });
    expect(storage.set).toHaveBeenCalledWith(
      expect.objectContaining({
        modelSettings: expect.objectContaining({
          endpoints: expect.arrayContaining([
            expect.objectContaining({ enabledModels: ['deepseek-flash'] }),
          ]),
        }),
      }),
    );
  });

  it('在具体对话中选择调用方模型并随任务发送', async () => {
    const user = userEvent.setup();
    mockChromeStorage({
      modelSettings: {
        endpoints: [
          {
            id: 'deepseek',
            name: 'DeepSeek',
            apiUrl: 'https://deepseek.example.com/v1',
            apiKey: 'deepseek-key',
            availableModels: ['deepseek-flash', 'deepseek-pro'],
            enabledModels: ['deepseek-flash', 'deepseek-pro'],
          },
        ],
        defaultSelection: {
          endpointId: 'deepseek',
          model: 'deepseek-flash',
        },
      },
    });
    const fetchMock = mockHealthyBackend('已使用 Pro 完成。');
    render(<App />);

    await createConversation(user);
    const modelSelect = await screen.findByLabelText('选择模型');
    await user.selectOptions(modelSelect, 'deepseek::deepseek-pro');
    await user.type(screen.getByLabelText('任务内容'), '整理页面');
    await user.click(screen.getByRole('button', { name: '发送任务' }));
    await screen.findByText('已使用 Pro 完成。');

    const runCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith('/agent/run'));
    expect(JSON.parse(String(runCall?.[1]?.body))).toMatchObject({
      llm_endpoint_id: 'deepseek',
      llm_model: 'deepseek-pro',
    });
  });

  it('运行中只显示过程线和展开箭头，完成后才显示操作耗时', async () => {
    const user = userEvent.setup();
    const encoder = new TextEncoder();
    let finishStream: (() => void) | undefined;
    mockChromeStorage();
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith('/health')) return jsonResponse({ status: 'ok' });
        if (url.includes('/browser/sessions/')) {
          return jsonResponse({ detail: 'Browser session not found' }, 404);
        }
        if (url.endsWith('/browser/session/start')) {
          return jsonResponse({ browser_session_id: 'browser-agent-test', mode: 'isolated', ready: true });
        }
        if (url.endsWith('/agent/run/stream')) {
          const stream = new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(encoder.encode([
                JSON.stringify({ type: 'run_started', run_id: 'run-ui-live' }),
                JSON.stringify({
                  type: 'trace',
                  event: {
                    kind: 'thinking',
                    status: 'running',
                    title: '正在分析页面并规划下一步',
                    timestamp: '2026-08-03T14:00:00.000+08:00',
                  },
                }),
                JSON.stringify({
                  type: 'trace',
                  event: {
                    kind: 'action',
                    status: 'running',
                    title: '执行 agent_browser_click',
                    timestamp: '2026-08-03T14:00:01.200+08:00',
                  },
                }),
              ].join('\n') + '\n'));
              finishStream = () => {
                controller.enqueue(encoder.encode([
                  JSON.stringify({ type: 'result', result: { success: true, answer: '实时轨迹任务完成' } }),
                  JSON.stringify({ type: 'done', run_id: 'run-ui-live' }),
                ].join('\n') + '\n'));
                controller.close();
              };
            },
          });
          return Promise.resolve(new Response(stream, {
            status: 200,
            headers: { 'Content-Type': 'application/x-ndjson' },
          }));
        }
        return jsonResponse({ detail: 'Not found' }, 404);
      }),
    );
    render(<App />);

    await createConversation(user);
    await user.type(screen.getByLabelText('任务内容'), '点击页面按钮');
    await user.click(screen.getByRole('button', { name: '发送任务' }));

    await screen.findByText('执行 agent_browser_click');
    const runningTrajectory = document.querySelector('.message.is-running .trajectory');
    expect(runningTrajectory).toBeInTheDocument();
    expect(runningTrajectory?.querySelector('summary svg')).toBeInTheDocument();
    expect(document.querySelector('.message.is-running .activity-line')).toBeInTheDocument();
    expect(screen.queryByText(/^操作了 /)).not.toBeInTheDocument();

    await act(async () => finishStream?.());

    expect(await screen.findByText('实时轨迹任务完成')).toBeInTheDocument();
    expect(screen.getByText('操作了 1.2s')).toBeInTheDocument();
  });

  it('删除运行中的会话时先等待任务取消，再关闭浏览器会话', async () => {
    const user = userEvent.setup();
    const encoder = new TextEncoder();
    const lifecycle: string[] = [];
    let startedRunId = '';
    mockChromeStorage();
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith('/health')) return jsonResponse({ status: 'ok' });
        if (url.includes('/browser/sessions/') && init?.method === 'DELETE') {
          lifecycle.push('session_closed');
          return jsonResponse({ closed: true });
        }
        if (url.includes('/browser/sessions/')) {
          return jsonResponse({ detail: 'Browser session not found' }, 404);
        }
        if (url.endsWith('/browser/session/start')) {
          return jsonResponse({ browser_session_id: 'browser-agent-test', mode: 'isolated', ready: true });
        }
        if (url.endsWith('/agent/run/stream')) {
          startedRunId = JSON.parse(String(init?.body)).run_id;
          const stream = new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(encoder.encode([
                JSON.stringify({ type: 'run_started', run_id: startedRunId }),
                JSON.stringify({
                  type: 'trace',
                  event: {
                    kind: 'thinking',
                    status: 'running',
                    title: '正在分析页面',
                    timestamp: '2026-08-03T14:00:00.000+08:00',
                  },
                }),
              ].join('\n') + '\n'));
            },
          });
          return Promise.resolve(new Response(stream, {
            status: 200,
            headers: { 'Content-Type': 'application/x-ndjson' },
          }));
        }
        if (startedRunId && url.endsWith(`/agent/runs/${startedRunId}`) && init?.method === 'DELETE') {
          lifecycle.push('run_cancelled');
          return jsonResponse({ cancelled: true, run_id: startedRunId });
        }
        return jsonResponse({ detail: 'Not found' }, 404);
      }),
    );
    render(<App />);

    await createConversation(user);
    await user.type(screen.getByLabelText('任务内容'), '保持运行');
    await user.click(screen.getByRole('button', { name: '发送任务' }));
    await screen.findByText('正在分析页面');
    await user.click(screen.getByRole('button', { name: '打开会话' }));
    await user.click(screen.getByRole('button', { name: '删除会话：保持运行' }));

    await waitFor(() => expect(lifecycle).toEqual(['run_cancelled', 'session_closed']));
  });

  it('在对话中只用文字展示可折叠任务轨迹，不显示 JSON 参数', async () => {
    const user = userEvent.setup();
    mockChromeStorage();
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith('/health')) return jsonResponse({ status: 'ok' });
        if (url.includes('/browser/sessions/')) {
          return jsonResponse({ detail: 'Browser session not found' }, 404);
        }
        if (url.endsWith('/browser/session/start')) {
          return jsonResponse({ browser_session_id: 'browser-agent-test', mode: 'isolated', ready: true });
        }
        if (url.endsWith('/agent/run/stream')) {
          return Promise.resolve(
            new Response(
              [
                JSON.stringify({ type: 'run_started', run_id: 'run-ui-1' }),
                JSON.stringify({
                  type: 'trace',
                  event: {
                    kind: 'thinking',
                    status: 'running',
                    title: '正在分析页面并规划下一步',
                    timestamp: '2026-08-03T14:00:00.000+08:00',
                  },
                }),
                JSON.stringify({
                  type: 'trace',
                  event: {
                    kind: 'action',
                    status: 'running',
                    title: '执行 agent_browser_click',
                    detail: '{"selector":"@e1"}',
                    timestamp: '2026-08-03T14:00:01.200+08:00',
                  },
                }),
                JSON.stringify({ type: 'result', result: { success: true, answer: '轨迹任务完成' } }),
                JSON.stringify({ type: 'done', run_id: 'run-ui-1' }),
              ].join('\n') + '\n',
              { status: 200, headers: { 'Content-Type': 'application/x-ndjson' } },
            ),
          );
        }
        return jsonResponse({ detail: 'Not found' }, 404);
      }),
    );
    render(<App />);

    await createConversation(user);
    await user.type(screen.getByLabelText('任务内容'), '点击页面按钮');
    await user.click(screen.getByRole('button', { name: '发送任务' }));

    expect(await screen.findByText('轨迹任务完成')).toBeInTheDocument();
    const trajectory = screen.getByText('操作了 1.2s');
    const details = trajectory.closest('details');
    expect(details?.querySelector('.trajectory-summary-content')).toBeInTheDocument();
    expect(details).not.toHaveAttribute('open');
    expect(screen.queryByText('查看完整轨迹')).not.toBeInTheDocument();
    expect(screen.queryByText('隐藏')).not.toBeInTheDocument();

    await user.click(trajectory);
    expect(details).toHaveAttribute('open');
    expect(screen.getByText('执行 agent_browser_click')).toBeInTheDocument();
    expect(screen.queryByText('{"selector":"@e1"}')).not.toBeInTheDocument();

    await user.click(trajectory);
    expect(details).not.toHaveAttribute('open');
  });

  it('可以新建会话并删除历史会话', async () => {
    const user = userEvent.setup();
    mockChromeStorage({
      chatSessions: [
        {
          id: 'saved-session',
          title: '旧会话',
          createdAt: 1,
          updatedAt: 1,
          messages: [{ id: 'm1', role: 'user', content: '旧任务' }],
        },
      ],
    });
    mockHealthyBackend();
    render(<App />);

    await user.click(screen.getByRole('button', { name: '打开会话' }));
    expect(await screen.findByText('旧会话')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '删除会话：旧会话' }));
    expect(screen.queryByText('旧会话')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '从历史新建会话' }));
    expect(screen.getByRole('dialog', { name: '选择浏览器' })).toBeInTheDocument();
  });
});
