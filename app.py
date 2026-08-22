import logging
import json
import os
import sys
from pathlib import Path

from com_domain_filter.cloudflare import CloudflareChecker
from com_domain_filter.sites import create_checker
from com_domain_filter.storage import default_app_data_dir
from com_domain_filter.web_ui import run


def configure_logging() -> Path:
    log_path = default_app_data_dir() / "app.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        encoding="utf-8",
    )
    return log_path


def run_packaged_browser_restart_check(output_path: Path) -> None:
    """仅供发布前验证打包产物，不会由普通用户操作触发。"""
    profile_dir = Path(os.environ["COM_DOMAIN_FILTER_CHECK_PROFILE"])
    site_url = os.environ["COM_DOMAIN_FILTER_CHECK_SITE"]
    cycles = []
    for cycle in range(1, 4):
        checker = CloudflareChecker(site_url, profile_dir)
        checker.start()
        cycles.append(
            {
                "cycle": cycle,
                "pid": checker._chrome_pid,
                "reused": checker._reused_existing,
                "page": checker.page.url,
            }
        )
        checker.close(keep_browser=True)
    report = {"cycles": cycles}
    check_domain = os.environ.get("COM_DOMAIN_FILTER_CHECK_DOMAIN")
    if check_domain:
        checker = create_checker(site_url, profile_dir)
        checker.start()
        result = checker.query(check_domain)
        report["query"] = {
            "domain": result.query,
            "status": result.status,
            "detail": result.detail,
        }
        checker.close(keep_browser=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    log_path = configure_logging()
    logging.info("COM域名筛选器启动，Python=%s", sys.version.split()[0])
    try:
        packaged_check_output = os.environ.get("COM_DOMAIN_FILTER_CHECK_OUTPUT")
        if packaged_check_output:
            run_packaged_browser_restart_check(Path(packaged_check_output))
            return
        run()
    except Exception as exc:
        logging.exception("软件发生未处理异常")
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("COM域名筛选器无法启动", f"{exc}\n\n错误日志：{log_path}")
            root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    main()
