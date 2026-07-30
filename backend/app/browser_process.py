"""agent-browser 进程环境、CLI 调用和 MCP 启动参数。"""

import asyncio
import json
import os
import subprocess
from tempfile import TemporaryFile

from dotenv import load_dotenv
from mcp import StdioServerParameters


BROWSER_SESSION_START_TIMEOUT_SECONDS = 35


def get_agent_browser_env() -> dict[str, str]:
    """构造 agent-browser 环境，并移除会被误判为启用的 false 值。"""
    env = dict(os.environ)
    auto_connect = env.get("AGENT_BROWSER_AUTO_CONNECT", "")
    if auto_connect.strip().lower() in {"", "0", "false", "no", "off"}:
        env.pop("AGENT_BROWSER_AUTO_CONNECT", None)
    env.setdefault(
        "AGENT_BROWSER_SESSION",
        os.getenv("AGENT_BROWSER_SESSION", "personal-agent"),
    )
    return env


async def run_agent_browser_cli(*arguments: str) -> None:
    """调用 agent-browser CLI，用于在 MCP 接管前显式启动浏览器会话。"""
    load_dotenv()
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
                env=get_agent_browser_env(),
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
    load_dotenv()
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
