# Browser Agent

Browser Agent 是一个由 FastAPI 后端驱动的浏览器 Agent：后端通过
`vercel-labs/agent-browser` 的 stdio MCP 控制 Chrome，并使用 OpenAI Responses API
兼容的模型完成任务规划与执行；`extension/` 提供 Chrome Manifest V3 Side Panel
界面。

## 架构与数据流

```text
Chrome Side Panel（React + TypeScript）
              │
              ▼
FastAPI 后端 ── OpenAI Responses API 兼容模型服务
              │
              └── stdio MCP：agent-browser mcp --tools all ── Chrome
```

后端在进程生命周期内复用模型客户端和 MCP 客户端；每次运行 Agent 前，都必须先
启动并确认一个就绪的浏览器会话。

## 已实现能力与限制

- 后端提供健康检查（`/health`、`/health/live`、`/health/ready`）、浏览器会话
  启动/查询/关闭、LLM 配置/模型发现、页面快捷建议，以及 Agent 运行与流式轨迹
  接口。
- 支持 `isolated` 会话（由后端启动独立 Chrome）、`current` 会话（绑定用户当前
  Chrome 的现有标签页）和 `existing` 会话（连接显式的 CDP 地址）。`isolated`
  不接受 `cdp_url`；`existing` 必须提供 `cdp_url`；`current` 可选的
  `expected_url` 只用于优先选择初始标签页。
- Agent 按 `conversation_id` 复用对话，支持在同一对话中按任务选择
  `llm_endpoint_id` / `llm_model` 切换模型，并返回最终答案和可选 token 用量。
- `POST /agent/run/stream` 以 NDJSON 流式返回脱敏后的决策、动作和结果轨迹；
  支持通过 `DELETE /agent/runs/{run_id}` 取消正在执行的任务。
- 扩展已接通后端：侧边栏支持输入任务、绑定浏览器会话、选择调用方与模型、
  查看流式轨迹、会话历史抽屉与设置管理。
- 当前后端依赖外部 `agent-browser` CLI 和可用的 OpenAI Responses API 兼容模型服务。

## 先决条件

- Python 3.12（依赖和 CI 均按 `>=3.12,<3.13` 锁定）
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip
- Node.js 与 npm
- Chrome 或 Chromium
- 外部 `agent-browser` CLI（当前仓库不内置，也不保证已在 PATH 中）
- OpenAI Responses API 兼容的模型服务及可用模型

## 后端快速开始

在仓库根目录执行：

```powershell
uv sync --locked
```

创建 `backend/.env`（不要提交此文件）：

```dotenv
# 可选：作为启动默认值；缺失时可通过前端设置接口配置
OPENAI_API_KEY=your-api-key
OPENAI_MODEL=your-model-name
# 可选：自托管或其他 OpenAI Responses API 兼容服务
# OPENAI_BASE_URL=https://api.example.com/v1
```

启动开发服务器（在 `backend/` 目录执行）：

```powershell
uv run uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

后端启动时不再强制要求 `OPENAI_API_KEY` 和 `OPENAI_MODEL`；缺失时 API 进程仍然
存活，模型配置可通过 `PUT /llm/config` 或 `PUT /llm/configs` 由前端提供。

### 安装 agent-browser

这是后端之外的外部先决条件。另开终端执行官方安装步骤：

```powershell
npm install -g agent-browser
agent-browser install
```

首次运行 `agent-browser install` 会安装其所需的浏览器运行时。确认
`agent-browser`（Windows 上为 `agent-browser.cmd`）可从 PATH 调用后，再启动后端。

## API 最小调用示例

保持后端运行，在 PowerShell 中先启动一个隔离会话，再运行 Agent：

```powershell
$session = Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/browser/session/start `
  -ContentType 'application/json' `
  -Body (@{
    browser_session_id = 'demo-session'
    mode = 'isolated'
  } | ConvertTo-Json)

$session

$result = Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/agent/run `
  -ContentType 'application/json' `
  -Body (@{
    message = '打开 https://example.com 并告诉我页面标题。'
    conversation_id = 'demo-conversation'
    browser_session_id = 'demo-session'
  } | ConvertTo-Json)

$result
```

常用端点：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/health` | 健康检查 |
| `GET` | `/health/live` | 进程存活探针（不依赖 MCP runtime） |
| `GET` | `/health/ready` | runtime 就绪探针，未就绪返回 503 |
| `POST` | `/browser/session/start` | 启动或接管浏览器会话 |
| `GET` | `/browser/sessions` | 列出后端管理的会话 |
| `GET` | `/browser/sessions/{browser_session_id}` | 查询会话 |
| `DELETE` | `/browser/sessions/{browser_session_id}` | 关闭/断开会话 |
| `POST` | `/agent/run` | 在就绪会话中运行 Agent |
| `POST` | `/agent/run/stream` | 以 NDJSON 流式运行 Agent |
| `DELETE` | `/agent/runs/{run_id}` | 取消正在执行的任务 |
| `PUT` | `/llm/config` | 配置单一调用方和模型 |
| `PUT` | `/llm/configs` | 配置多个调用方及各自启用的模型 |
| `POST` | `/llm/models` | 自动发现调用方可用模型 |
| `POST` | `/page/suggestions` | 为当前页面生成快捷任务建议 |

`/agent/run` 要求 `browser_session_id` 已由 `/browser/session/start` 启动且仍处于
就绪状态。更多工具和适配细节见 [`docs/api.md`](docs/api.md)。

## 扩展：开发、测试与安装

安装依赖并启动 Vite 监听构建：

```powershell
cd extension
npm ci
npm run dev
```

运行测试和生产构建：

```powershell
npm test
npm run build
```

生产构建输出到 `extension/dist`。在 Chrome 地址栏打开 `chrome://extensions`，
启用“开发者模式”，点击“加载已解压的扩展程序”，选择仓库中的
`extension/dist` 目录即可。扩展通过设置页配置 API 地址、Key 与模型，任务提交、
浏览器会话绑定和流式轨迹展示均直接调用 FastAPI 后端。

## 测试命令

后端（在仓库根目录，需先执行 `uv sync --locked`）：

```powershell
uv run pytest -q
uv run ruff check backend
uv run pyright
```

扩展（在 `extension/` 目录）：

```powershell
npm test
npm run build
```

## 目录结构

```text
backend/
  main.py                 FastAPI 应用、路由与 lifespan
  app/                    Agent、MCP、浏览器会话和模型实现
  tests/                  后端测试（pytest）
  logs/                   会话轨迹（Markdown + JSONL，自动清理）
extension/
  src/                    React Side Panel 源码
  package.json            npm 脚本与依赖
  package-lock.json       锁定的扩展依赖
  dist/                   构建产物（运行 build 后生成）
docs/
  api.md                  API 与 MCP 工具参考
  mcp-v2-migration-guide.md
pyproject.toml             后端依赖、Ruff/Pyright/pytest 配置
uv.lock                    锁定的 Python 依赖
.github/workflows/build.yml 干净检出、扩展构建与后端质量门 CI
```

## 安全注意事项

- 不要提交 `backend/.env`、API Key 或其他凭据；`.env` 已在忽略规则中。
- `existing` 模式的 `cdp_url` 会授予后端控制对应浏览器的能力，只连接你明确授权
  的浏览器。
- 浏览器会话状态、conversation trace、trace/录制文件和页面快照可能包含敏感页面
  数据；请限制保存位置和访问权限，并在共享或提交前检查内容。
- 使用真实账号时，先确认 Agent 任务、站点权限及模型服务的数据处理策略。
