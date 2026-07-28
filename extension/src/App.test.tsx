// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import App from './App';

afterEach(cleanup);

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
    expect(screen.queryByRole('tab', { name: 'Agent' })).not.toBeInTheDocument();
    expect(screen.queryByText('Agent 行为')).not.toBeInTheDocument();
  });

  it('不再展示工具箱功能入口', () => {
    render(<App />);

    expect(screen.queryByRole('button', { name: '工具' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '工具箱' })).not.toBeInTheDocument();
  });
});
