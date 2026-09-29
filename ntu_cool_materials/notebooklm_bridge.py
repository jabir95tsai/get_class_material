"""Loopback-only bridge for the user's normal-browser NotebookLM extension.

Only one explicitly selected course is served. File contents are exposed only
while the existing import engine has staged that file for an upload command.
Authentication never uses Google credentials or tokens in URLs.
"""
from __future__ import annotations

import base64
import hmac
import json
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .notebooklm import NotebookLMError, build_import_plan, import_plan, validate_notebook_url
from .notebooklm_native_host import SESSION_FILE

DEFAULT_PORT = 43821
CHUNK_SIZE = 384 * 1024


def _publish_native_session(session, port: int, path: Path = SESSION_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "version": 1, "session_id": session.session_id,
        "port": port, "token": session.token,
    }), encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(path)


def _remove_native_session(session_id: str, path: Path = SESSION_FILE) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("session_id") == session_id:
            path.unlink(missing_ok=True)
    except (OSError, ValueError, json.JSONDecodeError):
        pass


def pairing_key(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="ascii") as stream:
            stream.write(secrets.token_urlsafe(32))
    except FileExistsError:
        pass
    key = path.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", key):
        raise NotebookLMError("擴充套件配對檔格式錯誤，請檢查本機 secrets 資料夾。")
    return key


class BridgeSession:
    def __init__(self, plan, *, token: str, max_sources: int = 50, timeout: float = 240, notebook_url=None):
        self.plan = plan
        self.token = token
        self.max_sources = max_sources
        self.timeout = timeout
        self.session_id = secrets.token_hex(16)
        self.condition = threading.Condition()
        self.status = "waiting"
        self.message = "請在擴充套件選擇筆記本，再開始匯入。"
        self.command = None
        self.reply = None
        self.active_file = None
        self.uploaded = 0
        self.unchanged = 0
        self.worker = None
        self.closed = False
        self.suggested_url = validate_notebook_url(notebook_url) if notebook_url else None

    def summary(self):
        with self.condition:
            return {"session_id": self.session_id, "course": self.plan.root.name,
                    "sources": len(self.plan.sources), "skipped": len(self.plan.skipped),
                    "status": self.status, "message": self.message,
                    "uploaded": self.uploaded, "unchanged": self.unchanged,
                    "suggested_notebook_url": self.suggested_url}

    def start(self, target: str | None, force_new: bool):
        if target:
            target = validate_notebook_url(target)
        if not target and not force_new:
            raise ValueError("Choose an existing notebook or explicitly create a new one")
        with self.condition:
            if self.status != "waiting":
                raise ValueError("This session already started; start a new CLI session to retry")
            self.status = "running"
            self.message = "正在匯入；請保留擴充套件工作頁與 NotebookLM 分頁。"
            self.worker = threading.Thread(target=self._run, args=(target, force_new), daemon=True)
            self.worker.start()

    def _run(self, target, force_new):
        try:
            result = import_plan(self.plan, self, notebook_url=target,
                                 max_sources=self.max_sources, force_new=force_new)
            with self.condition:
                self.uploaded = result.uploaded
                self.unchanged = result.unchanged
                self.status = "complete"
                self.message = f"匯入完成：新增 {result.uploaded}，已存在 {result.unchanged}。"
        except Exception as exc:
            with self.condition:
                self.status = "failed"
                self.message = str(exc) if isinstance(exc, NotebookLMError) else "匯入中斷，請檢查 NotebookLM 與本機檔案後重新執行。"
        finally:
            with self.condition:
                self.command = None
                self.active_file = None
                self.condition.notify_all()

    def rpc(self, operation: str, payload: dict):
        with self.condition:
            if self.closed:
                raise NotebookLMError("本機連線已結束。")
            identifier = secrets.token_hex(16)
            self.reply = None
            self.command = {"id": identifier, "operation": operation, "payload": payload}
            deadline = time.monotonic() + self.timeout
            while self.reply is None and not self.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.command = None
                    raise NotebookLMError("等待擴充套件逾時；請保持工作頁開啟，檢查筆記本來源後再執行。")
                self.condition.wait(min(remaining, 1))
            if self.closed:
                raise NotebookLMError("匯入已停止；若上傳已開始，請先確認遠端來源。")
            reply = self.reply
            self.command = None
            if not reply.get("ok"):
                raise NotebookLMError("擴充套件未能確認網頁操作完成；請查看擴充套件工作頁的錯誤。")
            return reply.get("result")

    def acknowledge(self, data):
        with self.condition:
            if not self.command or data.get("id") != self.command["id"] or self.reply is not None:
                raise ValueError("Stale or duplicate command response")
            self.reply = data
            self.condition.notify_all()

    def stop(self):
        with self.condition:
            self.closed = True
            self.command = None
            self.active_file = None
            self.condition.notify_all()

    # BrowserAdapter protocol; the existing journal remains the authority.
    def open_notebook(self, url, title):
        result = self.rpc("open", {"url": url, "title": title})
        actual = validate_notebook_url(result)
        if url and actual.rsplit("/", 1)[-1] != url.rsplit("/", 1)[-1]:
            raise NotebookLMError("擴充套件回傳的筆記本與選取目標不同。")
        return actual

    def ready_titles(self):
        titles = self.rpc("ready", {})
        if not isinstance(titles, list) or not all(isinstance(t, str) for t in titles):
            raise NotebookLMError("來源清單格式不正確。")
        return set(titles)

    def source_count(self):
        count = self.rpc("count", {})
        if type(count) is not int or count < 0:
            raise NotebookLMError("來源數量無法確認。")
        return count

    def upload(self, path, title):
        with self.condition:
            self.active_file = Path(path)
        try:
            try:
                self.rpc("upload", {"title": title, "size": path.stat().st_size, "chunk_size": CHUNK_SIZE})
            except NotebookLMError:
                print("NotebookLM：未收到有效的上傳完成回覆；請查看擴充套件錯誤紀錄。", flush=True)
                raise
            # Independent inventory check before the engine commits 'complete'.
            try:
                ready = self.ready_titles()
            except NotebookLMError:
                print("NotebookLM：上傳步驟已回覆，但再次讀取來源清單失敗；保留 pending。", flush=True)
                raise
            if title not in ready:
                print("NotebookLM：上傳後再次核對，來源已不在可用清單；未確認儲存成功，保留 pending。", flush=True)
                raise NotebookLMError("上傳後來源尚未可用。")
            self.uploaded += 1
        finally:
            with self.condition:
                self.active_file = None


