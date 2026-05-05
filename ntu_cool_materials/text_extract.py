from __future__ import annotations

from pathlib import Path


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".tsv", ".json", ".html", ".htm", ".xml"}


class UnsupportedFileType(ValueError):
    pass


def extract_plain_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in TEXT_EXTENSIONS:
        raise UnsupportedFileType(
            f"{path.name} is not a plain-text file. Add a parser for {suffix or 'unknown'} files."
        )

    return path.read_text(encoding="utf-8", errors="replace")
