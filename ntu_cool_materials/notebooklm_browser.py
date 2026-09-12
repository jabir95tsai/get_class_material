"""Personal NotebookLM UI adapter (English and Traditional Chinese).

This is browser automation, not an official Google API. Unknown layouts fail
closed. Keep UI selectors here so the local import journal remains independent.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from .notebooklm import NotebookLMError, validate_notebook_url

HOME = "https://notebooklm.google.com/"
CREATE = re.compile(r"Create (new )?notebook|New notebook|建立.*筆記本|新增.*筆記本", re.I)
ADD = re.compile(r"Add sources?|新增來源|加入來源", re.I)
UPLOAD = re.compile(r"Upload files?|Choose files?|上傳檔案|選擇檔案", re.I)
ROWS = ".single-source-container"
TITLES = ".source-title, .source-item-title"
ERROR = re.compile(r"error|failed|unsupported|錯誤|失敗|不支援", re.I)
BUSY = re.compile(r"progress_activity|pending|processing|uploading|處理中|上傳中", re.I)


class NotebookLMBrowser:
    def __init__(self, profile_dir: Path, *, login_timeout: int = 600, upload_timeout: int = 180):
        self.profile_dir = profile_dir.expanduser().resolve()
        self.login_timeout = login_timeout
        self.upload_timeout = upload_timeout
        self._pw = None
        self.context = None
        self.page = None
        self._created_empty = False

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self._pw = sync_playwright().start()
            self.context = self._pw.chromium.launch_persistent_context(
                str(self.profile_dir), headless=False, locale="en-US",
            )
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.set_default_timeout(15000)
            return self
        except Exception as exc:
            self.__exit__(None, None, None)
            raise NotebookLMError("無法開啟 NotebookLM 專用瀏覽器。請先執行 python -m playwright install chromium，"
                                  "並確認同一個專用設定檔沒有被其他程式使用。") from exc

    def __exit__(self, *_):
        try:
            if self.context:
                self.context.close()
        finally:
            if self._pw:
                self._pw.stop()

    def _button(self, pattern):
        matches = self.page.get_by_role("button", name=pattern)
        for index in range(matches.count()):
            candidate = matches.nth(index)
            if candidate.is_visible() and candidate.is_enabled():
                return candidate
        return None

    def open_notebook(self, url: str | None, title: str) -> str:
        target = validate_notebook_url(url) if url else HOME
        try:
            self.page.goto(target, wait_until="domcontentloaded")
            print("請在開啟的專用瀏覽器完成 Google 登入；登入狀態會留在本機設定檔。"
                  "若 Google 拒絕此瀏覽器登入，請停止匯入；不要提供密碼給程式。")
            deadline = time.monotonic() + self.login_timeout
            while time.monotonic() < deadline:
                if self.page.is_closed():
                    raise NotebookLMError("NotebookLM 瀏覽器已關閉。")
                # Never perform notebook actions on an account/consent page.
                from urllib.parse import urlsplit
                if urlsplit(self.page.url).hostname in {"notebooklm.google.com", "notebook.google.com"}:
                    if self._button(ADD) or self._button(CREATE):
                        break
                self.page.wait_for_timeout(500)
            else:
                raise NotebookLMError("Google 登入尚未完成或 NotebookLM 介面無法辨識。")
            if url:
                # A login redirect can land on the home page instead of the target.
                self.page.goto(target, wait_until="domcontentloaded")
                self.page.get_by_role("button", name=ADD).first.wait_for(state="visible")
                actual = validate_notebook_url(self.page.url)
                if actual.rsplit("/", 1)[-1] != target.rsplit("/", 1)[-1]:
                    raise NotebookLMError("未能開啟指定筆記本，請確認帳號與存取權。")
            else:
                create = self._button(CREATE)
                if create is None:
                    raise NotebookLMError("找不到建立筆記本按鈕。請手動建立後傳入 --notebooklm-url。")
                create.click()
                self.page.wait_for_url(re.compile(r"https://(?:notebooklm|notebook)\.google\.com/notebook/"))
                self._created_empty = True
            # Creating a notebook opens the source dialog; close it before inventory.
            self.page.keyboard.press("Escape")
            if self._created_empty:
                # Rename only the dedicated title field, never a generic textbox.
                name = self.page.get_by_role("textbox", name=re.compile(r"Notebook title|筆記本名稱|筆記本標題", re.I))
                if name.count() == 1 and name.is_visible():
                    name.fill(title)
                    name.press("Tab")
                else:
                    print("筆記本已建立；未找到名稱欄位，可在網頁自行改名。課程對應仍會保存。")
            return validate_notebook_url(self.page.url)
        except NotebookLMError:
            raise
        except Exception as exc:
            raise NotebookLMError("無法開啟 NotebookLM 筆記本；請檢查登入、權限或網頁介面。"
                                  "若曾建立空白筆記本，可用 --notebooklm-url 指定它以免再次建立。") from exc

    def _rows(self):
        rows = self.page.locator(ROWS)
        if rows.count():
            return rows
        # An empty result must not be mistaken for an empty notebook on a changed UI.
        empty = self.page.get_by_text(re.compile(r"^0 sources?$|^0 個來源$|Saved sources will appear here|儲存的來源.*顯示", re.I))
        if self._created_empty or (empty.count() and empty.first.is_visible()):
            return rows
        raise NotebookLMError("無法可靠讀取來源清單。網頁可能仍在載入或介面已變動，已停止以避免重複上傳。")

    def _ready_title(self, row) -> str | None:
        # A filename alone is not success: upload/processing/error states are excluded.
        if row.locator('[role="progressbar"], mat-progress-spinner, mat-spinner, [aria-busy="true"]').count():
            return None
        text = row.inner_text()
        icons = row.locator("mat-icon").all_text_contents()
        if any(ERROR.search(icon) or BUSY.search(icon) for icon in icons):
            return None
        checkboxes = row.get_by_role("checkbox")
        if not checkboxes.count() or not checkboxes.first.is_enabled():
            return None
        titles = row.locator(TITLES)
        if titles.count() == 1:
            return titles.inner_text().strip()
        # Generated filenames are distinctive; use an exact line only.
        for line in text.splitlines():
            if re.search(r" \[[0-9a-f]{12}\]\.[a-z0-9]+$", line.strip()):
                return line.strip()
        return None

    def ready_titles(self) -> set[str]:
        rows = self._rows()
        return {title for i in range(rows.count()) if (title := self._ready_title(rows.nth(i)))}

    def source_count(self) -> int:
        return self._rows().count()

    def upload(self, path: Path, title: str) -> None:
        add = self._button(ADD)
        if add is None:
            raise NotebookLMError("找不到新增來源按鈕。")
        add.click()
        inputs = self.page.locator('input[type="file"]')
        if inputs.count() == 1:
            inputs.set_input_files(str(path))
        else:
            upload = self._button(UPLOAD)
            if upload is None:
                raise NotebookLMError("找不到檔案上傳介面。")
            with self.page.expect_file_chooser() as chooser:
                upload.click()
            chooser.value.set_files(str(path))
        self._created_empty = False
        deadline = time.monotonic() + self.upload_timeout
        while time.monotonic() < deadline:
            rows = self.page.locator(ROWS)
            for index in range(rows.count()):
                row = rows.nth(index)
                if self._ready_title(row) == title:
                    return
            alerts = self.page.get_by_role("alert").all_text_contents()
            if any(ERROR.search(alert) for alert in alerts):
                raise NotebookLMError("NotebookLM 回報來源匯入失敗。")
            self.page.wait_for_timeout(500)
        raise NotebookLMError("等待來源處理完成逾時；請在 NotebookLM 確認結果。")
