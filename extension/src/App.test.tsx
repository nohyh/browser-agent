// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mockChromeStorage(initialValue?: {
  apiUrl: string;
  apiKey: string;
  model: string;
}) {
  let storedValue = initialValue;
  const get = vi.fn(async () => (storedValue ? { modelConfig: storedValue } : {}));
  const set = vi.fn(async (value: { modelConfig: typeof storedValue }) => {
    storedValue = value.modelConfig;
  });

  vi.stubGlobal('chrome', {
    storage: {
      local: {
        get,
        set,
      },
    },
  });

  return { get, set };
}

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn(async () => body),
    text: vi.fn(async () => JSON.stringify(body)),
  } as unknown as Response;
}

describe('Browser Agent 侧边栏', () => {
  it('展示精简聊天首页与新 Logo', () => {
    render(<App />);

    expect(screen.getByText('Browser Agent')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Browser Agent' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '需要我做什么？' })).toBeInTheDocument();
    expect(screen.getByPlaceholderText('描述你想在当前网页完成的任务')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '发送任务' })).toBeDisabled();
    expect(screen.queryByText('理解当前页面')).not.toBeInTheDocument();
  });

  it('可以打开会话列表并切换历史会话', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole('button', { name: '打开会话' }));
    expect(screen.getByRole('dialog', { name: '会话列表' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /整理本周研究资料/ }));
    expect(screen.getByText('整理本周研究资料')).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: '会话列表' })).not.toBeInTheDocument();
  });

  it('设置页只保留模型配置', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole('button', { name: '打开设置' }));

    expect(screen.getByRole('heading', { name: '设置' })).toBeInTheDocument();
    expect(screen.getByLabelText('API 地址')).toHaveValue('https://api.openai.com/v1');
    expect(screen.getByLabelText('模型')).toHaveValue('gpt-5');
    expect(screen.queryByLabelText('服务商')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Temperature')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('最大输出')).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Agent' })).not.toBeInTheDocument();
    expect(screen.queryByText('Agent 行为')).not.toBeInTheDocument();
  });

  it('从 chrome.storage.local 恢复模型配置', async () => {
    const user = userEvent.setup();
    const storage = mockChromeStorage({
      apiUrl: 'https://gateway.example.com/v1',
      apiKey: 'saved-key',
      model: 'gpt-5-mini',
    });

    render(<App />);
    await user.click(screen.getByRole('button', { name: '打开设置' }));

    expect(await screen.findByDisplayValue('https://gateway.example.com/v1')).toBeInTheDocument();
    expect(screen.getByLabelText('API Key')).toHaveValue('saved-key');
    expect(screen.getByLabelText('模型')).toHaveValue('gpt-5-mini');
    expect(storage.get).toHaveBeenCalledWith('modelConfig');
  });

  it('将模型配置保存到 chrome.storage.local', async () => {
    const user = userEvent.setup();
    const storage = mockChromeStorage();

    render(<App />);
    await user.click(screen.getByRole('button', { name: '打开设置' }));
    await user.clear(screen.getByLabelText('API 地址'));
    await user.type(screen.getByLabelText('API 地址'), 'https://api.example.com/v1');
    await user.clear(screen.getByLabelText('API Key'));
    await user.type(screen.getByLabelText('API Key'), 'test-key');
    await user.clear(screen.getByLabelText('模型'));
    await user.type(screen.getByLabelText('模型'), 'gpt-5.1');
    await user.click(screen.getByRole('button', { name: '保存配置' }));

    await waitFor(() =>
      expect(storage.set).toHaveBeenCalledWith({
        modelConfig: {
          apiUrl: 'https://api.example.com/v1',
          apiKey: 'test-key',
          model: 'gpt-5.1',
        },
      }),
    );
    expect(screen.getByText('已保存到 Chrome 本地存储')).toBeInTheDocument();
  });

  it('不再展示工具箱功能入口', () => {
    render(<App />);

    expect(screen.queryByRole('button', { name: '工具' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '工具箱' })).not.toBeInTheDocument();
  });

  it('选择独立 profile 后先启动浏览器再执行真实任务', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          browser_session_id: 'browser-agent-test',
          mode: 'isolated',
          ready: true,
          url: 'about:blank',
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          success: true,
          answer: '任务已经完成',
          token_usage: null,
        }),
      );
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect(screen.getByRole('radio', { name: '独立 profile' })).toBeChecked();
    await user.type(screen.getByLabelText('任务内容'), '打开当前页面的登录按钮');
    await user.click(screen.getByRole('button', { name: '发送任务' }));

    expect(await screen.findByText('任务已经完成')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
      browser_session_id: expect.any(String),
      mode: 'isolated',
    });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({
      message: '打开当前页面的登录按钮',
      conversation_id: expect.any(String),
      browser_session_id: expect.any(String),
    });
  });

  it('选择当前浏览器时使用显式 CDP 地址', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          browser_session_id: 'browser-agent-test',
          mode: 'existing',
          ready: true,
          url: 'https://example.com',
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          success: true,
          answer: '已完成',
          token_usage: null,
        }),
      );
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    await user.click(screen.getByRole('radio', { name: '当前浏览器' }));
    const cdpInput = screen.getByLabelText('当前浏览器连接地址');
    await user.clear(cdpInput);
    await user.type(cdpInput, '9222');
    await user.type(screen.getByLabelText('任务内容'), '读取当前页面标题');
    await user.click(screen.getByRole('button', { name: '发送任务' }));

    await screen.findByText('已完成');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
      mode: 'existing',
      cdp_url: '9222',
    });
  });

  it('停止任务会中止正在进行的后端请求', async () => {
    const user = userEvent.setup();
    let resolveRun: ((response: Response) => void) | undefined;
    const runResponse = new Promise<Response>((resolve) => {
      resolveRun = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          browser_session_id: 'browser-agent-test',
          mode: 'isolated',
          ready: true,
          url: 'about:blank',
        }),
      )
      .mockReturnValueOnce(runResponse);
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    await user.type(screen.getByLabelText('任务内容'), '执行一个耗时任务');
    await user.click(screen.getByRole('button', { name: '发送任务' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await user.click(screen.getByRole('button', { name: '停止任务' }));

    expect(fetchMock.mock.calls[1][1].signal.aborted).toBe(true);
    expect(screen.getByText('任务已停止')).toBeInTheDocument();
    resolveRun?.(jsonResponse({ success: true, answer: 'late result' }));
  });
});
