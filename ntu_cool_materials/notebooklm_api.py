"""NotebookLM API transport for the existing locked, resumable import planner.

Uses the optional unofficial notebooklm-py package. Login runs through
notebooklm-py's own `notebooklm login`; this module never extracts browser cookies.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import json
import os
import subprocess
import sys
from pathlib import Path

from .notebooklm import (
    ImportPlan, ImportResult, NotebookLMError, build_import_plan, import_plan, validate_notebook_url,
    STATE_NAME, _read_state,
)
from . import console
from .i18n import t
from .spreadsheet_convert import convert_spreadsheets


def api_available() -> bool:
    """Whether the optional notebooklm-py package is installed in this Python."""
    return importlib.util.find_spec("notebooklm") is not None


def install_hint() -> str:
    """How to enable NotebookLM import for the Python that is running now."""
    lines = [f'  "{sys.executable}" -m pip install "get-class-material[notebooklm]"',
             "  notebooklm login"]
    return "\n".join(lines)


def _api_home() -> Path:
    """Default API login home under the shared secrets location."""
    from .cli import _secrets_dir
    return _secrets_dir().resolve() / "notebooklm-api"


def trigger_interactive_login(storage_path: Path | None = None) -> bool:
    """Launch a browser window for Google NotebookLM login and wait for completion."""
    print(t("NotebookLM：Google 登入未就緒或已過期，正在開啟瀏覽器登入視窗...",
            "NotebookLM: Google login missing or expired; opening a browser login window..."))
    print(t("請在開啟的視窗中完成 Google 登入；登入完成後視窗會自動關閉並繼續匯入。",
            "Finish the Google login in that window; it closes by itself and the import continues."))

    cmd = [sys.executable, "-m", "notebooklm", "login"]
    if os.name == "nt":
        cmd.extend(["--browser", "chrome"])
    cmd.extend(["--browser-timeout", "300"])

    env = dict(os.environ)
    if storage_path:
        cmd.extend(["--storage", str(storage_path)])
    elif not any(name in env for name in ("NOTEBOOKLM_HOME", "NOTEBOOKLM_PROFILE", "NOTEBOOKLM_AUTH_JSON")):
        env["NOTEBOOKLM_HOME"] = str(_api_home())

    try:
        res = subprocess.run(cmd, env=env)
        if res.returncode != 0 and os.name == "nt":
            fallback_cmd = [sys.executable, "-m", "notebooklm", "login", "--browser", "msedge", "--browser-timeout", "300"]
            if storage_path:
                fallback_cmd.extend(["--storage", str(storage_path)])
            res = subprocess.run(fallback_cmd, env=env)
        if res.returncode == 0:
            print(t("NotebookLM：Google 登入完成；繼續執行匯入...", "NotebookLM: Google login complete; continuing the import..."))
            return True
        return False
    except Exception:
        return False



def remember_api_storage(storage_path: Path) -> None:
    """Record only a file location, never copy authentication data."""
    config = Path.home() / ".ntu-cool-gcm" / "notebooklm-storage.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"storage_path": str(storage_path.resolve())}), encoding="utf-8")


def resolve_api_storage(storage_path: Path | None = None) -> Path | None:
    """Reuse saved local login without overriding explicit SDK account settings."""
    if storage_path is not None:
        return storage_path
    if any(os.environ.get(name) for name in (
        "NOTEBOOKLM_HOME", "NOTEBOOKLM_PROFILE", "NOTEBOOKLM_AUTH_JSON",
    )):
        return None
    candidate = _api_home() / "profiles" / "default" / "storage_state.json"
    if candidate.is_file():
        return candidate
    config = Path.home() / ".ntu-cool-gcm" / "notebooklm-storage.json"
    try:
        saved = json.loads(config.read_text(encoding="utf-8"))["storage_path"]
        if isinstance(saved, str):
            candidate = Path(saved)
            if candidate.is_absolute() and candidate.is_file():
                return candidate
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None  # Let the SDK use its normal saved profile.


def api_client_context(storage_path: Path | None = None):
    try:
        from notebooklm import NotebookLMClient
        from notebooklm.options import ClientConfig, RetryOptions, WebBackendConfig
    except ImportError:
        raise NotebookLMError(t("尚未安裝 NotebookLM 匯入套件 notebooklm-py。請執行：\n",
                                "The NotebookLM import package notebooklm-py isn't installed. Run:\n") + install_hint()) from None
    # Mutations with an uncertain response must not be silently replayed.
    config = ClientConfig(backend=WebBackendConfig(), retry=RetryOptions(
        rate_limit_max_retries=0, server_error_max_retries=0))
    storage_path = resolve_api_storage(storage_path)
    return NotebookLMClient.from_storage(
        path=str(storage_path) if storage_path else None, config=config)


class NotebookLMAPIAdapter:
    def __init__(self, expected_titles: set[str], *, storage_path: Path | None = None,
                 interactive: bool = False, explicit_url: bool = False, context_factory=None):
        self.expected_titles = expected_titles
        self.storage_path = storage_path
        self.interactive = interactive
        self.explicit_url = explicit_url
        self.context_factory = context_factory or api_client_context

        self.notebook_id = None
        self.runner = None
        self.read_only = False

    def __enter__(self):
        self._log_levels = {}
        if self._connect():
            return self
        # One interactive Google login, then a single clean retry.
        if (console.stdin_is_interactive() and self.context_factory is api_client_context
                and trigger_interactive_login(self.storage_path) and self._connect()):
            return self
        raise NotebookLMError(t("API 登入未就緒或已過期；請先執行 notebooklm login，再重新匯入。",
                                "NotebookLM login missing or expired; run notebooklm login, then import again.")) from None

    def _connect(self) -> bool:
        """Open the API client. False on any library failure (auth, network)."""
        self.runner = asyncio.Runner()
        # Library errors may contain authentication URLs; only our own messages escape.
        for name in ("notebooklm", "httpx", "httpcore"):
            logger = logging.getLogger(name)
            self._log_levels.setdefault(name, logger.level)
            logger.setLevel(logging.CRITICAL)
        try:
            self.context = self.context_factory(self.storage_path)
            self.client = self.runner.run(self.context.__aenter__())
        except BaseException as error:
            self._close()
            if isinstance(error, (KeyboardInterrupt, SystemExit, NotebookLMError)):
                raise
            return False
        if self.context_factory is api_client_context and self.storage_path is None:
            saved = resolve_api_storage()
            if saved is not None:
                try:
                    remember_api_storage(saved)
                except OSError:
                    pass  # A read-only home must not prevent importing.
        return True


    def _close(self):
        if self.runner:
            self.runner.close()
        for name, level in self._log_levels.items():
            logging.getLogger(name).setLevel(level)

    def __exit__(self, kind, value, traceback):
        try:
            self.runner.run(self.context.__aexit__(kind, value, traceback))
        except Exception:
            if kind is None:
                raise NotebookLMError(t("API 連線關閉未完成；請重新執行以核對紀錄，勿直接重送檔案。",
                                        "The API connection didn't close cleanly; run again to reconcile the record instead of re-sending files.")) from None
        finally:
            self._close()

    def call(self, operation):
        try:
            return self.runner.run(operation)
        except Exception:
            raise NotebookLMError(t("NotebookLM API 操作未完成；請檢查登入、網路與來源狀態。未確認的上傳不會自動重送。",
                                    "A NotebookLM API call didn't complete; check login, network and source status. Unconfirmed uploads are never re-sent automatically.")) from None

    def open_notebook(self, url: str | None, title: str) -> str:
        if self.read_only and not url:
            raise NotebookLMError(t("唯讀核對需要已有筆記本網址或課程對應紀錄。",
                                    "A read-only check needs a notebook URL or a saved course mapping."))
        owned = [n for n in self.call(self.client.notebooks.list()) if n.is_owner]
        chosen = None
        if url and self.explicit_url:
            target_id = validate_notebook_url(url).rsplit("/", 1)[-1]
            chosen = next((n for n in owned if n.id == target_id), None)
            if chosen is None:
                raise NotebookLMError(t("指定筆記本不在目前帳號的「我的筆記本」中；已停止，請確認登入帳號及網址。",
                                        "That notebook isn't among this account's own notebooks; stopped. Check the signed-in account and the URL."))
        elif self.interactive and owned:
            from .notebooklm_flow import choose
            print(t("\n選擇 NotebookLM 匯入目標：", "\nPick the NotebookLM target:"))
            print(t(f"0) [新增] 建立新的課程筆記本（名稱：{title}）", f"0) [new] Create a course notebook (name: {title})"))
            default_choice = "0"
            for i, notebook in enumerate(owned, 1):
                is_previous = bool(url and validate_notebook_url(url).rsplit("/", 1)[-1] == notebook.id)
                tag = t(" ★ (上次匯入)", " ★ (last import)") if is_previous else ""
                if is_previous:
                    default_choice = str(i)
                print(f"{i}) {notebook.title}{tag}")
            prompt = t(f"請選擇 [0-{len(owned)}，預設 {default_choice}]（q 取消）：",
                       f"Choose [0-{len(owned)}, default {default_choice}] (q to cancel): ")
            answer = choose(prompt, {str(i) for i in range(len(owned) + 1)}, default_choice)
            if answer == "q":
                raise NotebookLMError(t("已取消匯入。", "Import cancelled."))
            if answer != "0":
                chosen = owned[int(answer) - 1]
            target = chosen.title if chosen else title
            print(t(f"已選定匯入目標：{target}（{'已有筆記本' if chosen else '建立新筆記本'}）",
                    f"Import target: {target} ({'existing notebook' if chosen else 'new notebook'})"))
        elif url:
            target_id = validate_notebook_url(url).rsplit("/", 1)[-1]
            chosen = next((n for n in owned if n.id == target_id), None)
            if chosen is None:
                # Here the URL came from the course's saved mapping, not the user.
                raise NotebookLMError(t("這門課先前匯入的筆記本不在目前帳號的「我的筆記本」中（可能已刪除或換了帳號）；"
                                        "已停止。請用 --guide 重新選擇，或以 --notebooklm-url 指定筆記本。",
                                        "The notebook this course was imported into isn't among this account's own "
                                        "notebooks (deleted, or a different account); stopped. Re-pick with --guide, "
                                        "or name one with --notebooklm-url."))
        if chosen is None:
            if self.read_only:
                raise NotebookLMError(t("唯讀核對不會建立筆記本。", "A read-only check never creates notebooks."))
            chosen = self.call(self.client.notebooks.create(title))
        if not chosen.id:
            raise NotebookLMError(t("API 沒有回傳筆記本 ID，已停止。", "The API returned no notebook ID; stopped."))
        self.notebook_id = chosen.id
        # Keep the old hostname when supplied so existing journals stay in one record.
        return validate_notebook_url(url if (chosen and url and validate_notebook_url(url).rsplit("/", 1)[-1] == chosen.id) else f"https://notebook.google.com/notebook/{chosen.id}")


    def _sources(self):
        return self.call(self.client.sources.list(self.notebook_id, strict=True))

    def ready_titles(self) -> set[str]:
        from notebooklm.types import SourceStatus
        rows = self._sources()
        if not self.read_only and hasattr(self.client.sources, "rename"):
            from .notebooklm import strip_legacy_hash
            renamed_any = False
            for s in rows:
                if s.title and s.title not in self.expected_titles:
                    clean = strip_legacy_hash(s.title)
                    if clean in ("announcements - announcements.md", "announcements.md", "announcements - announcements") and "announcements" in self.expected_titles:
                        clean = "announcements"
                    if clean in self.expected_titles and not any(other.title == clean for other in rows):
                        try:
                            self.call(self.client.sources.rename(self.notebook_id, s.id, clean))
                            s.title = clean
                            renamed_any = True
                            print(t(f"  ✎ 自動更新來源名稱：{clean}", f"  ✎ renamed source: {clean}"))
                        except Exception:
                            pass
            if renamed_any:
                rows = self._sources()
        expected = [s for s in rows if s.title in self.expected_titles]
        if len({s.title for s in expected}) != len(expected):
            raise NotebookLMError(t("筆記本有重複的教材檔名，請先核對來源；不會再上傳。",
                                    "The notebook has duplicate source names; check them first. Nothing more will be uploaded."))
        for source in expected:
            if source.status in (SourceStatus.PREPARING, SourceStatus.PROCESSING):
                self.call(self.client.sources.wait_until_ready(self.notebook_id, source.id, timeout=180))
            elif source.status != SourceStatus.READY:
                raise NotebookLMError(t("筆記本已有同名教材，但狀態錯誤或不明；請先核對來源，不會重送。",
                                        "A source with this name exists but its status is failed or unknown; check it first. It won't be re-sent."))
        rows = self._sources()
        if any(s.title in self.expected_titles and s.status != SourceStatus.READY for s in rows):
            raise NotebookLMError(t("教材處理狀態尚未確認完成；已停止，不會重送。",
                                    "Source processing isn't confirmed complete; stopped without re-sending."))
        return {s.title for s in rows if s.title and s.status == SourceStatus.READY}

    def source_count(self) -> int:
        return len(self._sources())

    def upload(self, path: Path, title: str) -> None:
        if self.read_only:
            raise NotebookLMError(t("唯讀核對不會上傳檔案。", "A read-only check never uploads files."))
        from notebooklm.types import SourceStatus
        source = self.call(self.client.sources.add_file(self.notebook_id, path, title=title))
        if not source.id:
            raise NotebookLMError(t("上傳未回傳來源 ID。", "The upload returned no source ID."))
        self.call(self.client.sources.wait_until_ready(self.notebook_id, source.id, timeout=180))
        rows = self._sources()
        matches = [s for s in rows if s.id == source.id and s.title == title and s.status == SourceStatus.READY]
        if len(matches) != 1:
            raise NotebookLMError(t("API 上傳後未能確認相同來源 ID、檔名與完成狀態。",
                                    "After uploading, the API couldn't confirm the same source ID, name and ready status."))


def run_api_import(course_dir: Path, *, notebook_url: str | None = None,
                   include_media: bool = True, max_sources: int = 50,
                   dry_run: bool = False, storage_path: Path | None = None,
                   interactive: bool = False, verify_only: bool = False,
                   plan: ImportPlan | None = None) -> ImportResult:
    """Import `course_dir`; pass `plan` when the caller already built (and hashed) it."""
    if max_sources < 1:
        raise NotebookLMError(t("來源上限必須大於零。", "The source limit must be greater than zero."))
    if notebook_url:
        notebook_url = validate_notebook_url(notebook_url)
    if plan is None:
        if not dry_run:
            # Folders downloaded before Excel conversion existed still get their Markdown.
            convert_spreadsheets(course_dir)
        plan = build_import_plan(course_dir, include_media=include_media)
    print(t(f"NotebookLM API：{len(plan.sources)} 個候選來源；{len(plan.skipped)} 個略過。",
            f"NotebookLM API: {len(plan.sources)} candidate source(s); {len(plan.skipped)} skipped."))
    for relative, reason in plan.skipped:
        print(t(f"  略過 {relative}：{reason}", f"  skipped {relative}: {reason}"))
    if dry_run:
        for source in plan.sources:
            print(t(f"  候選 {source.relative_path}", f"  candidate {source.relative_path}"))
        print(t("預覽完成；未登入、未連線、未上傳。", "Preview done; nothing was logged in, connected or uploaded."))
        return ImportResult()
    if not plan.sources:
        return ImportResult()
    expected_titles = {s.title for s in plan.sources}
    state_file = plan.root / STATE_NAME
    if state_file.is_file():
        try:
            stored = _read_state(state_file)
            for nb in stored.get("notebooks", {}).values():
                for item in nb.get("sources", {}).values():
                    if item.get("title"):
                        expected_titles.add(item["title"])
        except Exception:
            pass
    with NotebookLMAPIAdapter(expected_titles, storage_path=storage_path,
                             interactive=interactive, explicit_url=bool(notebook_url)) as adapter:
        if verify_only:
            adapter.read_only = True
            state = _read_state(plan.root / STATE_NAME)
            url = adapter.open_notebook(notebook_url or state.get("default_url"), plan.root.name)
            ready = adapter.ready_titles()
            records = state["notebooks"].get(url, {}).get("sources", {})
            missing = [s for s in plan.sources if s.title not in ready and records.get(s.digest, {}).get("title") not in ready]
            complete = sum(records.get(s.digest, {}).get("status") == "complete" for s in plan.sources)
            print(t(f"唯讀核對：遠端已就緒 {len(plan.sources) - len(missing)}，缺少 {len(missing)}；本機完成紀錄 {complete}。",
                    f"Read-only check: {len(plan.sources) - len(missing)} ready remotely, {len(missing)} missing; "
                    f"{complete} complete in the local record."))
            print(t("未建立筆記本、未上傳、未修改匯入紀錄。",
                    "No notebook created, nothing uploaded, import record unchanged."))
            if missing:
                raise NotebookLMError(t("遠端來源尚未全部就緒；唯讀核對已停止，未重送。",
                                        "Not every remote source is ready; read-only check stopped, nothing re-sent."))
            result = ImportResult(unchanged=len(plan.sources), notebook_url=url)
        else:
            result = import_plan(plan, adapter, notebook_url=notebook_url, max_sources=max_sources)
    print(t(f"NotebookLM API：新增 {result.uploaded}，已存在 {result.unchanged}。",
            f"NotebookLM API: {result.uploaded} added, {result.unchanged} already there."))
    print(t(f"筆記本：{result.notebook_url}", f"Notebook: {result.notebook_url}"))
    return result
