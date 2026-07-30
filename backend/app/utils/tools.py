"""MCP 工具信息的轻量格式化辅助函数。"""

from types import SimpleNamespace
from typing import Any


# 这些能力由 Agent 或后端生命周期独占，不能暴露给模型。
INTERNAL_TOOL_NAMES = frozenset(
    {
        "agent_browser_snapshot",
        "agent_browser_tools_profiles",
        "agent_browser_close",
        "agent_browser_connect",
        "agent_browser_session",
        "agent_browser_session_list",
        "agent_browser_session_id",
        "agent_browser_session_info",
    }
)

# 高频工具始终直接暴露；snapshot 由 Agent 自动维护。
COMMON_TOOL_NAMES = frozenset(
    {
        "agent_browser_open",
        "agent_browser_read",
        "agent_browser_back",
        "agent_browser_forward",
        "agent_browser_reload",
        "agent_browser_click",
        "agent_browser_dblclick",
        "agent_browser_fill",
        "agent_browser_type",
        "agent_browser_press",
        "agent_browser_hover",
        "agent_browser_focus",
        "agent_browser_select",
        "agent_browser_check",
        "agent_browser_uncheck",
        "agent_browser_scroll",
        "agent_browser_scroll_into_view",
        "agent_browser_wait_for_selector",
        "agent_browser_wait_for_text",
        "agent_browser_wait_for_url",
        "agent_browser_wait_for_load",
        "agent_browser_get_text",
        "agent_browser_get_title",
        "agent_browser_get_url",
        "agent_browser_get_value",
        "agent_browser_get_attr",
        "agent_browser_find",
        "agent_browser_tab_list",
        "agent_browser_tab_new",
        "agent_browser_tab_switch",
        "agent_browser_tab_close",
    }
)

# 非常用工具的分组是静态接口，不根据名称或描述动态推断。
TOOL_GROUPS = {
    "agent_tools_get_page": {
        "description": "Get uncommon page inspection and navigation tools.",
        "tools": (
            "agent_browser_screenshot",
            "agent_browser_pushstate",
            "agent_browser_get_html",
            "agent_browser_get_count",
            "agent_browser_get_box",
            "agent_browser_get_styles",
            "agent_browser_get_cdp_url",
            "agent_browser_is_visible",
            "agent_browser_is_enabled",
            "agent_browser_is_checked",
            "agent_browser_a11y",
            "agent_browser_diff_snapshot",
            "agent_browser_diff_screenshot",
            "agent_browser_diff_url",
            "agent_browser_vitals",
        ),
    },
    "agent_tools_get_input": {
        "description": "Get uncommon keyboard, mouse, touch and wait tools.",
        "tools": (
            "agent_browser_keydown",
            "agent_browser_keyup",
            "agent_browser_keyboard_type",
            "agent_browser_keyboard_insert_text",
            "agent_browser_drag",
            "agent_browser_mouse_move",
            "agent_browser_mouse_down",
            "agent_browser_mouse_up",
            "agent_browser_mouse_wheel",
            "agent_browser_tap",
            "agent_browser_swipe",
            "agent_browser_wait_ms",
            "agent_browser_wait_for_function",
        ),
    },
    "agent_tools_get_files": {
        "description": "Get PDF, upload and download tools.",
        "tools": (
            "agent_browser_pdf",
            "agent_browser_upload",
            "agent_browser_download",
            "agent_browser_wait_for_download",
        ),
    },
    "agent_tools_get_tabs": {
        "description": "Get uncommon window, frame and dialog tools.",
        "tools": (
            "agent_browser_window_new",
            "agent_browser_frame_switch",
            "agent_browser_frame_main",
            "agent_browser_dialog_status",
            "agent_browser_dialog_accept",
            "agent_browser_dialog_dismiss",
        ),
    },
    "agent_tools_get_device": {
        "description": "Get browser settings and device emulation tools.",
        "tools": (
            "agent_browser_set_viewport",
            "agent_browser_set_device",
            "agent_browser_set_geo",
            "agent_browser_set_offline",
            "agent_browser_set_headers",
            "agent_browser_set_credentials",
            "agent_browser_set_media",
            "agent_browser_device",
        ),
    },
    "agent_tools_get_network": {
        "description": "Get network request, routing and HAR tools.",
        "tools": (
            "agent_browser_network_route",
            "agent_browser_network_unroute",
            "agent_browser_network_requests",
            "agent_browser_network_request",
            "agent_browser_network_har_start",
            "agent_browser_network_har_stop",
        ),
    },
    "agent_tools_get_state": {
        "description": "Get storage, cookie, authentication and state tools.",
        "tools": (
            "agent_browser_storage_get",
            "agent_browser_storage_set",
            "agent_browser_storage_clear",
            "agent_browser_cookies_get",
            "agent_browser_cookies_set",
            "agent_browser_cookies_set_curl",
            "agent_browser_cookies_clear",
            "agent_browser_auth_save",
            "agent_browser_auth_login",
            "agent_browser_auth_list",
            "agent_browser_auth_show",
            "agent_browser_auth_delete",
            "agent_browser_state_save",
            "agent_browser_state_load",
            "agent_browser_state_list",
            "agent_browser_state_clear",
            "agent_browser_state_show",
            "agent_browser_state_clean",
            "agent_browser_state_rename",
        ),
    },
    "agent_tools_get_debug": {
        "description": "Get evaluation, tracing, recording and clipboard tools.",
        "tools": (
            "agent_browser_eval",
            "agent_browser_batch",
            "agent_browser_trace_start",
            "agent_browser_trace_stop",
            "agent_browser_profiler_start",
            "agent_browser_profiler_stop",
            "agent_browser_record_start",
            "agent_browser_record_stop",
            "agent_browser_record_restart",
            "agent_browser_console",
            "agent_browser_errors",
            "agent_browser_highlight",
            "agent_browser_inspect",
            "agent_browser_clipboard_read",
            "agent_browser_clipboard_write",
            "agent_browser_clipboard_copy",
            "agent_browser_clipboard_paste",
        ),
    },
    "agent_tools_get_react": {
        "description": "Get React component inspection tools.",
        "tools": (
            "agent_browser_react_tree",
            "agent_browser_react_inspect",
            "agent_browser_react_renders_start",
            "agent_browser_react_renders_stop",
            "agent_browser_react_suspense",
            "agent_browser_remove_init_script",
        ),
    },
    "agent_tools_get_system": {
        "description": "Get profiles, streaming, approval and diagnostic tools.",
        "tools": (
            "agent_browser_profiles",
            "agent_browser_stream_enable",
            "agent_browser_stream_disable",
            "agent_browser_stream_status",
            "agent_browser_confirm",
            "agent_browser_deny",
            "agent_browser_skills_list",
            "agent_browser_skills_get",
            "agent_browser_skills_path",
            "agent_browser_plugin_add",
            "agent_browser_plugin_list",
            "agent_browser_plugin_show",
            "agent_browser_plugin_run",
            "agent_browser_doctor",
            "agent_browser_dashboard_start",
            "agent_browser_dashboard_stop",
            "agent_browser_install",
            "agent_browser_upgrade",
            "agent_browser_chat",
        ),
    },
}
TOOL_GETTER_NAMES = frozenset(TOOL_GROUPS)
REGISTERED_TOOL_NAMES = COMMON_TOOL_NAMES | frozenset(
    tool_name
    for group in TOOL_GROUPS.values()
    for tool_name in group["tools"]
)

