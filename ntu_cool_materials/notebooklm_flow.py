"""Interactive course selection and guided NotebookLM API import."""
from __future__ import annotations

from pathlib import Path

from . import console
from .i18n import t
from .notebooklm import NotebookLMError, build_import_plan
from .spreadsheet_convert import convert_spreadsheets


def choose(prompt: str, choices: set[str], default: str) -> str:
    while True:
        try:
            value = input(prompt).strip().lower() or default
        except (EOFError, KeyboardInterrupt):
            return "q"
        if value in choices or value == "q":
            return value
        print(t("請輸入列出的選項，或 q 取消。", "Enter one of the listed options, or q to cancel."))


def guided_import(course_dir: Path, *, notebook_url: str | None = None,
                  include_media: bool = True, max_sources: int = 50,
                  storage_path: Path | None = None) -> int:
    if not console.stdin_is_interactive():
        raise NotebookLMError(t("互動引導需要終端機。自動化請傳入 --course-dir 與明確參數；預覽可加 --dry-run。",
                                "The guided import needs a terminal. For automation pass --course-dir and explicit options; add --dry-run to preview."))
    convert_spreadsheets(course_dir)  # so the source count below includes converted workbooks
    plan = build_import_plan(course_dir, include_media=include_media)
    print(t(f"\nNotebookLM 匯入引導\n課程：{plan.root.name}\n"
            f"可用來源：{len(plan.sources)}，略過：{len(plan.skipped)}",
            f"\nNotebookLM guided import\nCourse: {plan.root.name}\n"
            f"Usable sources: {len(plan.sources)}, skipped: {len(plan.skipped)}"))
    if not plan.sources:
        return 0
    from .notebooklm_api import run_api_import
    # Hand over the plan: rebuilding it would hash every file (videos included) twice.
    run_api_import(plan.root, include_media=include_media, max_sources=max_sources,
                   notebook_url=notebook_url, storage_path=storage_path, interactive=True, plan=plan)
    return 0


def resolve_course_folder(output_root: Path, course_id: str) -> Path:
    """Select an exact downloaded course ID without prompting or guessing a term."""
    if not course_id.isascii() or not course_id.isdecimal():
        raise NotebookLMError(t("課程 ID 必須是數字。", "The course ID must be a number."))
    matches = [p for p in output_root.iterdir()
               if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")
               and p.name.endswith(f"({course_id})")] if output_root.is_dir() else []
    if len(matches) != 1:
        raise NotebookLMError(t(
            f"課程 {course_id} 找到 {len(matches)} 個資料夾；請先下載，或用 --course-dir 指定確切路徑。",
            f"Found {len(matches)} folders for course {course_id}; download it first, or pass the exact path with --course-dir."))
    return matches[0]


def select_course_folder(output_root: Path) -> Path | None:
    """Only inspect the configured materials root; never scan the user's home."""
    if not console.stdin_is_interactive():
        raise NotebookLMError(t("非互動模式請提供 --course-dir。", "Pass --course-dir when not running interactively."))
    folders = sorted((p for p in output_root.iterdir() if p.is_dir() and not p.name.startswith(".")),
                     key=lambda p: p.name) if output_root.is_dir() else []
    print(t("\n選擇已下載的課程：", "\nPick a downloaded course:"))
    for index, folder in enumerate(folders, 1):
        print(f"{index}) {folder.name}")
    print(t("p) 輸入其他課程資料夾路徑\nq) 返回", "p) Enter another course folder path\nq) Back"))
    answer = choose(t("選擇：", "Choice: "), {str(i) for i in range(1, len(folders) + 1)} | {"p"}, "q")
    if answer == "q":
        return None
    if answer != "p":
        return folders[int(answer) - 1]
    try:
        raw = input(t("課程資料夾完整路徑（q 取消）：", "Full course folder path (q to cancel): ")).strip().strip('"')
    except (EOFError, KeyboardInterrupt):
        return None
    return None if not raw or raw.lower() == "q" else Path(raw).expanduser()
