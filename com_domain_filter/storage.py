from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


APP_DIR_NAME = "COM域名筛选器"


def default_app_data_dir() -> Path:
    override = os.environ.get("COM_DOMAIN_FILTER_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Application Support" / APP_DIR_NAME


class HistoryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=20)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tested_domains (
                    domain TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    pattern TEXT NOT NULL,
                    prefix TEXT NOT NULL,
                    suffix TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT ''
                )
                """
            )

    def has_tested(self, domain: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM tested_domains WHERE domain = ? LIMIT 1", (domain.lower(),)
            ).fetchone()
        return row is not None

    def record(
        self,
        domain: str,
        status: str,
        checked_at: str,
        pattern: str,
        prefix: str,
        suffix: str,
        detail: str = "",
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO tested_domains
                    (domain, status, checked_at, pattern, prefix, suffix, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (domain.lower(), status, checked_at, pattern, prefix, suffix, detail),
            )
        return cursor.rowcount == 1

    def found_rows(self) -> list[tuple[str, str, str, str, str]]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT domain, checked_at, pattern, prefix, suffix
                FROM tested_domains
                WHERE status = 'exact_available'
                ORDER BY checked_at, domain
                """
            ).fetchall()

    def total_count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM tested_domains").fetchone()[0])


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, settings: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="settings-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(settings, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
