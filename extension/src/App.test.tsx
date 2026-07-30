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
});
