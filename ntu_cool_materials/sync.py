from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .canvas_client import CanvasAPIError, CanvasClient
from .storage import ManifestStore, course_directory_name, sanitize_component, sha256_file


@dataclass
class SyncStats:
    course_id: str
    course_name: str
    downloaded: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0
    module_snapshots: int = 0


def sync_course_materials(
    *,
    client: CanvasClient,
    course: dict[str, Any],
    output_dir: Path,
    store: ManifestStore,
    include_modules: bool = True,
    dry_run: bool = False,
) -> SyncStats:
    course_id = str(course["id"])
    course_name = _course_name(course)
    course_dir = output_dir / course_directory_name(course)
    stats = SyncStats(course_id=course_id, course_name=course_name)

    if include_modules:
        modules = _load_modules_with_items(client, course_id)
        stats.module_snapshots = 1
        if not dry_run:
            course_dir.mkdir(parents=True, exist_ok=True)
            (course_dir / "modules.json").write_text(
                json.dumps(modules, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    for file_info in client.list_course_files(course_id):
        target_path = _target_path(course_dir, file_info)
        skip_reason = _skip_reason(file_info)
        if skip_reason:
            stats.skipped += 1
            if not dry_run:
                store.upsert_file(
                    file_info=file_info,
                    course_id=course_id,
                    course_name=course_name,
                    local_path=target_path,
                    sha256=None,
                    skipped_reason=skip_reason,
                )
            continue

        if not store.needs_download(file_info, target_path):
            stats.unchanged += 1
            continue

        if dry_run:
            stats.downloaded += 1
            continue

        try:
            client.download_file_url(str(file_info["url"]), target_path)
            store.upsert_file(
                file_info=file_info,
                course_id=course_id,
                course_name=course_name,
                local_path=target_path,
                sha256=sha256_file(target_path),
            )
            stats.downloaded += 1
        except (CanvasAPIError, OSError, KeyError) as exc:
            stats.failed += 1
            print(f"[warn] failed to download {file_info.get('display_name')}: {exc}")

    return stats


def _load_modules_with_items(client: CanvasClient, course_id: str) -> list[dict[str, Any]]:
    modules = client.list_modules(course_id)
    for module in modules:
        if "items" not in module and module.get("id"):
            module["items"] = client.list_module_items(course_id, str(module["id"]))
    return modules


def _target_path(course_dir: Path, file_info: dict[str, Any]) -> Path:
    file_id = str(file_info.get("id") or "unknown")
    display_name = file_info.get("display_name") or file_info.get("filename") or f"file-{file_id}"
    filename = sanitize_component(f"{file_id}-{display_name}", fallback=f"file-{file_id}", max_length=180)
    return course_dir / "files" / filename


def _skip_reason(file_info: dict[str, Any]) -> str | None:
    if file_info.get("locked"):
        return "locked"
    if file_info.get("hidden"):
        return "hidden"
    if file_info.get("hidden_for_user"):
        return "hidden_for_user"
    if not file_info.get("url"):
        return "missing_url"
    return None


def _course_name(course: dict[str, Any]) -> str:
    return str(course.get("name") or course.get("course_code") or course.get("id") or "course")
