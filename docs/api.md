# agent-browser MCP API 完整参考

本文档对应本地源码和实际运行的 `agent-browser 0.33.0`。范围是 `agent-browser mcp --tools all` 暴露的完整 MCP 接口，共 152 个类型化工具。

## 1. 能力边界

`agent-browser mcp --tools all` 基本等同于当前版本 CLI 的完整类型化接口。MCP 层最终仍调用同一个 CLI 解析器和浏览器守护进程，因此页面导航、DOM 快照、元素操作、标签页、iframe、上传下载、网络拦截、状态管理、调试、React 检查和移动端模拟等能力与 CLI 保持一致。

仍需注意以下边界：

- MCP 只提供浏览器“手脚”，不包含 agent loop、任务规划、记忆或判断逻辑。
- Chrome 扩展页面不能直接启动本地 stdio 子进程。扩展应连接本地 Python 或 Node companion，再由 companion 启动 MCP。
- `stream` 返回实时流服务的状态和地址，实际画面流通过 WebSocket 传输，不会塞进普通 MCP 文本响应。
- 操作系统权限、Chrome 的远程调试授权、网站自身权限和浏览器安全限制仍然有效。
- 少量能力依赖 CDP。Safari 或 iOS WebDriver 后端不能完整支持截图标注、a11y 等 CDP 专属能力。
- `extraArgs` 能补足高级 CLI 参数，但如果上游将来新增 CLI 命令而没有发布对应 MCP 工具，仍需升级到已经同步 MCP 的版本。

## 2. 启动方式

默认启动只暴露 `core`：

```powershell
agent-browser mcp
```

完整能力必须使用：

```powershell
agent-browser mcp --tools all
```

也可以组合多个较小的 profile：

```powershell
agent-browser mcp --tools core,network,state,tabs
```

### 2.1 MCP 客户端配置

```json
{
  "mcpServers": {
    "agent-browser": {
      "command": "agent-browser",
      "args": ["mcp", "--tools", "all"]
    }
  }
}
```

Windows 上如果 MCP 客户端不能直接解析 `.cmd`，使用：

```json
{
  "mcpServers": {
    "agent-browser": {
      "command": "cmd.exe",
      "args": ["/d", "/s", "/c", "agent-browser.cmd mcp --tools all"]
    }
  }
}
```

### 2.2 连接用户当前的 Chrome

Chrome 144+ 的内置远程调试开关开启后，在 MCP 进程环境中设置：

```dotenv
AGENT_BROWSER_AUTO_CONNECT=1
AGENT_BROWSER_SESSION=personal-agent
```

第一次附加时 Chrome 可能显示授权提示，需要用户点击允许。使用 auto-connect 时不要同时设置 `AGENT_BROWSER_PROFILE`、`AGENT_BROWSER_CDP` 或 `allowedDomains`。

如果已经知道 CDP 端点，可以改为：

```dotenv
AGENT_BROWSER_CDP=ws://127.0.0.1:9222/devtools/browser/...
AGENT_BROWSER_SESSION=personal-agent
```

### 2.3 启动独立 profile 浏览器

不要设置 `AGENT_BROWSER_AUTO_CONNECT` 或 `AGENT_BROWSER_CDP`，改为：

```dotenv
AGENT_BROWSER_PROFILE=D:\BrowserProfiles\personal-agent
AGENT_BROWSER_HEADED=true
AGENT_BROWSER_SESSION=personal-agent
```

当前浏览器和独立 profile 可以共用完全相同的上层 MCP 工具。差异只在 MCP 子进程的启动环境。

## 3. MCP 协议

### 3.1 传输

- 传输方式：stdio
- 帧格式：每行一个 JSON-RPC 2.0 对象
- 默认 MCP 协议版本：`2025-11-25`
- 服务名：`agent-browser`
- 支持的方法：`initialize`、`ping`、`tools/list`、`tools/call`、`shutdown`

