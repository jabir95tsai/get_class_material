"""Opt-in, resumable uploads to personal NotebookLM through its browser UI.

No Canvas cookies, private RPC endpoints, or Google credential exports are used.
The browser adapter is deliberately isolated from local planning and journaling.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from .storage import sanitize_component, sha256_file

DOCUMENT_TYPES = {".pdf", ".txt", ".md", ".docx", ".pptx", ".csv", ".epub"}
MEDIA_TYPES = {".mp3", ".wav", ".m4a", ".mp4", ".aac", ".ogg", ".opus"}
MAX_FILE_BYTES = 200_000_000
STATE_NAME = ".notebooklm-import.json"


class NotebookLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class Source:
    path: Path
    relative_path: str
    digest: str
    title: str


@dataclass
class ImportPlan:
    root: Path
    sources: list[Source] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class ImportResult:
    uploaded: int = 0
    unchanged: int = 0
    notebook_url: str = ""


class BrowserAdapter(Protocol):
    def open_notebook(self, url: str | None, title: str) -> str: ...
    def ready_titles(self) -> set[str]: ...
    def source_count(self) -> int: ...
    def upload(self, path: Path, title: str) -> None: ...


def validate_notebook_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError as exc:
        raise NotebookLMError("NotebookLM 網址格式不正確。") from exc
    if (parsed.scheme != "https" or parsed.hostname not in
            {"notebooklm.google.com", "notebook.google.com"}
            or parsed.username or parsed.password or port
            or not re.fullmatch(r"/notebook/[A-Za-z0-9_-]+/?", parsed.path)):
        raise NotebookLMError("請提供 NotebookLM 筆記本的 https 網址（/notebook/…）。")
    # Strip tracking/account query strings; never persist authentication URLs.
    return f"https://{parsed.hostname}{parsed.path.rstrip('/')}"


def build_import_plan(course_dir: Path, *, include_media: bool = False) -> ImportPlan:
    root = course_dir.expanduser().resolve()
    if not root.is_dir():
        raise NotebookLMError("教材資料夾不存在；請指定一門課的資料夾。")
    plan = ImportPlan(root)
    allowed = DOCUMENT_TYPES | (MEDIA_TYPES if include_media else set())
    seen: set[str] = set()
    def fail_scan(exc):
        raise NotebookLMError("無法讀取教材資料夾，請檢查檔案存取權。") from exc

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=fail_scan):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "metadata"
                         and not (Path(directory) / d).is_symlink()
                         and not (getattr((Path(directory) / d).stat(), "st_file_attributes", 0) & 0x400)
                         and (Path(directory) / d).resolve().is_relative_to(root))
        for name in sorted(files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if name.startswith(".") or relative == "course_overview.md":
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                plan.skipped.append((relative, "連結檔案"))
                continue
            if path.suffix.lower() not in allowed:
                plan.skipped.append((relative, "未啟用的媒體或不支援的格式"))
                continue
            size = path.stat().st_size
            if not size or size > MAX_FILE_BYTES:
                plan.skipped.append((relative, "空檔案或超過 200 MB"))
                continue
            digest = sha256_file(path)
            if digest in seen:
                plan.skipped.append((relative, "內容相同"))
                continue
            seen.add(digest)
            label = sanitize_component(str(Path(relative).with_suffix("")).replace("\\", " - ")
                                       .replace("/", " - "), max_length=90)
            title = f"{label} [{digest[:12]}]{path.suffix.lower()}"
            plan.sources.append(Source(path, relative, digest, title))
    return plan


def _read_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "notebooks": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("version") != 1 or not isinstance(state.get("notebooks"), dict):
            raise ValueError
        for url, record in state["notebooks"].items():
            validate_notebook_url(url)
            if not isinstance(record.get("sources"), dict):
                raise ValueError
            for entry in record["sources"].values():
                if entry.get("status") not in {"pending", "complete"} or not isinstance(entry.get("title"), str):
                    raise ValueError
        if state.get("default_url"):
            validate_notebook_url(state["default_url"])
        return state
    except (ValueError, TypeError, AttributeError) as exc:
        raise NotebookLMError("匯入紀錄格式損壞；請先檢查 .notebooklm-import.json，避免重複上傳。") from exc


def _save_state(path: Path, state: dict) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".notebooklm-state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


@contextmanager
def _import_lock(root: Path):
    """Exclusive local lock. A crash leaves a visible, fail-closed lock."""
    path = root / ".notebooklm-import.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise NotebookLMError("此課程已有匯入工作或遺留鎖；確認沒有匯入程式後，才移除 .notebooklm-import.lock。") from exc
    try:
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def import_plan(plan: ImportPlan, browser: BrowserAdapter, *, notebook_url: str | None = None,
                max_sources: int = 50) -> ImportResult:
    if max_sources < 1:
        raise NotebookLMError("來源上限必須大於零。")
    if not plan.sources:
        return ImportResult()
    if notebook_url:
        notebook_url = validate_notebook_url(notebook_url)
    with _import_lock(plan.root):
        path = plan.root / STATE_NAME
        state = _read_state(path)
        target = notebook_url or state.get("default_url")
        url = validate_notebook_url(browser.open_notebook(target, plan.root.name))
        state["default_url"] = url
        record = state["notebooks"].setdefault(url, {"sources": {}})["sources"]
        _save_state(path, state)  # Save the notebook before starting any upload.
        ready = browser.ready_titles()
        result = ImportResult(notebook_url=url)
        pending: list[Source] = []
        for source in plan.sources:
            previous = record.get(source.digest)
            title = previous["title"] if previous else source.title
            if title in ready:
                record[source.digest] = {"title": title, "status": "complete"}
                result.unchanged += 1
            elif previous and previous["status"] == "pending":
                raise NotebookLMError(
                    f"先前上傳結果不明：{source.relative_path}。請在筆記本確認來源是否處理完成；"
                    "不會自動重送。若已確定不存在，才刪除紀錄中對應的 pending 項目後重試。")
            else:
                pending.append(source)
        _save_state(path, state)
        if browser.source_count() + len(pending) > max_sources:
            raise NotebookLMError("來源數將超過設定上限。請改用另一個筆記本／較小的教材資料夾，"
                                  "或依帳號方案提高 --notebooklm-max-sources。")
        for source in pending:
            # Stage an immutable snapshot with a deterministic, collision-resistant name.
            with tempfile.TemporaryDirectory(prefix=".notebooklm-upload-", dir=plan.root) as tmp:
                staged = Path(tmp) / source.title
                shutil.copyfile(source.path, staged)
                if sha256_file(staged) != source.digest:
                    raise NotebookLMError(f"檔案已變動，請重新執行：{source.relative_path}")
                record[source.digest] = {"title": source.title, "status": "pending"}
                _save_state(path, state)
                try:
                    browser.upload(staged, source.title)
                except Exception as exc:
                    # Do not include browser exception text: it may contain session URLs.
                    raise NotebookLMError(f"上傳尚未確認完成：{source.relative_path}。"
                                          "已保留 pending 紀錄，請檢查 NotebookLM 後再執行。") from exc
                record[source.digest]["status"] = "complete"
                _save_state(path, state)
                result.uploaded += 1
                print(f"  ✓ {source.relative_path}")
        return result


def run_import(course_dir: Path, *, profile_dir: Path, notebook_url: str | None = None,
               include_media: bool = False, dry_run: bool = False, max_sources: int = 50) -> ImportResult:
    if max_sources < 1:
        raise NotebookLMError("來源上限必須大於零。")
    if notebook_url:
        notebook_url = validate_notebook_url(notebook_url)
    plan = build_import_plan(course_dir, include_media=include_media)
    print(f"NotebookLM：{len(plan.sources)} 個候選來源；{len(plan.skipped)} 個略過。")
    for relative, reason in plan.skipped:
        print(f"  略過 {relative}：{reason}")
    if dry_run:
        for source in plan.sources:
            print(f"  候選 {source.relative_path}")
        print("預覽完成；未開啟瀏覽器、未上傳。遠端來源與帳號配額尚未核對。")
        return ImportResult()
    if not plan.sources:
        return ImportResult()
    from .notebooklm_browser import NotebookLMBrowser
    try:
        with NotebookLMBrowser(profile_dir) as browser:
            result = import_plan(plan, browser, notebook_url=notebook_url, max_sources=max_sources)
    except NotebookLMError:
        raise
    except Exception as exc:
        raise NotebookLMError("NotebookLM 瀏覽器操作未完成；請檢查登入與來源清單後重試。") from exc
    print(f"NotebookLM：新增 {result.uploaded}，已存在 {result.unchanged}。")
    return result
