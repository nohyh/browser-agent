# Browser Agent Setup Guide

本文档提供从零开始运行 Browser Agent 的完整步骤。

## 前置要求

- **Python 3.12+**
- **Node.js 18+** 和 npm
- **Chrome 浏览器**（用于加载扩展）
- **OpenAI API Key**（或兼容的 API 服务）

## 1. 克隆仓库

```bash
git clone <repository-url>
cd browser-agent
git checkout claude
```

## 2. 后端设置

### 2.1 安装 agent-browser CLI

Browser Agent 依赖 [`agent-browser`](https://github.com/norihiro-w/agent-browser) 工具与浏览器交互。

```bash
# 使用 pipx 全局安装（推荐）
pipx install agent-browser

# 或使用 pip
pip install agent-browser

# 验证安装
agent-browser --version
```

### 2.2 安装 Python 依赖

```bash
cd backend
pip install -r requirements.txt
```

### 2.3 配置环境变量

在 `backend/` 目录下创建 `.env` 文件：

```bash
cp .env.example .env
```

编辑 `.env` 文件，填入你的 API 配置：

```env
# OpenAI API 配置（必填）
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o

# 如果使用兼容服务（如 Azure OpenAI），设置 BASE_URL
# OPENAI_BASE_URL=https://your-endpoint.openai.azure.com/v1

# agent-browser CLI 路径（可选，默认从 PATH 查找）
# AGENT_BROWSER_CLI=/path/to/agent-browser

# 浏览器配置（可选，运行时动态指定）
# AGENT_BROWSER_HEADED=true
# AGENT_BROWSER_PROFILE=/path/to/profile
```

### 2.4 启动后端服务

```bash
cd backend
python -m uvicorn main:app --reload --port 8000
```

后端会在 `http://localhost:8000` 启动，访问 `http://localhost:8000/health` 验证服务运行正常。

## 3. 前端扩展设置

### 3.1 安装依赖

```bash
cd extension
npm install
```

### 3.2 构建扩展

```bash
npm run build
```

构建产物位于 `extension/dist/` 目录。

### 3.3 加载扩展到 Chrome

1. 打开 Chrome，访问 `chrome://extensions/`
2. 右上角启用「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择 `browser-agent/extension/dist` 目录
5. 扩展加载完成后，工具栏会显示 Browser Agent 图标

### 3.4 打开侧边栏

- 点击工具栏的 Browser Agent 图标，或
- 右键点击任意网页，选择「Browser Agent」，或
- 使用快捷键（如果已设置）

侧边栏会在浏览器右侧弹出。

## 4. 使用 Agent

### 4.1 选择浏览器模式

打开侧边栏后，你会看到两种模式选择：

- **使用当前浏览器**：Agent 连接到你正在使用的 Chrome 实例，可以直接操作当前标签页和窗口。需要 Chrome 启动时开启调试端口（见下文）。
- **打开独立 Profile**（默认）：Agent 启动一个全新的 Chrome 窗口（带独立用户数据），不影响你当前的浏览器状态。

### 4.2 启用「使用当前浏览器」模式

如果想让 Agent 操作当前浏览器，需要以调试模式重启 Chrome：

**Windows**：
```cmd
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

**macOS**：
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
```

**Linux**：
```bash
google-chrome --remote-debugging-port=9222
```

重启后，后端会自动探测调试端口并连接。

### 4.3 发送任务

在侧边栏底部的输入框中输入任务描述，例如：

- "打开 GitHub，搜索 agent-browser 仓库，并 star 它"
- "访问 Hacker News，找到当前热门的前 5 篇文章标题"
- "在当前页面找到所有外部链接，并列出它们的域名"

点击发送按钮（或按 Enter），Agent 会实时推送执行步骤：

- 📍 **观察页面**：读取当前页面状态
- 💭 **思考决策**：LLM 决定下一步动作
- 🎬 **执行操作**：点击、输入、导航等
- ✅ **完成**：返回最终结果

### 4.4 停止任务

任务运行期间，发送按钮会变为「停止」按钮。点击可立即取消当前任务。

## 5. 测试与调试

### 5.1 运行后端测试

```bash
cd backend
pytest tests/ -v
```

### 5.2 运行前端测试

```bash
cd extension
npm test
```

### 5.3 查看日志

- **后端日志**：`backend/logs/conversations/` 目录下存储每个会话的完整 trace
- **前端日志**：打开 Chrome DevTools（F12），Console 标签查看

### 5.4 查看 SSE 流

使用 curl 测试 SSE endpoint：

```bash
curl -N -X POST http://localhost:8000/agent/run/stream \
  -H "Content-Type: application/json" \
  -d '{
    "message": "访问 example.com",
    "conversation_id": "test",
    "browser_session_id": "test-session"
  }'
```

## 6. 常见问题

### Q: 扩展无法连接后端

**A**: 确认后端正在运行（`http://localhost:8000/health` 返回 200），并检查扩展的 `src/api.ts` 中的 `API_BASE` 是否正确。

### Q: "Browser session not ready" 错误

**A**: 
- 如果使用「当前浏览器」模式，确保 Chrome 以 `--remote-debugging-port=9222` 启动
- 如果使用「独立 Profile」模式，确保 `agent-browser` CLI 已正确安装

### Q: Agent 找不到页面元素

**A**: 
- 等待页面完全加载后再发送任务
- 使用更精确的描述（如"点击标题为 '登录' 的按钮"）
- 检查页面是否有 iframe 或 Shadow DOM

### Q: SSE 连接中断

**A**:
- 检查防火墙或代理设置
- 确认浏览器未阻止跨域请求（CORS 已在后端配置）
- 查看 Chrome Network 标签的 EventStream 类型请求

### Q: 任务超时

**A**:
- 默认最大步数为 20，复杂任务可能需要拆分
- 检查 LLM API 是否响应缓慢
- 增加 `backend/app/agent.py` 中的 `max_steps` 参数

## 7. 开发模式

### 7.1 热重载后端

```bash
cd backend
uvicorn main:app --reload --port 8000
```

代码修改后自动重启。

### 7.2 热重载前端

```bash
cd extension
npm run dev
```

修改代码后刷新扩展（`chrome://extensions/` → 点击刷新图标），然后重新打开侧边栏。

### 7.3 修改 Agent 提示词

编辑 `backend/app/llm.py` 中的系统提示词，重启后端生效。

### 7.4 添加新的浏览器工具

`agent-browser` 提供 150+ 工具，当前全部可用。如需自定义工具分组，编辑 `backend/app/utils/tools.py`。

## 8. 部署

### 8.1 部署后端

推荐使用 Docker 容器化部署：

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir agent-browser
COPY backend/ .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

构建并运行：

```bash
docker build -t browser-agent-backend .
docker run -p 8000:8000 --env-file .env browser-agent-backend
```

### 8.2 打包扩展

生产构建：

```bash
cd extension
npm run build
```

打包为 `.zip`：

```bash
cd dist
zip -r ../browser-agent-extension.zip .
```

提交到 Chrome Web Store 或直接分发 `.zip` 文件。

## 9. 架构参考

- **后端**：FastAPI + MCP + OpenAI Responses API
- **前端**：React 18 + TypeScript + Vite
- **浏览器控制**：agent-browser（基于 Playwright + CDP）
- **通信协议**：Server-Sent Events (SSE) 实时流
- **会话管理**：内存字典 + TTL 清理（24h）

详细设计文档：`docs/design.md`

## 10. 贡献指南

1. Fork 仓库，创建功能分支
2. 运行完整测试：`pytest` + `npm test`
3. 遵循代码风格：`black` + `ruff` + `prettier`
4. 提交 PR 前确保所有测试通过

## 11. 许可证

[MIT License](../LICENSE)

## 12. 支持

- 提交 Issue：<repository-issues-url>
- 文档：`docs/` 目录
- 示例：`examples/` 目录（如果有）
