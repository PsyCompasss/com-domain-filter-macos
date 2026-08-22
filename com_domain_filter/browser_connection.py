from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .sites import create_checker


@dataclass(frozen=True)
class BrowserConnectionResult:
    ready: bool
    selected_url: str
    pages: tuple[dict[str, str], ...]
    verification_required: bool
    message: str


def open_or_connect_browser(
    site_url: str,
    profile_dir: Path,
    preferred_page_url: str = "",
) -> BrowserConnectionResult:
    """打开或重新连接专用 Chrome，验证后立即断开但保留窗口。"""
    checker = create_checker(site_url, profile_dir)
    try:
        checker.start(
            allow_launch=True,
            preferred_page_url=preferred_page_url or None,
            wait_seconds=120,
        )
        pages = tuple(checker.page_options())
        selected_url = checker.page.url if checker.page else ""
        verification = checker.verification_present()
        if verification:
            return BrowserConnectionResult(
                ready=False,
                selected_url=selected_url,
                pages=pages,
                verification_required=True,
                message="Chrome 已连接，但查询网站正在等待真人验证。完成后请再次检测连接。",
            )
        return BrowserConnectionResult(
            ready=True,
            selected_url=selected_url,
            pages=pages,
            verification_required=False,
            message="准备就绪",
        )
    finally:
        checker.close(keep_browser=True)