def make_server(session: BridgeSession, port: int = DEFAULT_PORT):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Never log pairing keys, URLs, or source names.

        def _origin(self):
            origin = self.headers.get("Origin")
            if origin and not re.fullmatch(r"chrome-extension://[a-p]{32}", origin):
                return False
            return origin or ""

        def _send(self, code, data):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            origin = self._origin()
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _allowed(self, preflight=False):
            expected_host = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != expected_host or self._origin() is False:
                self._send(403, {"error": "Forbidden origin or host"})
                return False
            if not preflight and not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + session.token):
                self._send(401, {"error": "Pairing required"})
                return False
            return True

        def do_OPTIONS(self):
            if not self._allowed(preflight=True):
                return
            self.send_response(204)
            if self._origin():
                self.send_header("Access-Control-Allow-Origin", self._origin())
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if not self._allowed():
                return
            if self.path == "/session":
                self._send(200, session.summary())
            elif self.path == "/command":
                with session.condition:
                    self._send(200, {"command": session.command if session.reply is None else None})
            elif re.fullmatch(r"/chunk/[a-f0-9]{32}/[0-9]+", self.path):
                _, _, command_id, index = self.path.split("/")
                with session.condition:
                    if (not session.command or session.command["id"] != command_id
                            or session.command["operation"] != "upload" or not session.active_file):
                        self._send(409, {"error": "No active upload"})
                        return
                    offset = int(index) * CHUNK_SIZE
                    if offset >= session.active_file.stat().st_size:
                        self._send(416, {"error": "Chunk out of range"})
                        return
                    with session.active_file.open("rb") as stream:
                        stream.seek(offset)
                        chunk = stream.read(CHUNK_SIZE)
                    self._send(200, {"data": base64.b64encode(chunk).decode("ascii")})
            else:
                self._send(404, {"error": "Unknown route"})

        def do_POST(self):
            if not self._allowed():
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1024 * 1024:
                    raise ValueError("Invalid body size")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("Expected object")
                if data.get("session_id") != session.session_id:
                    raise ValueError("Stale session")
                if self.path == "/start":
                    target = data.get("notebook_url")
                    new = data.get("force_new", False)
                    if (target is not None and not isinstance(target, str)) or type(new) is not bool:
                        raise ValueError("Invalid target")
                    session.start(target, new)
                elif self.path == "/reply":
                    if type(data.get("ok")) is not bool:
                        raise ValueError("Invalid response")
                    session.acknowledge(data)
                elif self.path == "/stop":
                    session.stop()
                else:
                    self._send(404, {"error": "Unknown route"})
                    return
                self._send(200, {"ok": True})
            except (ValueError, TypeError, NotebookLMError):
                self._send(400, {"error": "Invalid request or session state"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def run_extension_import(course_dir: Path, *, secrets_dir: Path, include_media=False,
                         max_sources=50, port=DEFAULT_PORT, notebook_url=None) -> int:
    plan = build_import_plan(course_dir, include_media=include_media)
    if not plan.sources:
        print("沒有可匯入的來源。")
        return 0
    if max_sources < 1:
        raise NotebookLMError("來源上限必須大於零。")
    session = BridgeSession(plan, token=secrets.token_urlsafe(32), max_sources=max_sources, notebook_url=notebook_url)
    try:
        server = make_server(session, port)
    except OSError as exc:
        raise NotebookLMError("本機 NotebookLM 連線埠無法開啟；請關閉其他匯入工作或設定 --extension-port。") from exc
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _publish_native_session(session, server.server_port)
    print(f"\n擴充套件模式：{plan.root.name}，{len(plan.sources)} 個候選來源。")
    print("在正常 Chrome／Edge 點擴充套件圖示；本機教材會自動連接，不需要配對金鑰。")
    print("保留此終端機，並在擴充套件工作頁選擇筆記本及確認匯入；Ctrl+C 可停止。")
    try:
        while session.status not in {"complete", "failed"} and not session.closed:
            time.sleep(0.3)
        print(session.message)
        # Allow the extension to observe the terminal state before closing.
        time.sleep(2)
        return 0 if session.status == "complete" else 1
    except KeyboardInterrupt:
        print("已停止匯入；若來源已送出，請核對 NotebookLM 後再執行。")
        return 1
    finally:
        _remove_native_session(session.session_id)
        session.stop()
        if session.worker:
            session.worker.join(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