初始化请求：

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"my-agent","version":"0.1.0"}}}
```

### 3.2 工具发现必须分页

`tools/list` 每页最多返回 64 个工具。`--tools all` 有 152 个工具，所以客户端必须持续读取 `nextCursor`：

```json
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
{"jsonrpc":"2.0","id":3,"method":"tools/list","params":{"cursor":"64"}}
{"jsonrpc":"2.0","id":4,"method":"tools/list","params":{"cursor":"128"}}
```

游标是字符串。只有响应不再包含 `nextCursor` 时才算发现完成。

### 3.3 工具调用

```json
{"jsonrpc":"2.0","id":10,"method":"tools/call","params":{"name":"agent_browser_click","arguments":{"selector":"@e7","session":"personal-agent"}}}
```

### 3.4 返回结构

普通 CLI 映射工具返回：

```json
{
  "content": [
    {
      "type": "text",
      "text": "..."
    }
  ],
  "structuredContent": {
    "exitCode": 0,
    "stdout": "{\"success\":true,\"data\":{...}}",
    "stderr": "",
    "response": {
      "success": true,
      "data": {}
    }
  },
  "isError": false
}
```

判断成功时至少检查：

1. `isError` 必须为 `false`。
2. `structuredContent.response.success` 不能为 `false`。
3. 对重要动作继续检查页面后置条件。例如点击保存后重新 snapshot 或等待成功文本。

截图工具可能在 `content` 中额外返回 `type: "image"`。`agent_browser_tools_profiles` 是元工具，其 `structuredContent` 直接包含 profile 信息，不使用上述 CLI response 包装。

JSON-RPC 层常见错误码：

- `-32700`：JSON 解析失败
- `-32600`：请求格式无效
- `-32601`：方法不存在
- `-32602`：工具名、参数或类型无效

## 4. 所有工具共有的参数

下列 10 个字段会被注入每一个工具的 `inputSchema`。各 API 表只列工具自己的字段。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `session` | `string` | 否 | 隔离的浏览器会话名。一个 agent 任务应始终复用同一个值。 |
| `namespace` | `string` | 否 | 隔离 daemon socket 和 restore 状态目录。 |
| `restore` | `boolean \| string` | 否 | `true` 使用当前 session 作为恢复键；字符串使用显式恢复键。 |
| `restoreSave` | `"auto" \| "always" \| "never"` | 否 | 恢复状态的自动保存策略。 |
| `restoreCheckUrl` | `string` | 否 | 恢复后必须匹配的 URL pattern。 |
| `restoreCheckText` | `string` | 否 | 恢复后页面必须包含的文本。 |
| `restoreCheckFn` | `string` | 否 | 恢复后必须为真的 JavaScript 表达式。 |
| `allowedDomains` | `string[]` | 否 | 限制浏览器和 read 流量的域名模式。与 CDP、auto-connect、profile 等已有上下文模式不兼容。 |
| `extraArgs` | `string[]` | 否 | 附加到对应 CLI 命令的高级参数，用于精确 CLI parity。 |
| `timeoutMs` | `integer` | 否 | 整次工具调用超时，最小值 1，默认 120000 ms。 |

## 5. 参数记法

- 没有 `?` 的字段为必填。
- `field?: type` 表示可选字段。
- `A \| B` 表示联合类型。
- `"a" \| "b"` 表示枚举。
- `object` 表示 JSON object。
- `array<array>` 表示二维 JSON 数组。

## 6. 完整 API 参考

### 6.1 MCP 元信息、浏览器生命周期与会话，共 12 个

| 工具 | 专有参数 | 作用 |
|---|---|---|
| `agent_browser_tools_profiles` | 无 | 返回可用 MCP profile、当前启用 profile、工具数量和启动方法。 |
| `agent_browser_open` | `url?: string, headed?: boolean, webgpu?: boolean` | 启动浏览器，并可选导航到 URL。 |
| `agent_browser_connect` | `target: string` | 通过端口、HTTP URL 或 WebSocket URL 连接 CDP。 |
| `agent_browser_close` | `all?: boolean` | 关闭当前 session；`all` 关闭全部活跃 session。 |
| `agent_browser_session` | 无 | 显示当前 session。 |
| `agent_browser_session_list` | 无 | 列出活跃 session。 |
| `agent_browser_session_id` | `prefix?: string, scope?: "worktree" \| "cwd" \| "git-root"` | 基于工作区、当前目录或 Git 根生成稳定 session id。 |
| `agent_browser_session_info` | 无 | 返回 session、daemon、启动方式和 restore 诊断信息。 |
| `agent_browser_profiles` | 无 | 列出可用 Chrome profile。 |
| `agent_browser_stream_enable` | `port?: integer` | 启用运行时 WebSocket 画面流。 |
| `agent_browser_stream_disable` | 无 | 停止运行时画面流。 |
| `agent_browser_stream_status` | 无 | 返回画面流状态、端口和连接信息。 |

### 6.2 导航、读取与观察，共 26 个

| 工具 | 专有参数 | 作用 |
|---|---|---|
| `agent_browser_read` | `url?: string, raw?: boolean, requireMd?: boolean, llms?: "index" \| "full", outline?: boolean, filter?: string, readTimeoutMs?: integer` | 将 URL 或当前活动标签页读取为 agent 友好的文本。显式 URL 优先读取 Markdown。 |
| `agent_browser_snapshot` | `interactive?: boolean, includeUrls?: boolean, compact?: boolean, depth?: integer, selector?: string` | 返回带 `@eN` 元素引用的可访问性树。 |
| `agent_browser_screenshot` | `path?: string, fullPage?: boolean, annotate?: boolean, selector?: string, screenshotDir?: string, format?: "png" \| "jpeg", quality?: integer` | 截图。小型 PNG 或 JPEG 还会作为 MCP image content 返回。 |
| `agent_browser_pdf` | `path: string` | 将当前页面保存为 PDF。 |
| `agent_browser_back` | 无 | 后退。 |
| `agent_browser_forward` | 无 | 前进。 |
| `agent_browser_reload` | 无 | 刷新当前页面。 |
| `agent_browser_pushstate` | `url: string` | 执行 SPA 客户端导航。 |
| `agent_browser_get_text` | `selector: string` | 获取元素可见文本。 |
| `agent_browser_get_html` | `selector: string` | 获取元素 `innerHTML`。 |
| `agent_browser_get_value` | `selector: string` | 获取表单控件值。 |
| `agent_browser_get_attr` | `selector: string, name: string` | 获取元素属性。 |
| `agent_browser_get_count` | `selector: string` | 统计匹配元素数量。 |
| `agent_browser_get_box` | `selector: string` | 获取元素边界框。 |
| `agent_browser_get_styles` | `selector: string` | 获取元素计算样式。 |
| `agent_browser_get_url` | 无 | 获取当前 URL。 |
| `agent_browser_get_title` | 无 | 获取当前页面标题。 |
| `agent_browser_get_cdp_url` | 无 | 获取当前浏览器 CDP WebSocket URL。 |
| `agent_browser_is_visible` | `selector: string` | 判断元素是否可见。 |
| `agent_browser_is_enabled` | `selector: string` | 判断元素是否可用。 |
| `agent_browser_is_checked` | `selector: string` | 判断 checkbox 或 switch 是否选中。 |
| `agent_browser_a11y` | `url?: string, selector?: string, tags?: string, json?: boolean` | 运行内置 axe-core 可访问性审计。 |
| `agent_browser_diff_snapshot` | `baseline?: string, selector?: string, compact?: boolean, depth?: integer` | 将当前 snapshot 与上次或文件基线比较。 |
| `agent_browser_diff_screenshot` | `baseline?: string, output?: string, selector?: string, fullPage?: boolean, threshold?: number` | 将当前截图与图片基线做像素差异比较。 |
| `agent_browser_diff_url` | `url1: string, url2: string, screenshot?: boolean, selector?: string, compact?: boolean, depth?: integer, fullPage?: boolean, waitUntil?: string` | 比较两个 URL 的 snapshot，并可选比较截图。 |
| `agent_browser_vitals` | `url?: string, json?: boolean` | 收集 Core Web Vitals 和 hydration 指标。 |

### 6.3 元素、键盘、鼠标与通用动作，共 28 个

| 工具 | 专有参数 | 作用 |
|---|---|---|
| `agent_browser_click` | `selector: string, newTab?: boolean` | 点击 `@ref` 或 CSS selector；可要求在新标签页打开。 |
| `agent_browser_dblclick` | `selector: string` | 双击元素。 |
| `agent_browser_focus` | `selector: string` | 聚焦元素。 |
| `agent_browser_hover` | `selector: string` | 悬停元素。 |
| `agent_browser_fill` | `selector: string, text: string` | 清空并填写 input。 |
| `agent_browser_type` | `selector: string, text: string, delayMs?: integer, clear?: boolean` | 向元素输入文本，可设置逐键延迟和是否先清空。 |
| `agent_browser_press` | `key: string` | 在当前焦点按下并释放按键或组合键。 |
| `agent_browser_keydown` | `key: string` | 按住按键。 |
| `agent_browser_keyup` | `key: string` | 释放按键。 |
| `agent_browser_keyboard_type` | `text: string` | 在当前焦点使用真实按键事件输入。 |
| `agent_browser_keyboard_insert_text` | `text: string` | 在当前焦点直接插入文本，不发送按键事件。 |
| `agent_browser_check` | `selector: string` | 选中 checkbox 或 switch。 |
| `agent_browser_uncheck` | `selector: string` | 取消选中 checkbox 或 switch。 |
| `agent_browser_select` | `selector: string, values: string[]` | 选择一个或多个原生 select option。 |
| `agent_browser_drag` | `source: string, target: string` | 将 source 拖放到 target。 |
| `agent_browser_upload` | `selector: string, files: string[]` | 向 file input 上传一个或多个文件。 |
| `agent_browser_download` | `selector: string, path: string` | 点击元素并将产生的下载保存到 path。 |
| `agent_browser_scroll` | `direction?: "up" \| "down" \| "left" \| "right", amount?: integer, selector?: string` | 滚动页面或指定元素。 |
| `agent_browser_scroll_into_view` | `selector: string` | 将元素滚动到可视区域。 |
| `agent_browser_find` | `locator: "role" \| "text" \| "label" \| "placeholder" \| "alt" \| "title" \| "testid" \| "first" \| "last" \| "nth", value: string, action?: string, text?: string, name?: string, exact?: boolean, index?: integer` | 使用语义 locator 查找元素，并可执行 click、fill、check、hover 或 text 等动作。 |
| `agent_browser_eval` | `script: string` | 在页面内执行 JavaScript。脚本通过 stdin 传给 CLI，避免 shell 转义问题。 |
| `agent_browser_batch` | `commands: array<array>, bail?: boolean` | 在一次调用中顺序执行多个 CLI 命令；`bail` 在首个失败时停止。 |
| `agent_browser_mouse_move` | `x: number, y: number` | 移动鼠标。 |
| `agent_browser_mouse_down` | `button?: "left" \| "right" \| "middle"` | 按下鼠标按钮。 |
| `agent_browser_mouse_up` | `button?: "left" \| "right" \| "middle"` | 释放鼠标按钮。 |
| `agent_browser_mouse_wheel` | `dy: number, dx?: number` | 滚动鼠标滚轮。 |
| `agent_browser_tap` | `selector: string` | 在 iOS 或 touch 后端轻触元素。 |
| `agent_browser_swipe` | `direction: "up" \| "down" \| "left" \| "right", amount?: integer` | 执行滑动手势。 |

文件路径必须是运行 MCP companion 的本机可访问路径。对当前浏览器执行上传仍然是正常 CDP 文件注入，不要求 LLM 直接操作文件管理器。

### 6.4 等待，共 7 个

| 工具 | 专有参数 | 作用 |
|---|---|---|
| `agent_browser_wait_ms` | `ms: integer` | 固定等待毫秒数。 |
| `agent_browser_wait_for_selector` | `selector: string, waitTimeoutMs?: integer` | 等待元素出现。 |
| `agent_browser_wait_for_text` | `text: string, waitTimeoutMs?: integer` | 等待页面出现指定文本。 |
| `agent_browser_wait_for_url` | `url: string, waitTimeoutMs?: integer` | 等待当前 URL 匹配 pattern。 |
| `agent_browser_wait_for_load` | `state: "load" \| "domcontentloaded" \| "networkidle", waitTimeoutMs?: integer` | 等待加载状态。 |
| `agent_browser_wait_for_function` | `expression: string, waitTimeoutMs?: integer` | 等待 JavaScript 表达式变为真。 |
| `agent_browser_wait_for_download` | `path?: string, waitTimeoutMs?: integer` | 等待浏览器下载完成。 |

`waitTimeoutMs` 是浏览器条件的等待时间，外层共有的 `timeoutMs` 是整个 MCP 工具调用的超时。应保证 `timeoutMs` 大于 `waitTimeoutMs`。

### 6.5 标签页、窗口、iframe 与弹窗，共 10 个

| 工具 | 专有参数 | 作用 |
|---|---|---|
| `agent_browser_tab_new` | `url?: string, label?: string` | 新建标签页，可同时导航并设置稳定 label。 |
| `agent_browser_tab_list` | 无 | 列出标签页、稳定 tab id 和 label。 |
| `agent_browser_tab_switch` | `tab: string` | 按 `tN` id 或 label 切换标签页。 |
| `agent_browser_tab_close` | `tab?: string` | 关闭指定标签页；省略时关闭活动页。 |
| `agent_browser_window_new` | 无 | 新建浏览器窗口。 |
| `agent_browser_frame_switch` | `frame: string` | 按 selector、`@ref` 或 frame id 进入 iframe。 |
| `agent_browser_frame_main` | 无 | 返回主 frame。 |
| `agent_browser_dialog_status` | 无 | 获取当前 JavaScript dialog 状态。 |
| `agent_browser_dialog_accept` | `text?: string` | 接受 alert、confirm 或 prompt；prompt 可传输入文本。 |
| `agent_browser_dialog_dismiss` | 无 | 取消 JavaScript dialog。 |

tab id 形如 `t1`、`t2`，在一个 session 内不会重复使用。元素引用必须写成 `@e7`，不能传裸的 `e7`。进入 iframe 后应重新 snapshot；操作结束后调用 `agent_browser_frame_main`。

### 6.6 浏览器设置与设备模拟，共 8 个

| 工具 | 专有参数 | 作用 |
|---|---|---|
| `agent_browser_set_viewport` | `width: integer, height: integer, scale?: number` | 设置 viewport 和可选设备缩放比例。 |
| `agent_browser_set_device` | `device: string` | 按设备名模拟设备。 |
| `agent_browser_set_geo` | `latitude: number, longitude: number` | 设置地理位置。 |
| `agent_browser_set_offline` | `enabled: boolean` | 开关离线模式。 |
| `agent_browser_set_headers` | `headers: object` | 设置额外 HTTP headers。 |
| `agent_browser_set_credentials` | `username: string, password: string` | 设置 HTTP Basic Auth。 |
| `agent_browser_set_media` | `colorScheme?: "dark" \| "light" \| "no-preference", reducedMotion?: "reduce" \| "no-preference"` | 模拟颜色主题和减少动画偏好。 |
| `agent_browser_device` | `action?: "list"` | 列出可用 iOS simulator。 |

### 6.7 网络，共 6 个

| 工具 | 专有参数 | 作用 |
|---|---|---|
| `agent_browser_network_route` | `url: string, abort?: boolean, body?: string, resourceType?: string` | 拦截匹配请求，可阻断或返回 mock body。 |
| `agent_browser_network_unroute` | `url?: string` | 移除指定或全部网络 route。 |
| `agent_browser_network_requests` | `filter?: string, type?: string, method?: string, status?: string, clear?: boolean` | 查询捕获的网络请求，可按文本、资源类型、方法和状态过滤。 |
| `agent_browser_network_request` | `requestId: string` | 获取单个请求和响应详情。 |
| `agent_browser_network_har_start` | `content?: "all" \| "text" \| "none"` | 开始 HAR 录制，默认嵌入文本响应体。 |
| `agent_browser_network_har_stop` | `path?: string` | 停止 HAR 并保存；省略 path 时返回临时文件路径。 |

### 6.8 Storage、Cookie、认证与状态，共 19 个

| 工具 | 专有参数 | 作用 |
|---|---|---|
| `agent_browser_storage_get` | `storageType: "local" \| "session", key?: string` | 读取 localStorage 或 sessionStorage。 |
| `agent_browser_storage_set` | `storageType: "local" \| "session", key: string, value: string` | 写入 localStorage 或 sessionStorage。 |
| `agent_browser_storage_clear` | `storageType: "local" \| "session"` | 清空指定 Web Storage。 |
| `agent_browser_cookies_get` | 无 | 获取 cookies。 |
| `agent_browser_cookies_set` | `name: string, value: string, domain?: string, path?: string, url?: string, expires?: integer, httpOnly?: boolean, secure?: boolean, sameSite?: "Strict" \| "Lax" \| "None"` | 设置单个 cookie。 |
| `agent_browser_cookies_set_curl` | `file: string, domain?: string, url?: string` | 从 JSON、Copy-as-cURL 或 Cookie header 文件导入 cookies。 |
| `agent_browser_cookies_clear` | 无 | 清空 cookies。 |
| `agent_browser_auth_save` | `name: string, url: string, username: string, password: string, usernameSelector?: string, passwordSelector?: string, submitSelector?: string` | 保存加密 auth profile。 |
| `agent_browser_auth_login` | `name: string` | 使用已保存 auth profile 登录。 |
| `agent_browser_auth_list` | 无 | 列出 auth profile。 |
| `agent_browser_auth_show` | `name: string` | 显示 auth profile 元数据，不应返回明文密码。 |
| `agent_browser_auth_delete` | `name: string` | 删除 auth profile。 |
| `agent_browser_state_save` | `path: string` | 保存 cookies 和 storage state。 |
| `agent_browser_state_load` | `path: string` | 加载 cookies 和 storage state。 |
| `agent_browser_state_list` | 无 | 列出保存的 state。 |
| `agent_browser_state_clear` | `name?: string, all?: boolean` | 清除指定、当前或全部保存状态。 |
| `agent_browser_state_show` | `path: string` | 显示 state 文件摘要。 |
| `agent_browser_state_clean` | `olderThanDays: integer` | 删除超过指定天数的旧 state。 |
| `agent_browser_state_rename` | `oldName: string, newName: string` | 重命名保存状态。 |

state 文件可能含明文 session token。若保存到磁盘，应加入 `.gitignore`，并使用 `AGENT_BROWSER_ENCRYPTION_KEY` 配置静态加密。

### 6.9 调试、录制与剪贴板，共 15 个

| 工具 | 专有参数 | 作用 |
|---|---|---|
| `agent_browser_trace_start` | 无 | 开始 Chrome trace。 |
| `agent_browser_trace_stop` | `path?: string` | 停止 trace 并保存。 |
| `agent_browser_profiler_start` | `categories?: string` | 开始 Chrome profiler，可指定 category。 |
| `agent_browser_profiler_stop` | `path?: string` | 停止 profiler 并保存 JSON。 |
| `agent_browser_record_start` | `path: string, url?: string` | 开始视频录制，并可先导航。 |
| `agent_browser_record_stop` | 无 | 停止视频录制。 |
| `agent_browser_record_restart` | `path: string, url?: string` | 停止当前录制并用新 path 重新开始。 |
| `agent_browser_console` | `clear?: boolean` | 读取或清空 console log。 |
| `agent_browser_errors` | `clear?: boolean` | 读取或清空未捕获页面错误。 |
| `agent_browser_highlight` | `selector: string` | 在页面中高亮元素。 |
| `agent_browser_inspect` | 无 | 为活动页面打开 Chrome DevTools。 |
| `agent_browser_clipboard_read` | 无 | 读取剪贴板文本。 |
| `agent_browser_clipboard_write` | `text: string` | 写入剪贴板文本。 |
| `agent_browser_clipboard_copy` | 无 | 复制当前选区。 |
| `agent_browser_clipboard_paste` | 无 | 向当前焦点粘贴剪贴板。 |

### 6.10 React 检查，共 6 个

| 工具 | 专有参数 | 作用 |
|---|---|---|
| `agent_browser_react_tree` | `json?: boolean` | 获取 React component tree。 |
| `agent_browser_react_inspect` | `id: integer, json?: boolean` | 检查 React fiber 的 props、hooks、state 和源码位置。 |
| `agent_browser_react_renders_start` | `json?: boolean` | 开始记录 React render。 |
| `agent_browser_react_renders_stop` | `json?: boolean` | 停止并返回 React render profile。 |
| `agent_browser_react_suspense` | `onlyDynamic?: boolean, json?: boolean` | 检查 Suspense boundary。 |
| `agent_browser_remove_init_script` | `id: string` | 移除已注册 init script。 |

React 工具要求浏览器启动前启用 React DevTools hook，例如在 MCP 环境中设置：

```dotenv
AGENT_BROWSER_ENABLE=react-devtools
```

### 6.11 动作审批，共 2 个

| 工具 | 专有参数 | 作用 |
|---|---|---|
| `agent_browser_confirm` | `id: string` | 批准 action policy 产生的待审批动作。 |
| `agent_browser_deny` | `id: string` | 拒绝待审批动作。 |

只有配置 `AGENT_BROWSER_ACTION_POLICY` 或 `AGENT_BROWSER_CONFIRM_ACTIONS` 后，这组工具才会进入实际审批流程。

### 6.12 Skills、插件、诊断和内置 chat，共 13 个

| 工具 | 专有参数 | 作用 |
|---|---|---|
| `agent_browser_skills_list` | 无 | 列出与当前二进制版本匹配的内置 skills。 |
| `agent_browser_skills_get` | `names?: string[], all?: boolean, full?: boolean` | 获取一个或多个 skill 内容；`full` 包含 references 和 templates。 |
| `agent_browser_skills_path` | `name?: string` | 返回 skill 目录路径。 |
| `agent_browser_plugin_add` | `reference: string, name?: string, capabilities?: string[], global?: boolean, noManifest?: boolean` | 从 npm 或 GitHub 添加 plugin。 |
| `agent_browser_plugin_list` | 无 | 列出已配置 plugin。 |
| `agent_browser_plugin_show` | `name: string` | 显示一个 plugin。 |
| `agent_browser_plugin_run` | `name: string, requestType: string, payload?: object` | 运行 plugin 的 `command.run` 或自定义 request。 |
| `agent_browser_doctor` | `quick?: boolean, fix?: boolean, debug?: boolean, offline?: boolean, headed?: boolean, webgpu?: boolean` | 诊断安装、Chrome、daemon、配置、provider 和启动能力。 |
| `agent_browser_dashboard_start` | `port?: integer` | 启动本地 observability dashboard。 |
| `agent_browser_dashboard_stop` | 无 | 停止 dashboard。 |
| `agent_browser_install` | `withDeps?: boolean` | 安装浏览器二进制及可选系统依赖。 |
| `agent_browser_upgrade` | 无 | 升级 agent-browser。 |
| `agent_browser_chat` | `message: string, model?: string, quiet?: boolean, verbose?: boolean` | 使用 agent-browser 自带的单轮 AI chat。自研 agent harness 通常不需要暴露此工具。 |

## 7. MCP profile

profile 可以重叠。`all` 是唯一不会漏掉工具的选择。

| Profile | 工具数 | 内容 |
|---|---:|---|
| `core` | 29 | 导航、snapshot、常用交互、等待、截图、基础读取、基础 tab、eval 和 close。 |
| `network` | 9 | headers、credentials、offline、route、request 和 HAR。 |
| `state` | 27 | storage、cookies、auth、state、session、profile 和 skills。 |
| `debug` | 40 | 上传下载、trace、profiler、录制、a11y、console、diff、batch、审批、stream、plugin、doctor、dashboard、安装升级和 chat。 |
| `tabs` | 13 | 导航历史、tab、window、frame 和 dialog。 |
| `react` | 8 | React tree、inspect、render、Suspense、Vitals、pushstate 和 init script。 |
| `mobile` | 15 | 键盘、鼠标、viewport、device、geo、media、tap 和 swipe。 |
| `all` | 152 | 当前版本全部类型化 MCP 工具。 |

## 8. 推荐的上层适配规则

即使 MCP 启动时使用 `--tools all`，也不建议把 152 个 schema 同时放进每一次 LLM 上下文。更稳妥的结构是：

1. companion 与一个长生命周期 MCP 进程保持连接。
2. 所有调用传同一个 `session`。
3. agent harness 对外暴露一组稳定基础工具。
4. 低频能力通过 `agent_tools_discover(category, query, limit)` 按
   network、state、debug、tabs、React 等分类在当前任务内启用。
5. snapshot 由 harness 在任务开始及可能改变页面的动作后自动刷新，
   不作为普通模型工具重复暴露。
6. 动作返回与刷新后的 URL、snapshot 指纹对比；无法验证页面变化时标记为
   `uncertain`，不能只根据工具返回成功就假设目标已经完成。

当前后端仍以 `--tools all` 缓存完整 schema，但每轮只发送常用工具、当前任务已
发现的工具和一个分类发现入口；`eval` 留在 debug 分类中作为逃生通道。

推荐始终可用的基础集合：

```text
agent_browser_open
agent_browser_read
agent_browser_snapshot
agent_browser_click
agent_browser_fill
agent_browser_type
agent_browser_press
agent_browser_check
agent_browser_uncheck
agent_browser_select
agent_browser_scroll
agent_browser_wait_for_selector
agent_browser_wait_for_text
agent_browser_wait_for_load
agent_browser_screenshot
agent_browser_get_text
agent_browser_get_url
agent_browser_tab_list
agent_browser_tab_switch
agent_browser_frame_switch
agent_browser_frame_main
agent_browser_upload
agent_browser_drag
agent_browser_eval
```

## 9. 简单 Python 示例

安装客户端依赖：

```powershell
pip install mcp python-dotenv
```

项目 `.env`：

```dotenv
AGENT_BROWSER_AUTO_CONNECT=1
AGENT_BROWSER_SESSION=personal-agent
```

`example.py`：

```python
import asyncio
import json
import os

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def server_parameters() -> StdioServerParameters:
    env = dict(os.environ)
    env.setdefault("AGENT_BROWSER_AUTO_CONNECT", "1")
    env.setdefault("AGENT_BROWSER_SESSION", "personal-agent")

    if os.name == "nt":
        return StdioServerParameters(
            command="cmd.exe",
            args=[
                "/d",
                "/s",
                "/c",
                "agent-browser.cmd mcp --tools all",
            ],
            env=env,
        )

    return StdioServerParameters(
        command="agent-browser",
        args=["mcp", "--tools", "all"],
        env=env,
    )


