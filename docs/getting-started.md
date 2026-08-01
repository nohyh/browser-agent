# Browser Agent 快速上手

Browser Agent 由 Chrome 侧边栏扩展、FastAPI companion 和 `agent-browser` MCP 服务组成。扩展只负责交互和请求编排，浏览器操作和 Agent loop 在后端完成。

## 先决条件

- Node.js 18 或更高版本
- Python 3.11 或更高版本
- Chrome
- 可用的 OpenAI 兼容模型接口
- `agent-browser` CLI 已安装并且在 `PATH` 中

安装后端依赖和浏览器 CLI：

```powershell
cd backend
python -m pip install -r requirements.txt
npm install -g agent-browser
agent-browser --version
```

## 启动后端

在 `backend` 目录中启动 companion：

```powershell
$env:OPENAI_API_KEY = "sk-..."
$env:OPENAI_MODEL = "gpt-5"
# 可选：使用 OpenAI 兼容网关
# $env:OPENAI_BASE_URL = "https://gateway.example.com/v1"
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

后端会在启动时创建长生命周期 MCP 连接。`OPENAI_API_KEY` 也可以留空，之后在扩展设置中填写；如果 `agent-browser` 不在 PATH 中，后端会在启动阶段直接报错。

## 加载扩展

```powershell
cd extension
npm install
npm run build
```

打开 `chrome://extensions`，开启开发者模式，选择“加载已解压的扩展程序”，载入 `extension/dist`。点击扩展图标即可打开侧边栏。第一次使用时可以在设置中确认：

- API 地址、API Key 和模型名称
- 后端地址，默认是 `http://127.0.0.1:8000`
- 当前浏览器的 CDP 地址，默认是 `9222`

配置保存在当前 Chrome profile 的 `chrome.storage.local` 中。API Key 只随本地任务请求发送给后端，不会写入 Agent 对话 trace。

## 选择浏览器

### 独立 profile

侧边栏默认选择“独立 profile”。输入任务并发送后，后端会为当前会话启动独立浏览器；登录态和页面不会复用当前 Chrome。

### 当前浏览器

选择“当前浏览器”，填写 Chrome 的 CDP 端口或地址，例如 `9222` 或 `http://127.0.0.1:9222`。Chrome 必须以远程调试方式启动，例如：

```powershell
chrome.exe --remote-debugging-port=9222 --user-data-dir="$env:TEMP\browser-agent-chrome"
```

然后在该 Chrome 窗口打开目标页面，再回到侧边栏发送任务。当前浏览器模式只连接明确提供的 CDP 目标，连接失败不会自动创建替代 profile。

## 使用方式

1. 选择“当前浏览器”或“独立 profile”。
2. 在输入框描述任务，按 Enter 发送；Shift+Enter 换行。
3. Agent 会先准备浏览器，再观察页面、执行动作并根据最新状态验证结果。
4. 执行中可以点击“停止任务”中止请求。停止不会自动关闭浏览器会话。

同一个对话会复用 `conversation_id` 和浏览器会话。点击新建会话会生成新的隔离标识；切换到另一种浏览器模式后建议新建会话，避免把已连接的会话和不同目标混用。

## 验证

```powershell
cd extension
npm test -- --run
npm run build

cd ..\backend
python -m pytest -q
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

如果侧边栏提示无法连接后端，先确认 `uvicorn` 仍在运行；如果会话启动失败，检查 `agent-browser --version`、CDP 端口和 Chrome 是否允许远程调试。
