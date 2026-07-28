"""MCP 工具信息的轻量格式化辅助函数。"""

from typing import Any


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
