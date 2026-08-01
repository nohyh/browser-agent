# Browser Agent

一个基于 LLM 的智能浏览器自动化助手，通过自然语言指令控制浏览器完成复杂任务。

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Node.js 18+](https://img.shields.io/badge/node-18+-green.svg)](https://nodejs.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## ✨ 特性

- 🤖 **自然语言控制**：用日常语言描述任务，Agent 自动规划和执行
- 🔄 **实时流式反馈**：SSE 推送每一步执行状态，可随时中断
- 🎯 **双模式浏览器**：
  - **当前浏览器模式**：操作你正在使用的 Chrome 实例
  - **隔离 Profile 模式**：启动独立浏览器窗口，互不干扰
- 🎨 **可视化指示器**：页面边框高亮 + 模拟鼠标指针，清晰展示 Agent 操作
- 🧠 **智能决策**：基于页面观察的 LLM 推理，支持多步任务链
- 📦 **Chrome 扩展**：侧边栏 UI，无需离开浏览器
- 🛠️ **150+ 浏览器工具**：点击、输入、导航、截图、等待、存储管理等全覆盖
- 🔒 **隐私保护**：敏感数据（密码、Token）自动脱敏

## 🚀 快速开始

### 前置要求

- Python 3.12+
- Node.js 18+
- Chrome 浏览器
- OpenAI API Key（或兼容服务）

### 1. 安装后端

```bash
# 克隆仓库
git clone <repository-url>
cd browser-agent/backend

# 安装 agent-browser CLI
pipx install agent-browser

# 安装 Python 依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY 和 OPENAI_MODEL

# 启动后端
uvicorn main:app --reload
```

后端运行在 `http://localhost:8000`

### 2. 安装扩展

```bash
cd browser-agent/extension

# 安装依赖
npm install

# 构建扩展
npm run build
```

打开 Chrome：
1. 访问 `chrome://extensions/`
2. 启用「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择 `extension/dist` 目录

### 3. 开始使用

1. 点击工具栏的 Browser Agent 图标打开侧边栏
2. 选择浏览器模式（隔离 / 当前）
3. 输入任务，例如：
   - "打开 GitHub，搜索 agent-browser 仓库"
   - "访问 Hacker News，总结前 5 篇热门文章"
   - "在当前页面找到所有外部链接并列出"
4. 观看 Agent 实时执行

## 📖 详细文档

- [完整设置指南](docs/setup.md) — 从零开始的详细步骤
- [架构设计](docs/design.md) — 技术细节和设计决策
- [API 文档](docs/api.md) — MCP 工具完整列表

## 🏗️ 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                     Chrome Extension                         │
│  ┌─────────────┐                                             │
│  │  Side Panel │  React + TypeScript + SSE Client            │
│  └──────┬──────┘                                             │
└─────────┼────────────────────────────────────────────────────┘
          │ HTTP + SSE
          ▼
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Backend                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │ Agent Loop   │  │ OpenAI API   │  │ MCP Client   │       │
│  │ (observe →   │─▶│ (gpt-4o)     │  │ (stdio)      │       │
│  │  decide →    │  │              │  │              │       │
│  │  act)        │  └──────────────┘  └──────┬───────┘       │
│  └──────────────┘                           │               │
└──────────────────────────────────────────────┼───────────────┘
                                               │
                                               ▼
┌─────────────────────────────────────────────────────────────┐
│                   agent-browser CLI                          │
│  Playwright + CDP → Chrome Browser Control                   │
└─────────────────────────────────────────────────────────────┘
```

**核心组件**：

- **Agent Loop** (`backend/app/agent.py`)：observe → decide → act 循环，最多 20 步
- **LLM Decision** (`backend/app/llm.py`)：OpenAI Responses API 结构化输出
- **MCP Client** (`backend/app/mcp_client.py`)：与 agent-browser 的 stdio 通信
- **SSE Streaming** (`backend/main.py`)：实时推送步骤到前端
- **Browser Service**：会话管理 + 工具调用 + 并发锁

## 🔧 开发

### 后端开发

```bash
cd backend

# 热重载
uvicorn main:app --reload

# 运行测试
pytest tests/ -v

# 代码格式化
black . && ruff check --fix .
```

### 前端开发

```bash
cd extension

# 开发模式（需手动刷新扩展）
npm run dev

# 运行测试
npm test

# 代码格式化
npm run format
```

## 📊 示例任务

### 信息提取

```
"访问当前页面，提取所有 h2 标题和对应的第一段文字"
```

### 多步导航

```
"打开 GitHub，搜索 'browser automation'，点击第一个仓库，告诉我它的 star 数"
```

### 表单填写

```
"在当前表单中填入：姓名 '张三'，邮箱 'test@example.com'，然后点击提交"
```

### 数据收集

```
"访问 Hacker News 首页，收集前 10 篇文章的标题、作者和评论数"
```

## 🛡️ 安全与隐私

- **敏感数据脱敏**：密码、Token、Cookie 自动从 trace 中移除
- **本地执行**：所有浏览器操作在本地执行，不上传到云端
- **隔离模式**：独立 Profile 确保不影响主浏览器数据
- **明确授权**：每个任务都是用户主动发起

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 提交改动 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 提交 Pull Request

## 📝 许可证

[MIT License](LICENSE)

## 🙏 致谢

- [agent-browser](https://github.com/norihiro-w/agent-browser) - 浏览器控制 MCP 服务器
- [OpenAI](https://openai.com/) - LLM API
- [Playwright](https://playwright.dev/) - 浏览器自动化引擎
- [FastAPI](https://fastapi.tiangolo.com/) - 高性能 Web 框架

## 📞 联系方式

- Issues: [GitHub Issues](<repository-issues-url>)
- Discussions: [GitHub Discussions](<repository-discussions-url>)

---

**Built with ❤️ by the Browser Agent Team**
