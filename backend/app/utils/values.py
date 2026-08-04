"""嵌套工具结果的提取与有界裁剪。"""

from collections.abc import Collection
from typing import Any


def extract_snapshot(value: Any) -> str | None:
    """从 MCP 包装层中递归提取页面快照。"""
    if isinstance(value, dict):
        snapshot = value.get("snapshot")
        if isinstance(snapshot, str):
            return snapshot
        for key in ("data", "response"):
            found = extract_snapshot(value.get(key))
            if found is not None:
                return found
    return None


def compact_value(
    value: Any,
    *,
    string_limit: int,
    list_limit: int,
    exclude_keys: Collection[str] = (),
    label: str = "result",
) -> Any:
    """递归限制字符串和列表大小，同时保留合法的嵌套结构。"""
    if isinstance(value, str):
        if len(value) <= string_limit:
            return value
        suffix = f"\n... [{label} truncated]"
        return value[: string_limit - len(suffix)] + suffix
    if isinstance(value, dict):
        return {
            key: compact_value(
                item,
                string_limit=string_limit,
                list_limit=list_limit,
                exclude_keys=exclude_keys,
                label=label,
            )
            for key, item in value.items()
            if key not in exclude_keys
        }
    if isinstance(value, list):
        return [
            compact_value(
                item,
                string_limit=string_limit,
                list_limit=list_limit,
                exclude_keys=exclude_keys,
                label=label,
            )
            for item in value[-list_limit:]
        ]
    return value
