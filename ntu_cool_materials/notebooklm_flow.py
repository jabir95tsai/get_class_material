"""Interactive course selection and guided NotebookLM API import."""
from __future__ import annotations

import sys
from pathlib import Path

from .notebooklm import NotebookLMError, build_import_plan


def choose(prompt: str, choices: set[str], default: str) -> str:
    while True:
        try:
            value = input(prompt).strip().lower() or default
        except (EOFError, KeyboardInterrupt):
            return "q"
        if value in choices or value == "q":
            return value
        print("請輸入列出的選項，或 q 取消。")


def guided_import(course_dir: Path, *, notebook_url: str | None = None,
                  include_media: bool = False, max_sources: int = 50,
                  storage_path: Path | None = None) -> int:
    if not sys.stdin.isatty():
        raise NotebookLMError("互動引導需要終端機。自動化請傳入 --course-dir 與明確參數；預覽可加 --dry-run。")
    plan = build_import_plan(course_dir, include_media=include_media)
    print(f"\nNotebookLM 匯入引導\n課程：{plan.root.name}\n"
          f"可用來源：{len(plan.sources)}，略過：{len(plan.skipped)}")
    if not plan.sources:
        return 0
    from .notebooklm_api import run_api_import
    run_api_import(plan.root, include_media=include_media, max_sources=max_sources,
                   notebook_url=notebook_url, storage_path=storage_path, interactive=True)
    return 0


def resolve_course_folder(output_root: Path, course_id: str) -> Path:
    """Select an exact downloaded course ID without prompting or guessing a term."""
    if not course_id.isascii() or not course_id.isdecimal():
        raise NotebookLMError("課程 ID 必須是數字。")
    matches = [p for p in output_root.iterdir()
               if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")
               and p.name.endswith(f"({course_id})")] if output_root.is_dir() else []
    if len(matches) != 1:
        raise NotebookLMError(f"課程 {course_id} 找到 {len(matches)} 個資料夾；請先下載，或用 --course-dir 指定確切路徑。")
    return matches[0]


def select_course_folder(output_root: Path) -> Path | None:
    """Only inspect the configured materials root; never scan the user's home."""
    if not sys.stdin.isatty():
        raise NotebookLMError("非互動模式請提供 --course-dir。")
    folders = sorted((p for p in output_root.iterdir() if p.is_dir() and not p.name.startswith(".")),
                     key=lambda p: p.name) if output_root.is_dir() else []
    print("\n選擇已下載的課程：")
    for index, folder in enumerate(folders, 1):
        print(f"{index}) {folder.name}")
    print("p) 輸入其他課程資料夾路徑\nq) 返回")
    answer = choose("選擇：", {str(i) for i in range(1, len(folders) + 1)} | {"p"}, "q")
    if answer == "q":
        return None
    if answer != "p":
        return folders[int(answer) - 1]
    try:
        raw = input("課程資料夾完整路徑（q 取消）：").strip().strip('"')
    except (EOFError, KeyboardInterrupt):
        return None
    return None if not raw or raw.lower() == "q" else Path(raw).expanduser()
