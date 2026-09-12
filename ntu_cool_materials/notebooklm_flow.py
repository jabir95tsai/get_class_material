"""Interactive onboarding and a browser-independent manual upload fallback."""
from __future__ import annotations

import shutil
import sys
import tempfile
import webbrowser
from pathlib import Path

from .notebooklm import NotebookLMError, build_import_plan, run_import, validate_notebook_url
from .storage import sha256_file


def choose(prompt: str, choices: set[str], default: str) -> str:
    while True:
        try:
            value = input(prompt).strip().lower() or default
        except (EOFError, KeyboardInterrupt):
            return "q"
        if value in choices or value == "q":
            return value
        print("請輸入列出的選項，或 q 取消。")


def prepare_manual_upload(course_dir: Path, *, include_media: bool = False,
                          max_sources: int = 50) -> Path | None:
    """Create a fresh immutable upload bundle. Never changes the remote journal."""
    if max_sources < 1:
        raise NotebookLMError("每批來源數必須大於零。")
    plan = build_import_plan(course_dir, include_media=include_media)
    if not plan.sources:
        print("沒有可匯入的文件。")
        return None
    # Hidden staging root is excluded by future scans. Fresh runs never overwrite.
    base = plan.root / ".notebooklm-manual"
    base.mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="upload-", dir=base))
    lines = ["NotebookLM 手動上傳清單", "", "此資料夾尚未上傳至 Google。",
             "請在正常瀏覽器登入 NotebookLM，建立或開啟筆記本，選擇新增來源 → 上傳檔案。",
             "每個 batch 資料夾是一批來源。預設每批 50 個；若筆記本已有來源，請預留剩餘額度。",
             "不同 batch 可上傳到不同筆記本；不要把整個壓縮檔或本清單當成教材上傳。",
             "重複上傳不會自動去重；請自行核對遠端來源。", ""]
    try:
        for index, source in enumerate(plan.sources):
            batch = output / f"batch-{index // max_sources + 1:03d}"
            batch.mkdir(exist_ok=True)
            target = batch / source.title
            shutil.copyfile(source.path, target)
            if sha256_file(target) != source.digest:
                raise NotebookLMError("準備期間教材已變動；請重新產生上傳資料夾。")
            lines.append(f"{target.relative_to(output).as_posix()} ← {source.relative_path}")
        lines.extend(["", "略過的檔案："] + [f"{name}：{reason}" for name, reason in plan.skipped])
        (output / "上傳說明.txt").write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        # Keep partial output explicitly marked; never advertise it as ready.
        (output / "未完成.txt").write_text("產生中斷；請勿使用這一批，請重新產生。", encoding="utf-8")
        raise
    print(f"已準備 {len(plan.sources)} 個來源，尚未上傳。\n上傳資料夾：{output}")
    return output


def guided_import(course_dir: Path, *, profile_dir: Path, notebook_url: str | None = None,
                  include_media: bool = False, max_sources: int = 50) -> int:
    if not sys.stdin.isatty():
        raise NotebookLMError("互動引導需要終端機。自動化請傳入 --course-dir 與明確參數；預覽可加 --dry-run。")
    plan = build_import_plan(course_dir, include_media=include_media)
    print(f"\nNotebookLM 匯入引導\n課程：{plan.root.name}\n"
          f"可用來源：{len(plan.sources)}，略過：{len(plan.skipped)}")
    if not plan.sources:
        return 0
    print("1) 準備分批資料夾，在自己的瀏覽器上傳（相容性較高）\n"
          "2) 自動匯入（實驗功能，需要在專用瀏覽器登入 Google）\n"
          "q) 暫不匯入，保留已下載教材")
    mode = choose("選擇 [1/2/q，預設 1]：", {"1", "2"}, "1")
    if mode == "q":
        return 0
    if mode == "2":
        if not notebook_url:
            target = choose("筆記本：1) 每門課自動建立／重用  2) 貼上已有筆記本網址 [1/2]：", {"1", "2"}, "1")
            if target == "q":
                return 0
            if target == "2":
                while True:
                    try:
                        raw = input("請貼上筆記本網址（q 取消）：").strip()
                    except (EOFError, KeyboardInterrupt):
                        return 0
                    if raw.lower() == "q":
                        return 0
                    try:
                        notebook_url = validate_notebook_url(raw)
                        break
                    except NotebookLMError as exc:
                        print(str(exc))
        print("接下來會將這門課的候選教材上傳至你的 Google NotebookLM。"
              "請只上傳有權使用的教材；登入狀態只保存在你的電腦。")
        if choose("開始上傳？[y/N]：", {"y", "n"}, "n") != "y":
            return 0
        try:
            run_import(plan.root, profile_dir=profile_dir, notebook_url=notebook_url,
                       include_media=include_media, max_sources=max_sources)
            return 0
        except (NotebookLMError, OSError) as exc:
            print(str(exc) if isinstance(exc, NotebookLMError) else "本機檔案存取失敗。")
            print("教材已保留。若曾開始上傳，先在 NotebookLM 核對來源，避免手動重複上傳。")
            if choose("改成準備手動上傳資料夾？[y/N]：", {"y", "n"}, "n") != "y":
                return 1
            # A fallback cannot turn an unconfirmed automatic upload into success.
            prepare_manual_upload(plan.root, include_media=include_media, max_sources=max_sources)
            return 1
    output = prepare_manual_upload(plan.root, include_media=include_media, max_sources=max_sources)
    if output and choose("開啟 NotebookLM 網頁？[Y/n]：", {"y", "n"}, "y") == "y":
        try:
            if not webbrowser.open(notebook_url or "https://notebooklm.google.com/"):
                print("請自行在瀏覽器開啟 https://notebooklm.google.com/")
        except webbrowser.Error:
            print("請自行在瀏覽器開啟 https://notebooklm.google.com/")
    return 0


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
