"""agent-browser 进程环境、CLI 调用和 MCP 启动参数。"""

import asyncio
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryFile
from urllib.request import urlopen

from mcp import StdioServerParameters


BROWSER_SESSION_START_TIMEOUT_SECONDS = 35
# Chrome 系浏览器常用的远程调试端口，按命中概率排序。
CDP_DISCOVERY_PORTS = (9222, 9223, 9333, 9229, 21222)
CDP_DISCOVERY_TIMEOUT_SECONDS = 1.2
# 独立 profile 落在后端目录内，避免污染用户真实浏览器数据。
ISOLATED_PROFILE_ROOT = Path(__file__).parent.parent / ".profiles"


def get_agent_browser_env(
    overrides: dict[str, str | None] | None = None,
) -> dict[str, str]:
    """构造 agent-browser 环境，并移除会被误判为启用的 false 值。"""
    env = dict(os.environ)
    auto_connect = env.get("AGENT_BROWSER_AUTO_CONNECT", "")
    if auto_connect.strip().lower() in {"", "0", "false", "no", "off"}:
        env.pop("AGENT_BROWSER_AUTO_CONNECT", None)
    env.setdefault("AGENT_BROWSER_SESSION", "personal-agent")
    # 值为 None 表示显式删除，避免上层 .env 的模式设置泄漏到本次启动。
    for key, value in (overrides or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def isolated_profile_env(browser_session_id: str) -> dict[str, str | None]:
    """独立会话使用后端自己的 profile 目录，并强制有头模式。"""
    profile_dir = ISOLATED_PROFILE_ROOT / browser_session_id
    profile_dir.mkdir(parents=True, exist_ok=True)
    return {
        "AGENT_BROWSER_PROFILE": str(profile_dir),
        "AGENT_BROWSER_HEADED": "true",
        "AGENT_BROWSER_AUTO_CONNECT": None,
        "AGENT_BROWSER_CDP": None,
    }


def existing_browser_env(cdp_url: str) -> dict[str, str | None]:
    """接管现有浏览器时只允许 CDP 上下文，不能同时给出 profile。"""
    return {
        "AGENT_BROWSER_CDP": cdp_url,
        "AGENT_BROWSER_AUTO_CONNECT": None,
        "AGENT_BROWSER_PROFILE": None,
        "AGENT_BROWSER_HEADED": None,
    }


def _probe_cdp_port(port: int) -> str | None:
    """探测单个本地端口是否是可用的 CDP 端点。"""
    try:
        with urlopen(
            f"http://127.0.0.1:{port}/json/version",
            timeout=CDP_DISCOVERY_TIMEOUT_SECONDS,
        ) as response:
            payload = json.loads(
                response.read().decode("utf-8", errors="replace")
            )
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None
    websocket_url = payload.get("webSocketDebuggerUrl")
    if isinstance(websocket_url, str) and websocket_url.startswith("ws"):
        return websocket_url
    return f"http://127.0.0.1:{port}"


async def discover_cdp_url(
    ports: tuple[int, ...] | None = None,
) -> str | None:
    """并发探测常见端口，返回用户当前浏览器的 CDP 地址。"""
    candidates = ports or CDP_DISCOVERY_PORTS
    configured = os.getenv("AGENT_BROWSER_CDP", "").strip()
    if configured:
        return configured

    results = await asyncio.gather(
        *(asyncio.to_thread(_probe_cdp_port, port) for port in candidates)
    )
    for found in results:
        if found:
            return found
    return None


async def run_agent_browser_cli(
    *arguments: str,
    env_overrides: dict[str, str | None] | None = None,
) -> None:
    """调用 agent-browser CLI，用于在 MCP 接管前显式启动浏览器会话。"""
    if os.name == "nt":
        command = (
            "cmd.exe",
            "/d",
            "/s",
            "/c",
            "agent-browser.cmd",
            *arguments,
        )
    else:
        command = ("agent-browser", *arguments)

    # Chrome 会继承标准输出句柄；使用 PIPE 会让 Python 等待浏览器退出。
    with TemporaryFile() as stdout_file, TemporaryFile() as stderr_file:
        try:
            process = await asyncio.to_thread(
                subprocess.run,
                command,
                check=False,
                stdout=stdout_file,
                stderr=stderr_file,
                env=get_agent_browser_env(env_overrides),
                timeout=BROWSER_SESSION_START_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                "agent-browser session startup timed out after "
                f"{BROWSER_SESSION_START_TIMEOUT_SECONDS} seconds"
            ) from exc

        stdout_file.seek(0)
        stderr_file.seek(0)
        captured_stdout = stdout_file.read().decode(
            "utf-8",
            errors="replace",
        )
        captured_stderr = stderr_file.read().decode(
            "utf-8",
            errors="replace",
        )

    stdout = getattr(process, "stdout", None) or captured_stdout
    stderr = getattr(process, "stderr", None) or captured_stderr
    payload = None
    if stdout.strip():
        try:
            payload = json.loads(stdout.strip().splitlines()[-1])
        except json.JSONDecodeError:
            pass

    if isinstance(payload, dict) and payload.get("success") is False:
        raise RuntimeError(
            payload.get("error")
            or "agent-browser CLI reported an unknown error"
        )
    if process.returncode != 0:
        detail = stderr.strip() or stdout.strip()
        raise RuntimeError(
            f"agent-browser CLI exited with code {process.returncode}"
            + (f": {detail}" if detail else "")
        )


def get_server_parameters() -> StdioServerParameters:
    """根据当前操作系统构造 agent-browser MCP 的启动参数。"""
    env = get_agent_browser_env()

    if os.name == "nt":
        return StdioServerParameters(
            command="cmd.exe",
            args=["/d", "/s", "/c", "agent-browser.cmd mcp --tools all"],
            env=env,
        )

    return StdioServerParameters(
        command="agent-browser",
        args=["mcp", "--tools", "all"],
        env=env,
    )
