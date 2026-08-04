# Browser Agent MCP v2 迁移指南

> 状态：等待 `agent-browser` 支持 MCP `2026-07-28` 后执行  
> 目标：从 Python MCP SDK v1 和 MCP `2025-11-25` 一次性迁移到 Python MCP SDK v2 和 MCP `2026-07-28`  
> 原则：不保留旧协议兼容，不增加无关功能，不引入不必要的抽象

## 1. 当前状态

本文档编写时，项目使用：

- Python MCP SDK `1.26.0`
- `agent-browser 0.33.2`
- stdio 传输
- MCP 协议 `2025-11-25`

当前连接链路位于：

- `backend/main.py`：在 FastAPI lifespan 中创建 stdio transport 和 `ClientSession`
- `backend/app/mcp_client.py`：启动 `agent-browser`、发现工具和调用工具
- `backend/app/utils/tools.py`：读取 MCP 工具 schema 并生成 LLM 可读描述
- `backend/tests/test_agent.py`：MCP 适配层和 Agent 行为测试

当前实现使用 Python SDK v1 的以下接口：

```text
ClientSession
session.initialize()
list_tools(cursor=...)
nextCursor
inputSchema
structuredContent
isError
```

Python SDK v2 使用高层 `Client`，Python 属性统一改为 snake_case。MCP `2026-07-28` 不再使用 `initialize` 握手，而是在每个请求中携带协议版本和客户端能力。

## 2. 什么时候开始迁移

只有同时满足以下条件时才开始修改项目：

1. `agent-browser` 已发布明确支持 MCP `2026-07-28` 的版本。
2. 该版本的 stdio MCP server 能接受固定为 `2026-07-28` 的客户端。
3. `tools/list`、`tools/call` 和分页在新协议下可用。
4. 新版 `agent-browser` 的工具返回结构已确认。

不要仅因为 Python MCP SDK v2 已发布就提前迁移。客户端固定使用新协议后，无法连接只支持 `2025-11-25` 的 `agent-browser`。

### 2.1 迁移前探测

先在隔离的临时 Python 环境中安装 MCP SDK v2，不修改项目虚拟环境。使用下面的最小探测程序连接待升级的 `agent-browser`：

```python
import asyncio

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    params = StdioServerParameters(
        command="cmd.exe",
        args=["/d", "/s", "/c", "agent-browser.cmd mcp --tools all"],
    )
    async with Client(
        stdio_client(params),
        mode="2026-07-28",
    ) as client:
        print(client.protocol_version)
        page = await client.list_tools()
        print(len(page.tools), page.next_cursor)


asyncio.run(main())
```

必须看到：

```text
2026-07-28
```

如果连接失败、返回 `UnsupportedProtocolVersionError`，或只能通过 `mode="auto"` 回退成功，则不要开始迁移。

## 3. 迁移范围

本次只修改：

```text
backend/requirements.txt
backend/main.py
backend/app/mcp_client.py
backend/app/utils/tools.py
backend/tests/test_agent.py
docs/api.md
```

本次不做：

- Python MCP SDK v1 兼容
- MCP `2025-11-25` 回退
- 自动重连
- Streamable HTTP
- subscriptions、Tasks、Skills 或 MCP Apps 扩展
- Agent loop、并发模型或会话存储重构
- 前端与后端接线
- 工具审批和动态工具 profile
- 新的 MCP gateway、manager 或 repository 抽象

## 4. TDD 迁移步骤

严格按照以下顺序执行。

### 4.1 验证迁移前基线

在现有环境中运行：

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

同时记录：

- 当前测试数量和结果
- 当前 `agent-browser` 版本
- 当前协商协议版本
- `--tools all` 的实际分页数和工具数
- 一个无浏览器副作用的元工具调用结果

工具数量可能随 `agent-browser` 升级发生变化。迁移测试应验证“读取完所有分页”，不要永久断言旧版本的 152 个工具。

### 4.2 编写 v2 失败测试

先修改现有 `backend/tests/test_agent.py` 中的 MCP mock，使其使用 v2 属性：

```python
SimpleNamespace(
    input_schema={"type": "object", "properties": {}},
)
```

分页结果使用：

```python
SimpleNamespace(
    tools=[...],
    next_cursor=None,
)
```

工具调用结果使用：

```python
SimpleNamespace(
    structured_content={"response": {"success": True}},
    is_error=False,
    content=[],
)
```

