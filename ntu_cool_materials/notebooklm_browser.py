"""Personal NotebookLM UI adapter (English and Traditional Chinese).

This is browser automation, not an official Google API. Unknown layouts fail
closed. Keep UI selectors here so the local import journal remains independent.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .notebooklm import NotebookLMError, validate_notebook_url

HOME = "https://notebooklm.google.com/"
CREATE = re.compile(r"Create (new )?notebook|New notebook|建立.*筆記本|新增.*筆記本", re.I)
ADD = re.compile(r"Add sources?|新增來源|加入來源", re.I)
UPLOAD = re.compile(r"Upload files?|Choose files?|上傳檔案|選擇檔案", re.I)
ROWS = ".single-source-container"
TITLES = ".source-title, .source-item-title"
ERROR = re.compile(r"error|failed|unsupported|錯誤|失敗|不支援", re.I)
BUSY = re.compile(r"progress_activity|pending|processing|uploading|處理中|上傳中", re.I)


class GoogleLoginRejected(NotebookLMError):
    """Google refused this sign-in surface; retries cannot repair the flow."""


def is_google_rejected_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.hostname == "accounts.google.com" and bool(
        re.search(r"/signin/rejected(?:/|$)", parsed.path))


@dataclass
class NotebookChoice:
    title: str
    url: str | None = None
    card: object = None


def startup_error(exc: Exception) -> str:
    """Classify without echoing driver logs, profile contents, or session URLs."""
    message = str(exc).lower()
    if "sync api inside the asyncio loop" in message:
        return "瀏覽器執行環境衝突：請更新程式，或使用獨立 notebooklm 指令重試。"
    if "executable doesn't exist" in message or "playwright install" in message:
        return "尚未安裝 Playwright Chromium。請執行 python -m playwright install chromium。"
    if any(word in message for word in ("processsingleton", "singletonlock", "profile appears to be in use", "user data directory is already in use")):
        return "NotebookLM 設定檔正被其他瀏覽器使用；請關閉該專用瀏覽器後再試。"
    if isinstance(exc, PermissionError) or "permission denied" in message or "access is denied" in message:
        return "無權限開啟 NotebookLM 瀏覽器或設定檔；請確認安裝位置與資料夾權限。"
    return "NotebookLM 瀏覽器啟動失敗（原因未能分類）。請確認瀏覽器安裝與系統限制；此錯誤不代表一定缺少 Chromium。"


class NotebookLMBrowser:
    def __init__(self, profile_dir: Path, *, login_timeout: int = 600, upload_timeout: int = 180, playwright=None):
        self.profile_dir = profile_dir.expanduser().resolve()
        self.login_timeout = login_timeout
        self.upload_timeout = upload_timeout
        self._pw = playwright
        self._owns_pw = playwright is None
        self.context = None
        self.page = None
        self._created_empty = False
        self._logged_in = False

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            if self._owns_pw:
                self._pw = sync_playwright().start()
            self.context = self._pw.chromium.launch_persistent_context(
                str(self.profile_dir), headless=False, locale="en-US",
            )
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.set_default_timeout(15000)
            return self
        except Exception as exc:
            try:
                self.__exit__(None, None, None)
            except Exception:
                pass
            raise NotebookLMError(startup_error(exc)) from exc

    def __exit__(self, *_):
        try:
            if self.context:
                self.context.close()
        finally:
            if self._pw and self._owns_pw:
                self._pw.stop()

    def _button(self, pattern):
        matches = self.page.get_by_role("button", name=pattern)
        for index in range(matches.count()):
            candidate = matches.nth(index)
            if candidate.is_visible() and candidate.is_enabled():
                return candidate
        return None

    def login(self) -> None:
        try:
            self.page.goto(HOME, wait_until="domcontentloaded")
            print("請在開啟的專用瀏覽器完成 Google 登入；登入狀態會留在本機設定檔。"
                  "若 Google 拒絕此瀏覽器登入，請停止匯入；不要提供密碼給程式。")
            deadline = time.monotonic() + self.login_timeout
            while time.monotonic() < deadline:
                if self.page.is_closed():
                    raise NotebookLMError("NotebookLM 瀏覽器已關閉。")
                rejected = is_google_rejected_url(self.page.url)
                if not rejected and urlsplit(self.page.url).hostname == "accounts.google.com":
                    notice = self.page.get_by_text(re.compile(
                        r"This browser or app may not be secure|這個瀏覽器或應用程式可能不安全|此瀏覽器或應用程式可能不安全", re.I))
                    rejected = any(notice.nth(i).is_visible() for i in range(notice.count()))
                if rejected:
                    raise GoogleLoginRejected(
                        "Google 已拒絕此自動化瀏覽器登入，無法繼續自動掃描或上傳。"
                        "請改用系統預設瀏覽器登入 NotebookLM，並使用分批上傳資料夾。"
                        "重新安裝 Chromium 不會解除這項登入限制。")
                # Never perform notebook actions on an account/consent page.
                if urlsplit(self.page.url).hostname in {"notebooklm.google.com", "notebook.google.com"}:
                    if self._button(ADD) or self._button(CREATE):
                        self._logged_in = True
                        break
                self.page.wait_for_timeout(500)
            else:
                raise NotebookLMError("Google 登入尚未完成或 NotebookLM 介面無法辨識。")
        except NotebookLMError:
            raise
        except Exception as exc:
            raise NotebookLMError("NotebookLM 登入未完成；請檢查網路或瀏覽器是否已關閉。") from exc

    def list_notebooks(self) -> list[NotebookChoice]:
        """Scan only visible home-page UI; never access private RPC or auth state."""
        if not self._logged_in:
            self.login()
        if urlsplit(self.page.url).path not in {"", "/"}:
            self.page.goto(HOME, wait_until="domcontentloaded")
        self.page.get_by_role("button", name=CREATE).first.wait_for(state="visible")
        choices = []
        seen = set()
        anchors = self.page.locator('a[href*="/notebook/"]')
        for index in range(anchors.count()):
            link = anchors.nth(index)
            if not link.is_visible():
                continue
            try:
                url = validate_notebook_url(urljoin(self.page.url, link.get_attribute("href") or ""))
            except NotebookLMError:
                continue
            if url not in seen:
                seen.add(url)
                choices.append(NotebookChoice(link.inner_text().strip() or "未命名筆記本", url))
        # Some versions render notebook tiles as components rather than links.
        if not choices:
            cards = self.page.locator("project-button")
            for index in range(cards.count()):
                card = cards.nth(index)
                title = card.locator(".project-button-title")
                if card.is_visible() and title.count() == 1:
                    choices.append(NotebookChoice(title.inner_text().strip(), card=card))
        return choices

    def select_notebook(self, choice: NotebookChoice) -> str:
        if choice.url:
            return validate_notebook_url(choice.url)
        try:
            choice.card.click()
            self.page.wait_for_url(re.compile(r"https://(?:notebooklm|notebook)\.google\.com/notebook/"))
            return validate_notebook_url(self.page.url)
        except Exception as exc:
            raise NotebookLMError("無法開啟選取的筆記本；請重新掃描或貼上筆記本網址。") from exc

    def open_notebook(self, url: str | None, title: str) -> str:
        target = validate_notebook_url(url) if url else HOME
        try:
            if not self._logged_in:
                self.login()
            if url:
                # A login redirect can land on the home page instead of the target.
                self.page.goto(target, wait_until="domcontentloaded")
                self.page.get_by_role("button", name=ADD).first.wait_for(state="visible")
                actual = validate_notebook_url(self.page.url)
                if actual.rsplit("/", 1)[-1] != target.rsplit("/", 1)[-1]:
                    raise NotebookLMError("未能開啟指定筆記本，請確認帳號與存取權。")
            else:
                self.page.goto(HOME, wait_until="domcontentloaded")
                self.page.get_by_role("button", name=CREATE).first.wait_for(state="visible")
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