# 这些字段由 companion 或后端统一管理，无需占用 LLM 上下文。
HIDDEN_PARAMETERS = {
    "session",
    "namespace",
    "restore",
    "restoreSave",
    "restoreCheckUrl",
    "restoreCheckText",
    "restoreCheckFn",
    "allowedDomains",
    "extraArgs",
    "timeoutMs",
}


def _parameter_type(schema: dict[str, Any]) -> str:
    """把 JSON Schema 中的常见类型压缩成适合 prompt 的短文本。"""
    if "enum" in schema:
        return "|".join(str(value) for value in schema["enum"])
    if schema.get("type") == "array":
        return f"{_parameter_type(schema.get('items') or {})}[]"
    if "anyOf" in schema:
        return "|".join(_parameter_type(item) for item in schema["anyOf"])
    return schema.get("type", "any")


def format_mcp_tools(mcp_tools: list[Any]) -> str:
    """将全部 MCP 工具压缩成供 LLM 阅读的名称、参数签名和描述。"""
    lines = []
    for tool in mcp_tools:
        schema = getattr(tool, "inputSchema", None) or {}
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        parameters = []
        for name, parameter_schema in properties.items():
            if name in HIDDEN_PARAMETERS:
                continue
            optional = "" if name in required else "?"
            # 参数描述直接复用 MCP schema，避免模型只能根据参数名猜测语义。
            description = parameter_schema.get("description") or ""
            description_suffix = f"{{{description}}}" if description else ""
            parameters.append(
                f"{name}{optional}:{_parameter_type(parameter_schema)}"
                f"{description_suffix}"
            )
        signature = "; ".join(parameters)
        description = getattr(tool, "description", "") or ""
        lines.append(f"{tool.name}({signature}): {description}")
    return "\n".join(lines)

#让元工具和常用工具也能够使用统一的格式
def _getter_tool(name: str) -> Any:
    group = TOOL_GROUPS[name]
    return SimpleNamespace(
        name=name,
        description=group["description"],
        inputSchema={
            "type": "object",
            "properties": {},
        },
    )


def select_mcp_tools_for_llm(mcp_tools: list[Any]) -> list[Any]:
    """始终给模型固定的常用工具和无参数工具组入口。"""
    visible = [
        tool
        for tool in mcp_tools
        if tool.name in COMMON_TOOL_NAMES
    ]
    visible.extend(_getter_tool(name) for name in TOOL_GROUPS)
    return visible

#获取指定非常用工具组的所有工具信息
def get_tool_group(
    mcp_tools: list[Any],
    getter_name: str,
) -> list[dict[str, str]]:
    """按固定分组返回当前 MCP 实际提供的完整工具签名。"""
    group = TOOL_GROUPS.get(getter_name)
    if group is None:
        raise ValueError(f"unknown tool getter: {getter_name}")
    tools_by_name = {tool.name: tool for tool in mcp_tools}
    return [
        {
            "name": tool_name,
            "description": format_mcp_tools(
                [tools_by_name[tool_name]]
            )[:800],
        }
        for tool_name in group["tools"]
        if tool_name in tools_by_name
    ]
