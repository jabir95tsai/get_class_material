from __future__ import annotations

import hashlib
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def course_directory_name(course: dict[str, Any]) -> str:
    """Canonical per-course directory name used by sync, announcements, etc."""
    course_id = str(course.get("id") or "")
    name = str(course.get("name") or course.get("course_code") or course_id or "course")
    return sanitize_component(f"{name} ({course_id})" if course_id else name, max_length=160)


def sanitize_component(value: str | None, fallback: str = "untitled", max_length: int = 120) -> str:
    raw = unicodedata.normalize("NFKC", value or "").strip()
    cleaned = "".join("_" if _is_unsafe_filename_char(char) else char for char in raw)
    cleaned = " ".join(cleaned.split()).strip(" .")

    if not cleaned:
        cleaned = fallback

    if cleaned.upper() in WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"

    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" .")

    return cleaned or fallback


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _is_unsafe_filename_char(char: str) -> bool:
    return ord(char) < 32 or char in '<>:"/\\|?*'


@dataclass(frozen=True)
class StoredFile:
    file_id: str
    course_id: str
    updated_at: str | None
    size: int | None
    local_path: Path
    sha256: str | None


class ManifestStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.db_path)
        self._connection.row_factory = sqlite3.Row
        self._ensure_schema()

    def close(self) -> None:
        self._connection.close()

    def get_file(self, file_id: str) -> StoredFile | None:
        row = self._connection.execute(
            """
            SELECT file_id, course_id, updated_at, size, local_path, sha256
            FROM files
            WHERE file_id = ?
            """,
            (str(file_id),),
        ).fetchone()
        if row is None:
            return None

        return StoredFile(
            file_id=row["file_id"],
            course_id=row["course_id"],
            updated_at=row["updated_at"],
            size=row["size"],
            local_path=Path(row["local_path"]),
            sha256=row["sha256"],
        )

    def needs_download(self, file_info: dict[str, Any], target_path: Path) -> bool:
        file_id = str(file_info.get("id") or "")
        if not file_id or not target_path.exists():
            return True

        stored = self.get_file(file_id)
        if stored is None:
            return True

        updated_at = file_info.get("updated_at")
        size = _to_int(file_info.get("size"))
        return stored.updated_at != updated_at or stored.size != size

    def upsert_file(
        self,
        *,
        file_info: dict[str, Any],
        course_id: str,
        course_name: str,
        local_path: Path,
        sha256: str | None,
        skipped_reason: str | None = None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO files (
                file_id, course_id, course_name, display_name, filename, content_type,
                size, updated_at, canvas_url, local_path, sha256, downloaded_at,
                skipped_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_id) DO UPDATE SET
                course_id = excluded.course_id,
                course_name = excluded.course_name,
                display_name = excluded.display_name,
                filename = excluded.filename,
                content_type = excluded.content_type,
                size = excluded.size,
                updated_at = excluded.updated_at,
                canvas_url = excluded.canvas_url,
                local_path = excluded.local_path,
                sha256 = excluded.sha256,
                downloaded_at = excluded.downloaded_at,
                skipped_reason = excluded.skipped_reason
            """,
            (
                str(file_info.get("id") or ""),
                str(course_id),
                course_name,
                file_info.get("display_name"),
                file_info.get("filename"),
                file_info.get("content-type") or file_info.get("content_type"),
                _to_int(file_info.get("size")),
                file_info.get("updated_at"),
                file_info.get("url"),
                str(local_path),
                sha256,
                utc_now_iso(),
                skipped_reason,
            ),
        )
        self._connection.commit()

    def _ensure_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                file_id TEXT PRIMARY KEY,
                course_id TEXT NOT NULL,
                course_name TEXT NOT NULL,
                display_name TEXT,
                filename TEXT,
                content_type TEXT,
                size INTEGER,
                updated_at TEXT,
                canvas_url TEXT,
                local_path TEXT NOT NULL,
                sha256 TEXT,
                downloaded_at TEXT NOT NULL,
                skipped_reason TEXT
            )
            """
        )
        self._connection.commit()


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
