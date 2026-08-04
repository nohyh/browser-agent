"""浏览器会话 HTTP 接口背后的领域服务。"""

from fastapi import HTTPException

from app.api.schemas import BrowserSessionResult, BrowserSessionStartRequest
from app.mcp_client import BrowserService, ManagedBrowserSession


def browser_session_result(managed: ManagedBrowserSession) -> BrowserSessionResult:
    """只公开会话状态，不泄露外部浏览器的 CDP 地址。"""
    return BrowserSessionResult(
        browser_session_id=managed.browser_session_id,
        mode=managed.mode,
        ownership=managed.ownership,
        status=managed.status,
        ready=managed.ready,
        url=managed.url,
    )


async def start_browser_session(
    payload: BrowserSessionStartRequest,
    browser: BrowserService,
) -> BrowserSessionResult:
    """显式启动浏览器会话，避免首次工具调用承担冷启动。"""
    try:
        managed = await browser.start_session(
            payload.browser_session_id,
            mode=payload.mode,
            cdp_url=payload.cdp_url,
            expected_url=payload.expected_url,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "browser_session_conflict",
                "browser_session_id": payload.browser_session_id,
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:
        error = str(exc) or type(exc).__name__
        raise HTTPException(
            status_code=503,
            detail={
                "code": "browser_session_start_failed",
                "browser_session_id": payload.browser_session_id,
                "message": (
                    f"Browser session '{payload.browser_session_id}' "
                    f"failed to start: {error}"
                ),
            },
        ) from exc
    return browser_session_result(managed)


async def list_browser_sessions(browser: BrowserService) -> list[BrowserSessionResult]:
    return [browser_session_result(managed) for managed in browser.list_sessions()]


async def get_browser_session(
    browser_session_id: str,
    browser: BrowserService,
) -> BrowserSessionResult:
    managed = browser.get_session(browser_session_id)
    if managed is None:
        raise HTTPException(status_code=404, detail="Browser session not found")
    return browser_session_result(managed)


async def close_browser_session(
    browser_session_id: str,
    browser: BrowserService,
) -> BrowserSessionResult:
    try:
        managed = await browser.close_session(browser_session_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Browser session not found",
        ) from exc
    return browser_session_result(managed)
