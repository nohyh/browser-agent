"""MCP 工具信息的轻量格式化辅助函数。"""

import re
from types import SimpleNamespace
from typing import Any

from app.models import ToolBehavior
from app.utils.errors import ToolValidationError


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

READ_ONLY_TOOL_NAMES = frozenset(
    {
        "agent_browser_snapshot",
        "agent_browser_read",
        "agent_browser_get_text",
        "agent_browser_get_title",
        "agent_browser_get_url",
        "agent_browser_get_value",
        "agent_browser_get_attr",
        "agent_browser_find",
        "agent_browser_tab_list",
        "agent_browser_screenshot",
        "agent_browser_diff_snapshot",
        "agent_browser_diff_screenshot",
        "agent_browser_diff_url",
        "agent_browser_a11y",
        "agent_browser_get_count",
        "agent_browser_get_box",
        "agent_browser_get_styles",
        "agent_browser_is_visible",
        "agent_browser_is_enabled",
        "agent_browser_is_checked",
        "agent_browser_vitals",
        "agent_browser_network_requests",
        "agent_browser_dialog_status",
        "agent_browser_clipboard_read",
        "agent_browser_errors",
    }
)

NAVIGATION_TOOL_NAMES = frozenset(
    {
        "agent_browser_open",
        "agent_browser_back",
        "agent_browser_forward",
        "agent_browser_reload",
        "agent_browser_hover",
        "agent_browser_focus",
        "agent_browser_scroll",
        "agent_browser_scroll_into_view",
        "agent_browser_wait_for_selector",
        "agent_browser_wait_for_text",
        "agent_browser_wait_for_url",
        "agent_browser_wait_for_load",
        "agent_browser_wait_for_download",
        "agent_browser_wait_ms",
        "agent_browser_tab_new",
        "agent_browser_tab_switch",
        "agent_browser_tab_close",
        "agent_browser_window_new",
        "agent_browser_frame_switch",
        "agent_browser_frame_main",
        "agent_browser_pushstate",
    }
)

NON_TERMINATING_MUTATION_TOOL_NAMES = frozenset(
    {
        "agent_browser_fill",
        "agent_browser_type",
        "agent_browser_keyboard_type",
        "agent_browser_keyboard_insert_text",
    }
)


def tool_input_schema(tool: Any) -> dict[str, Any] | None:
    """读取工具参数的 JSON Schema，兼容 MCP 2.0 snake_case 与旧版 camelCase。"""
    if tool is None:
        return None
    schema = getattr(tool, "input_schema", None)
    if not isinstance(schema, dict):
        schema = getattr(tool, "inputSchema", None)
    return schema if isinstance(schema, dict) else None


def tool_read_only_hint(tool: Any) -> bool | None:
    """读取 MCP 注解中的只读提示，兼容 dict 与 model 两种承载方式。"""
    annotations = getattr(tool, "annotations", None)
    if annotations is None:
        return None
    if isinstance(annotations, dict):
        hint = annotations.get("read_only_hint")
        if hint is None:
            hint = annotations.get("readOnlyHint")
    else:
        hint = getattr(annotations, "read_only_hint", None)
        if hint is None:
            hint = getattr(annotations, "readOnlyHint", None)
    return hint if isinstance(hint, bool) else None


def get_tool_behavior(name: str, tool: Any | None = None) -> ToolBehavior:
    """返回保守的工具行为；未知工具默认按潜在写操作处理。"""
    read_only_hint = tool_read_only_hint(tool)

    if read_only_hint is True or name in READ_ONLY_TOOL_NAMES:
        return ToolBehavior(
            name=name,
            category="read_only",
            changes_page=False,
            terminates_sequence=False,
            retry_policy="read_once",
        )
    if name in NAVIGATION_TOOL_NAMES or name.startswith("agent_browser_wait_for_"):
        return ToolBehavior(
            name=name,
            category="navigation",
            changes_page=True,
            terminates_sequence=True,
            retry_policy="observe",
        )
    return ToolBehavior(
        name=name,
        category="potential_write",
        changes_page=True,
        terminates_sequence=(
            name not in NON_TERMINATING_MUTATION_TOOL_NAMES
        ),
        retry_policy="none",
    )


