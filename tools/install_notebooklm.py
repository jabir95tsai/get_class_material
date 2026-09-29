"""Install the local checkout's Windows companion without copying extension IDs."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EXTENSION_FILES = (
    "manifest.json", "worker.js", "panel.html", "panel.css", "panel.mjs",
    "page.mjs", "README.md", "icons/icon-16.png", "icons/icon-32.png",
    "icons/icon-48.png", "icons/icon-128.png",
)


def extension_id(source: Path) -> str:
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    key = base64.b64decode(manifest["key"], validate=True)
    if not key:
        raise ValueError("Extension public key is empty")
    digest = hashlib.sha256(key).hexdigest()[:32]
    return "".join(chr(ord("a") + int(char, 16)) for char in digest)


def validate_source(root: Path) -> str:
    source = root / "extensions" / "notebooklm"
    for name in EXTENSION_FILES:
        if not (source / name).is_file():
            raise FileNotFoundError(f"缺少安裝檔案：{source / name}。請使用完整專案資料夾。")
    for name in ("pyproject.toml", "LICENSE", "ntu_cool_materials/__init__.py"):
        if not (root / name).is_file():
            raise FileNotFoundError(f"缺少專案檔案：{root / name}")
    return extension_id(source)


def install(root: Path, target: Path) -> Path:
    # Validate everything before touching an existing installation.
    identity = validate_source(root)
    target.mkdir(parents=True, exist_ok=True)
    runtime = target / "venv"
    python = runtime / "Scripts" / "python.exe"
    print("[1/3] 準備獨立 Python 環境…", flush=True)
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(runtime)], check=True)
    print("[2/3] 安裝本機版本與必要套件（首次需要網路）…", flush=True)
    subprocess.run([
        str(python), "-m", "pip", "install", "--disable-pip-version-check", f"{root}[notebooklm]",
    ], check=True)
    executable = runtime / "Scripts" / "ntu-cool-notebooklm-host.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"找不到已安裝的 companion：{executable}")
    print("[3/3] 設定擴充套件與本機連線…", flush=True)
    source = root / "extensions" / "notebooklm"
    extension = target / "extension"
    for name in EXTENSION_FILES:
        dest = extension / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, dest)
    shutil.copy2(root / "LICENSE", extension / "LICENSE")
    # Explicit executable avoids registering a stale host found elsewhere on PATH.
    subprocess.run([
        str(python), "-X", "utf8", "-c",
        "import sys; from pathlib import Path; "
        "from ntu_cool_materials.notebooklm_native_host import install_host; "
        "install_host(sys.argv[1], executable=Path(sys.argv[2]))",
        identity, str(executable),
    ], check=True, cwd=target)
    return extension


def open_setup(extension: Path) -> None:
    candidates = [shutil.which("chrome")]
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if os.environ.get(variable):
            candidates.append(str(Path(os.environ[variable]) / "Google/Chrome/Application/chrome.exe"))
    chrome = next((path for path in candidates if path and Path(path).is_file()), None)
    if chrome:
        subprocess.Popen([chrome, "chrome://extensions/"])
    else:
        print("請在 Chrome 網址列開啟 chrome://extensions/")
    os.startfile(str(extension))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-open", action="store_true", help="Install without opening Chrome or Explorer")
    parser.add_argument("--extension-setup", action="store_true", help="Open the optional legacy extension setup")
    args = parser.parse_args(argv)
    if os.name != "nt" or sys.version_info < (3, 11):
        print("此安裝程式需要 Windows 與 Python 3.11 以上。", file=sys.stderr)
        return 1
    try:
        target = Path.home() / ".ntu-cool-gcm" / "notebooklm"
        extension = install(ROOT, target)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"安裝未完成：{exc}\n修正問題後可再次雙擊 Install-NotebookLM.cmd。", file=sys.stderr)
        return 1
    print("\nAPI 模式安裝完成。首次請執行 Login-NotebookLM.cmd 自行登入 Google。")
    print("之後雙擊 Start-NotebookLM.cmd，即可選課匯入，不需要擴充套件。")
    if args.extension_setup:
        print(f"選用的舊版擴充套件路徑：{extension}")
    if args.extension_setup and not args.no_open:
        try:
            open_setup(extension)
        except OSError as exc:
            print(f"無法自動開啟視窗，請依上方路徑手動操作：{exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
