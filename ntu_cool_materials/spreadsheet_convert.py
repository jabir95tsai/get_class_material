"""Convert downloaded Excel workbooks to Markdown so NotebookLM can import them.

NotebookLM rejects .xlsx/.xls sources. Each workbook gets a sibling
`<name>.xlsx.md` (one `## <sheet>` table per worksheet) rendered by Microsoft's
markitdown, which is an optional dependency (`pip install "get-class-material[excel]"`).
The original workbook is never modified. Conversion is idempotent: a Markdown
file at least as new as its workbook is left alone.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import t
from .storage import atomic_write_text

SPREADSHEET_TYPES = {".xlsx", ".xls"}
INSTALL_HINT = 'pip install "get-class-material[excel]"'


@dataclass
class ConvertStats:
    done: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    missing_dependency: bool = False


def markdown_path_for(workbook: Path) -> Path:
    # Keep the workbook extension so `report.xlsx` never collides with a Page saved as `report.md`.
    return workbook.with_name(workbook.name + ".md")


def find_spreadsheets(course_dir: Path) -> list[Path]:
    found: list[Path] = []
    for directory, dirs, files in os.walk(course_dir, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "metadata")
        for name in sorted(files):
            path = Path(directory) / name
            if (not name.startswith(".") and path.suffix.lower() in SPREADSHEET_TYPES
                    and not path.is_symlink() and path.stat().st_size > 0):
                found.append(path)
    return found


def _is_current(workbook: Path, target: Path) -> bool:
    return target.is_file() and target.stat().st_mtime >= workbook.stat().st_mtime


def _load_converter():
    try:
        from markitdown import MarkItDown
    except ImportError:
        return None
    return MarkItDown(enable_plugins=False)


def workbook_markdown(workbook: Path, converter) -> str:
    body = converter.convert(str(workbook)).text_content.strip()
    if not body:
        raise ValueError("no cell content")
    return f"# {workbook.name}\n\n{body}\n"


def convert_spreadsheets(course_dir: Path, *, quiet: bool = False) -> ConvertStats:
    """Write `<workbook>.md` next to every changed workbook under course_dir."""
    stats = ConvertStats()
    if not course_dir.is_dir():
        return stats
    pending = []
    for workbook in find_spreadsheets(course_dir):
        if _is_current(workbook, markdown_path_for(workbook)):
            stats.skipped += 1
        else:
            pending.append(workbook)
    if not pending:
        return stats
    converter = _load_converter()
    if converter is None:
        stats.missing_dependency = True
        if not quiet:
            print(t(
                f"  ⚠ 有 {len(pending)} 個 Excel 檔未轉成 Markdown(NotebookLM 不支援 Excel)。安裝轉換工具:{INSTALL_HINT}",
                f"  ⚠ {len(pending)} Excel file(s) not converted to Markdown (NotebookLM can't read Excel). Install: {INSTALL_HINT}",
            ))
        return stats
    for workbook in pending:
        try:
            atomic_write_text(markdown_path_for(workbook), workbook_markdown(workbook, converter))
        except Exception as exc:  # A bad workbook must never break the download run.
            stats.failed.append(f"{workbook.name}: {type(exc).__name__}: {exc}")
            if not quiet:
                print(t(f"  ⚠ Excel 轉換失敗 {workbook.name}: {exc}",
                        f"  ⚠ Excel conversion failed for {workbook.name}: {exc}"))
            continue
        stats.done += 1
        if not quiet:
            print(f"  [excel→md] {markdown_path_for(workbook).name}")
    return stats
