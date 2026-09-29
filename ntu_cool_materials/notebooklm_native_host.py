"""Chrome/Edge Native Messaging proxy for the local NotebookLM bridge.

The browser never receives or stores the bridge bearer token.  The active CLI
writes a short-lived discovery record in the user's private application data;
this host reads it and forwards a small allow-listed set of loopback requests.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import BinaryIO


HOST_NAME = "tw.edu.ntu.cool_notebooklm"
MAX_NATIVE_MESSAGE = 1024 * 1024
SESSION_FILE = Path.home() / ".ntu-cool-gcm" / ".secrets" / "notebooklm_native_session.json"
_GET_PATH = re.compile(r"^/(?:session|command|chunk/[A-Za-z0-9_-]{1,128}/\d{1,8})$")
_POST_PATHS = {"/start", "/reply", "/stop"}


def _binary_stdio() -> tuple[BinaryIO, BinaryIO]:
    if os.name == "nt":
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    return sys.stdin.buffer, sys.stdout.buffer


def read_message(stream: BinaryIO) -> dict | None:
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise ValueError("Incomplete native message header")
    size = struct.unpack("=I", header)[0]
    if size < 2 or size > MAX_NATIVE_MESSAGE:
        raise ValueError("Invalid native message size")
    payload = stream.read(size)
    if len(payload) != size:
        raise ValueError("Incomplete native message")
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Native message must be an object")
    return value


def write_message(stream: BinaryIO, value: dict) -> None:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_NATIVE_MESSAGE:
        raise ValueError("Native response too large")
    stream.write(struct.pack("=I", len(payload)))
    stream.write(payload)
    stream.flush()


def _load_session(path: Path = SESSION_FILE) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid native session")
    port, token, session_id = data.get("port"), data.get("token"), data.get("session_id")
    if not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError("Invalid native session port")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise ValueError("Invalid native session token")
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", session_id):
        raise ValueError("Invalid native session id")
    return data


def proxy(request: dict, *, session_path: Path = SESSION_FILE) -> dict:
    request_id = request.get("id")
    path = request.get("path")
    data = request.get("data")
    if not isinstance(request_id, str) or not isinstance(path, str):
        return {"id": request_id, "ok": False, "error": "invalid_request"}
    method = "POST" if data is not None else "GET"
    if method == "GET" and not _GET_PATH.fullmatch(path):
        return {"id": request_id, "ok": False, "error": "path_not_allowed"}
    if method == "POST" and (path not in _POST_PATHS or not isinstance(data, dict)):
        return {"id": request_id, "ok": False, "error": "path_not_allowed"}
    try:
        session = _load_session(session_path)
        body = None
        headers = {"Authorization": f"Bearer {session['token']}"}
        if method == "POST":
            body = json.dumps(data, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"http://127.0.0.1:{session['port']}{path}", data=body,
            headers=headers, method=method,
        )
        with urllib.request.urlopen(req, timeout=12) as response:
            result = json.loads(response.read(MAX_NATIVE_MESSAGE).decode("utf-8"))
        return {"id": request_id, "ok": True, "result": result}
    except FileNotFoundError:
        return {"id": request_id, "ok": False, "error": "no_active_import"}
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        return {"id": request_id, "ok": False, "error": "bridge_unavailable"}


def _host_executable() -> Path:
    found = shutil.which("ntu-cool-notebooklm-host")
    if not found:
        raise RuntimeError("找不到 ntu-cool-notebooklm-host；請先安裝 get-class-material。")
    return Path(found).resolve()


def install_host(extension_id: str, *, executable: Path | None = None) -> Path:
    if not re.fullmatch(r"[a-p]{32}", extension_id):
        raise ValueError("擴充套件 ID 必須是 32 個 a-p 字母。")
    target_dir = Path.home() / ".ntu-cool-gcm" / "native-host"
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = target_dir / f"{HOST_NAME}.json"
    manifest = {
        "name": HOST_NAME,
        "description": "NTU COOL to NotebookLM local companion",
        "path": str((executable or _host_executable()).resolve()),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if os.name == "nt":
        import winreg
        for vendor in ("Google\\Chrome", "Microsoft\\Edge"):
            key_path = f"Software\\{vendor}\\NativeMessagingHosts\\{HOST_NAME}"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                winreg.SetValueEx(key, None, 0, winreg.REG_SZ, str(manifest_path.resolve()))
    else:
        destinations = [
            Path.home() / ".config/google-chrome/NativeMessagingHosts",
            Path.home() / ".config/chromium/NativeMessagingHosts",
        ]
        if sys.platform == "darwin":
            destinations = [
                Path.home() / "Library/Application Support/Google/Chrome/NativeMessagingHosts",
                Path.home() / "Library/Application Support/Microsoft Edge/NativeMessagingHosts",
            ]
        for directory in destinations:
            directory.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(manifest_path, directory / manifest_path.name)
    return manifest_path


def serve(stdin: BinaryIO, stdout: BinaryIO) -> int:
    while True:
        try:
            message = read_message(stdin)
            if message is None:
                return 0
            write_message(stdout, proxy(message))
        except Exception:
            return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--install", metavar="EXTENSION_ID", help="Register this native host for Chrome and Edge.")
    args, unknown = parser.parse_known_args(argv)
    if args.install:
        try:
            path = install_host(args.install)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"安裝失敗：{exc}", file=sys.stderr)
            return 1
        print(f"NotebookLM 本機橋接已安裝：{path}")
        return 0
    # Chrome passes origin and (on Windows) --parent-window after launching.
    stdin, stdout = _binary_stdio()
    return serve(stdin, stdout)


if __name__ == "__main__":
    raise SystemExit(main())
