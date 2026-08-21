import logging
import sys
from pathlib import Path

from com_domain_filter.storage import default_app_data_dir
from com_domain_filter.ui import run


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


def main() -> None:
    log_path = configure_logging()
    logging.info("COM域名筛选器启动，Python=%s", sys.version.split()[0])
    try:
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