def unwrap(result):
    structured = result.structuredContent or {}
    response = structured.get("response")

    if result.isError:
        text = "\n".join(
            item.text for item in result.content
            if getattr(item, "type", None) == "text"
        )
        raise RuntimeError(text or json.dumps(structured, ensure_ascii=False))

    if isinstance(response, dict) and response.get("success") is False:
        raise RuntimeError(
            response.get("error")
            or response.get("message")
            or json.dumps(response, ensure_ascii=False)
        )

    return response if response is not None else structured


async def list_all_tools(client: ClientSession):
    tools = []
    cursor = None

    while True:
        page = await client.list_tools(cursor=cursor)
        tools.extend(page.tools)
        cursor = page.nextCursor
        if cursor is None:
            return tools


async def main():
    load_dotenv()
    session_id = os.getenv("AGENT_BROWSER_SESSION", "personal-agent")

    async with stdio_client(server_parameters()) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()

            tools = await list_all_tools(client)
            print(f"discovered tools: {len(tools)}")

            current_url = unwrap(
                await client.call_tool(
                    "agent_browser_get_url",
                    arguments={"session": session_id},
                )
            )
            print(json.dumps(current_url, ensure_ascii=False, indent=2))

            snapshot = unwrap(
                await client.call_tool(
                    "agent_browser_snapshot",
                    arguments={
                        "session": session_id,
                        "interactive": True,
                        "compact": True,
                    },
                )
            )
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
```

运行：

```powershell
python example.py
```

这个示例会：

1. 由 companion 启动一个长生命周期 MCP 进程。
2. 使用 `--tools all` 分三页发现全部 152 个工具。
3. 通过 auto-connect 连接用户当前 Chrome。
4. 读取当前 URL。
5. 获取精简的可交互元素 snapshot。

如果需要改为独立 profile，只改 `.env`：

```dotenv
AGENT_BROWSER_PROFILE=D:\BrowserProfiles\personal-agent
AGENT_BROWSER_HEADED=true
AGENT_BROWSER_SESSION=personal-agent
```

同时删除 `AGENT_BROWSER_AUTO_CONNECT`。上面的 Python 和全部工具调用保持不变。