至少增加或调整以下测试：

1. 多页 `list_tools()` 能完整返回所有工具。
2. 下一页使用 `next_cursor`。
3. `format_mcp_tools()` 读取 `input_schema`。
4. `unwrap()` 读取 `structured_content` 和 `is_error`。
5. `structured_content` 为字典以外的合法 JSON 值时不会报错或丢失。
6. 工具执行错误仍转换为清晰的 Python 异常。
7. 每个工具调用仍强制覆盖 `session` 参数。
8. 工具调用向 SDK 传递 `read_timeout_seconds`。
9. MCP 子进程环境不包含 `OPENAI_API_KEY`。

运行测试并确认它们在旧实现上按预期失败，然后再修改生产代码。

## 5. 代码迁移

### 5.1 更新依赖

将 `backend/requirements.txt` 中：

```text
mcp>=1.0.0
```

替换为：

```text
mcp>=2,<3
```

不要使用同时覆盖 v1 和 v2 的版本范围。

### 5.2 使用固定的新协议 Client

`backend/main.py` 不再导入或创建 `ClientSession`，改为使用高层 `Client`：

```python
from mcp import Client
from mcp.client.stdio import stdio_client
```

lifespan 中的目标结构：

```python
params = get_server_parameters()
try:
    async with Client(
        stdio_client(params),
        mode="2026-07-28",
    ) as client:
        browser = BrowserService(client)
        await browser.cache_tools()
        app.state.browser_service = browser
        yield
finally:
    await openai_client.close()
```

必须遵守：

- 使用 `mode="2026-07-28"`。
- 不使用 `mode="auto"`。
- 不调用 `initialize()`。
- 不保留 `ClientSession` 分支。
- 不捕获版本错误后回退到旧协议。

### 5.3 更新 BrowserService

`backend/app/mcp_client.py` 改为接收 v2 `Client`：

```python
from mcp import Client, StdioServerParameters


class BrowserService:
    def __init__(self, client: Client):
        self.client = client
        self.tools: list[Any] = []
```

工具分页保持现有简单循环，只替换 v2 字段：

```python
async def list_tools(self) -> list[Any]:
    tools = []
    cursor = None
    while True:
        page = await self.client.list_tools(cursor=cursor)
        tools.extend(page.tools)
        cursor = page.next_cursor
        if cursor is None:
            return tools
```

不要为分页增加新的迭代器类或缓存抽象。

工具调用改用 SDK 原生超时：

```python
result = await self.client.call_tool(
    name,
    arguments=arguments,
    read_timeout_seconds=BROWSER_TOOL_TIMEOUT_SECONDS,
)
```

删除外层 `asyncio.wait_for()` 和不再需要的 `asyncio` 导入。SDK v2 会负责请求超时和取消。

### 5.4 更新工具结果解析

只做字段迁移和必要的类型保护，不引入新的结果模型：

```python
def unwrap(result: Any) -> Any:
    structured = result.structured_content

    if result.is_error:
        text = "\n".join(
            item.text
            for item in result.content
            if getattr(item, "type", None) == "text"
        )
        raise RuntimeError(
            text
            or json.dumps(structured, ensure_ascii=False, default=str)
        )

    if isinstance(structured, dict):
        response = structured.get("response")
        if isinstance(response, dict) and response.get("success") is False:
            raise RuntimeError(
                response.get("error")
                or response.get("message")
                or json.dumps(response, ensure_ascii=False)
            )
        if response is not None:
            return response

    if structured is not None:
        return structured

    return "\n".join(
        item.text
        for item in result.content
        if getattr(item, "type", None) == "text"
    )
```

注意：

- 不要使用 `structured_content or {}`，否则会丢失 `False`、`0`、空字符串和空列表。
- MCP `2026-07-28` 允许 `structuredContent` 是任意 JSON 值，不应假设它一定是字典。
- 不要同时兼容 `structuredContent` 和 `structured_content`。
- 不要同时兼容 `isError` 和 `is_error`。

### 5.5 限制子进程环境

当前实现复制整个 `os.environ`，会把 OpenAI Key 等后端秘密传入 MCP 子进程。迁移时顺手改为最小环境：

```python
env = {
    key: value
    for key, value in os.environ.items()
    if key.startswith("AGENT_BROWSER_")
}
env.setdefault("AGENT_BROWSER_SESSION", default_browser_session_id)
```

