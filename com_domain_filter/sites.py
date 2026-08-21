from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from .cloudflare import CloudflareChecker
from .wanwang import WanwangChecker


DEFAULT_SITES = (
    {"name": "Cloudflare", "url": "https://domains.cloudflare.com/"},
    {"name": "阿里云万网", "url": "https://wanwang.aliyun.com/domain"},
)


def checker_for_url(site_url: str):
    host = (urlparse(site_url).hostname or "").lower()
    if host == "domains.cloudflare.com":
        return CloudflareChecker
    if host == "wanwang.aliyun.com":
        return WanwangChecker
    raise ValueError("这个网址已保存，但查询程序尚未适配。当前支持 Cloudflare 和阿里云万网。")


def create_checker(site_url: str, profile_dir: Path):
    return checker_for_url(site_url)(site_url, profile_dir)