def _json_schema_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _schema_violations(
    value: Any,
    schema: dict[str, Any],
    path: str,
) -> list[dict[str, Any]]:
    """校验 MCP 常用 JSON Schema 子集并返回可读的字段路径。"""
    if not isinstance(schema, dict):
        return []

    for keyword in ("anyOf", "oneOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list) and branches:
            matches = [
                not _schema_violations(value, branch, path)
                for branch in branches
                if isinstance(branch, dict)
            ]
            valid = sum(matches)
            if (keyword == "anyOf" and valid == 0) or (
                keyword == "oneOf" and valid != 1
            ):
                return [
                    {
                        "kind": "schema",
                        "field": path or "$",
                        "expected": keyword,
                    }
                ]

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        violations: list[dict[str, Any]] = []
        for branch in all_of:
            if isinstance(branch, dict):
                violations.extend(_schema_violations(value, branch, path))
        if violations:
            return violations

    if "const" in schema and value != schema["const"]:
        return [
            {
                "kind": "enum",
                "field": path or "$",
                "allowed": [schema["const"]],
            }
        ]
    choices = schema.get("enum")
    if isinstance(choices, list) and value not in choices:
        return [
            {
                "kind": "enum",
                "field": path or "$",
                "allowed": choices,
            }
        ]

    expected = schema.get("type")
    if isinstance(expected, list):
        type_matches = any(
            isinstance(item, str)
            and _json_schema_type_matches(value, item)
            for item in expected
        )
        if not type_matches:
            return [
                {
                    "kind": "type",
                    "field": path or "$",
                    "expected": "|".join(str(item) for item in expected),
                }
            ]
    elif isinstance(expected, str) and not _json_schema_type_matches(
        value,
        expected,
    ):
        return [
            {
                "kind": "type",
                "field": path or "$",
                "expected": expected,
            }
        ]

    violations = []
    if isinstance(value, str):
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if isinstance(min_length, int) and len(value) < min_length:
            violations.append(
                {
                    "kind": "constraint",
                    "field": path or "$",
                    "constraint": "minLength",
                    "expected": min_length,
                }
            )
        if isinstance(max_length, int) and len(value) > max_length:
            violations.append(
                {
                    "kind": "constraint",
                    "field": path or "$",
                    "constraint": "maxLength",
                    "expected": max_length,
                }
            )
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                matches_pattern = re.search(pattern, value) is not None
            except re.error:
                matches_pattern = True
            if not matches_pattern:
                violations.append(
                    {
                        "kind": "constraint",
                        "field": path or "$",
                        "constraint": "pattern",
                        "expected": pattern,
                    }
                )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        exclusive_minimum = schema.get("exclusiveMinimum")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            violations.append(
                {
                    "kind": "constraint",
                    "field": path or "$",
                    "constraint": "minimum",
                    "expected": minimum,
                }
            )
        if isinstance(maximum, (int, float)) and value > maximum:
            violations.append(
                {
                    "kind": "constraint",
                    "field": path or "$",
                    "constraint": "maximum",
                    "expected": maximum,
                }
            )
        if (
            isinstance(exclusive_minimum, (int, float))
            and value <= exclusive_minimum
        ):
            violations.append(
                {
                    "kind": "constraint",
                    "field": path or "$",
                    "constraint": "exclusiveMinimum",
                    "expected": exclusive_minimum,
                }
            )
        if (
            isinstance(exclusive_maximum, (int, float))
            and value >= exclusive_maximum
        ):
            violations.append(
                {
                    "kind": "constraint",
                    "field": path or "$",
                    "constraint": "exclusiveMaximum",
                    "expected": exclusive_maximum,
                }
            )

    if isinstance(value, dict):
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        required = schema.get("required")
        if isinstance(required, list):
            for field in required:
                if (
                    isinstance(field, str)
                    and field not in value
                    and field not in HIDDEN_PARAMETERS
                ):
                    violations.append(
                        {
                            "kind": "missing",
                            "field": _schema_path(path, field),
                        }
                    )

        additional = schema.get("additionalProperties", True)
        for field, item in value.items():
            if field in HIDDEN_PARAMETERS:
                continue
            field_path = _schema_path(path, str(field))
            field_schema = properties.get(field)
            if isinstance(field_schema, dict):
                violations.extend(
                    _schema_violations(item, field_schema, field_path)
                )
            elif additional is False:
                violations.append(
                    {"kind": "unknown", "field": field_path}
                )
            elif isinstance(additional, dict):
                violations.extend(
                    _schema_violations(item, additional, field_path)
                )

        min_properties = schema.get("minProperties")
        max_properties = schema.get("maxProperties")
        if isinstance(min_properties, int) and len(value) < min_properties:
            violations.append(
                {
                    "kind": "constraint",
                    "field": path or "$",
                    "constraint": "minProperties",
                    "expected": min_properties,
                }
            )
        if isinstance(max_properties, int) and len(value) > max_properties:
            violations.append(
                {
                    "kind": "constraint",
                    "field": path or "$",
                    "constraint": "maxProperties",
                    "expected": max_properties,
                }
            )

    if isinstance(value, list):
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(value) < min_items:
            violations.append(
                {
                    "kind": "constraint",
                    "field": path or "$",
                    "constraint": "minItems",
                    "expected": min_items,
                }
            )
        if isinstance(max_items, int) and len(value) > max_items:
            violations.append(
                {
                    "kind": "constraint",
                    "field": path or "$",
                    "constraint": "maxItems",
                    "expected": max_items,
                }
            )
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                violations.extend(
                    _schema_violations(
                        item,
                        item_schema,
                        f"{path}[{index}]" if path else f"[{index}]",
                    )
                )

    return violations


def _schema_path(parent: str, child: str) -> str:
    return f"{parent}.{child}" if parent else child


def validate_tool_arguments(
    name: str,
    arguments: dict[str, Any],
    tools: list[Any],
) -> None:
    """使用已缓存的 MCP inputSchema 做调用前的最小本地校验。"""
    tool = next(
        (candidate for candidate in tools if getattr(candidate, "name", None) == name),
        None,
    )
    schema = tool_input_schema(tool)
    if not isinstance(schema, dict):
        return
    violations = _schema_violations(arguments, schema, "")
    # 各违规类别统一收集为列表，避免 Pyright 对 object 上的 append 报错。
    details: dict[str, list[object]] = {}
    for violation in violations:
        kind = violation["kind"]
        if kind == "missing":
            details.setdefault("missing", []).append(violation["field"])
        elif kind == "unknown":
            details.setdefault("unknown", []).append(violation["field"])
        elif kind == "type":
            details.setdefault("invalid_types", []).append(
                {
                    "field": violation["field"],
                    "expected": violation["expected"],
                }
            )
        elif kind == "enum":
            details.setdefault("invalid_enums", []).append(
                {
                    "field": violation["field"],
                    "allowed": violation["allowed"],
                }
            )
        else:
            details.setdefault("invalid_constraints", []).append(
                {
                    "field": violation["field"],
                    "constraint": violation.get("constraint"),
                    "expected": violation.get("expected"),
                }
            )
    if details:
        raise ToolValidationError(name, arguments, details)


def repetition_limit(name: str) -> int:
    """为等待、滚动和分页动作保留不同的停滞容忍度。"""
    normalized = name.casefold()
    if "wait" in normalized:
        return 6
    if "scroll" in normalized:
        return 8
    if any(marker in normalized for marker in ("page", "next", "prev")):
        return 5
    return 4


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
        schema = tool_input_schema(tool) or {}
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