Python MCP SDK 会在这些显式变量之外加入启动子进程所需的安全基础环境。不要手动复制 `OPENAI_*` 或整个父进程环境。

### 5.6 更新工具 schema 属性

`backend/app/utils/tools.py` 中：

```python
schema = getattr(tool, "inputSchema", None) or {}
```

改为：

```python
schema = tool.input_schema or {}
```

本次不要扩展 `_parameter_type()`，除非新版 `agent-browser` 的实际 schema 已经使用当前函数无法处理的结构，并且现有测试能够证明必须修改。

## 6. 不允许出现的兼容代码

迁移完成后，生产代码和测试中不应出现：

```text
ClientSession
initialize()
mode="auto"
nextCursor
inputSchema
structuredContent
isError
```

不要编写类似代码：

```python
getattr(result, "structured_content", getattr(result, "structuredContent", None))
```

也不要用 SDK 版本判断选择不同分支。目标是一次性迁移，而不是维护双协议适配层。

## 7. 回归验证

### 7.1 静态检查

在 `backend` 中搜索旧接口：

```powershell
rg -n "ClientSession|initialize\(\)|nextCursor|inputSchema|structuredContent|isError|mode=\"auto\"" .
```

排除迁移文档本身后，代码和测试应无结果。

### 7.2 单元测试

运行完整后端测试：

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

不得只运行新增的 MCP 测试。

### 7.3 真实 stdio 验证

使用更新后的项目代码连接新版 `agent-browser`，确认：

1. `client.protocol_version == "2026-07-28"`。
2. 没有发送 `initialize`。
3. 所有工具分页读取完成。
4. 工具名称没有缺页或重复。
5. 无副作用元工具调用成功。
6. 一个浏览器读取工具调用成功。
7. 一个浏览器操作工具调用成功，并使用指定的 `browser_session_id`。
8. 工具错误能被转换为可读异常。
9. 退出 FastAPI lifespan 后 stdio 子进程正常关闭。

### 7.4 环境泄漏验证

测试中向父进程环境放入假的：

```text
OPENAI_API_KEY=test-secret
```

然后检查 `StdioServerParameters.env`，确认：

- 包含需要的 `AGENT_BROWSER_*`
- 不包含 `OPENAI_API_KEY`
- 不包含 `OPENAI_BASE_URL`

测试不得输出真实环境变量值。

## 8. 文档同步

迁移通过后更新 `docs/api.md`：

- MCP 协议版本改为 `2026-07-28`。
- 删除 `initialize`、`ping` 和旧 session 协议说明。
- Python 示例改为 `Client(..., mode="2026-07-28")`。
- Python 属性改为 snake_case。
- 说明浏览器工具的 `session` 参数是显式浏览器状态 handle，不是已移除的 MCP 协议 session。
- 根据新版 `agent-browser` 的实际输出更新工具数量和分页示例。

不要根据旧版的 152 个工具假设新版数量不变，必须以迁移时的实际 `tools/list` 结果为准。

## 9. 验收标准

满足以下全部条件才算迁移完成：

- `backend/requirements.txt` 固定为 MCP SDK v2。
- 只使用高层 `Client`。
- 客户端固定为 MCP `2026-07-28`。
- 没有 `initialize()` 或旧协议回退。
- 所有 Python MCP 属性均为 snake_case。
- 工具分页、调用、错误和超时测试通过。
- 当前 Agent API 行为没有无关变化。
- MCP 子进程不继承 OpenAI Key。
- 真实 `agent-browser` 协商结果为 `2026-07-28`。
- 完整后端测试通过。
- `docs/api.md` 已同步到新版实际行为。

如果真实服务端验证失败，不要加入旧协议兼容代码。应撤回本次迁移提交，继续使用迁移前版本，等待 `agent-browser` 完整支持新协议后重新执行本指南。

## 10. 官方参考

- [MCP Python SDK v2](https://py.sdk.modelcontextprotocol.io/v2/)
- [Python SDK v1 到 v2 迁移指南](https://py.sdk.modelcontextprotocol.io/v2/migration/)
- [Python SDK v2 Client](https://py.sdk.modelcontextprotocol.io/v2/client/)
- [Python SDK v2 Client transports](https://py.sdk.modelcontextprotocol.io/v2/client/transports/)
- [MCP 2026-07-28 关键变更](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
- [MCP 2026-07-28 版本与兼容性](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning)
- [MCP 2026-07-28 Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)
