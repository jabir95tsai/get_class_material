"""Install the local checkout plus notebooklm-py into a private Windows venv."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def validate_source(root: Path) -> None:
    for name in ("pyproject.toml", "LICENSE", "ntu_cool_materials/__init__.py"):
        if not (root / name).is_file():
            raise FileNotFoundError(f"缺少專案檔案：{root / name}。請使用完整專案資料夾。")


def install(root: Path, target: Path) -> Path:
    # Validate everything before touching an existing installation.
    validate_source(root)
    target.mkdir(parents=True, exist_ok=True)
    runtime = target / "venv"
    python = runtime / "Scripts" / "python.exe"
    print("[1/2] 準備獨立 Python 環境…", flush=True)
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(runtime)], check=True)
    print("[2/2] 安裝本機版本與 NotebookLM 套件（首次需要網路）…", flush=True)
    subprocess.run([
        str(python), "-m", "pip", "install", "--disable-pip-version-check", f"{root}[notebooklm]",
    ], check=True)
    return python


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    if os.name != "nt" or sys.version_info < (3, 11):
        print("此安裝程式需要 Windows 與 Python 3.11 以上。", file=sys.stderr)
        return 1
    try:
        install(ROOT, Path.home() / ".ntu-cool-gcm" / "notebooklm")
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"安裝未完成：{exc}\n修正問題後可再次雙擊 Install-NotebookLM.cmd。", file=sys.stderr)
        return 1
    print("\n安裝完成。首次請執行 Login-NotebookLM.cmd 自行登入 Google。")
    print("之後雙擊 Start-NotebookLM.cmd，即可選課匯入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
