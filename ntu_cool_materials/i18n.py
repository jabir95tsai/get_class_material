"""Tiny localization helper.

Default language is Traditional Chinese ("zh"). The CLI accepts `--lang en`
which switches every wrapped string to English by calling `set_lang("en")`
once at startup. Strings are paired inline at the call site:

    print(t("找到 5 門課程", "Found 5 course(s)"))

This keeps both translations next to each other in code, makes diffs simple,
and avoids having to maintain a parallel dict file.
"""
from __future__ import annotations


_LANG = "zh"


def set_lang(lang: str) -> None:
    """Set the global UI language. Accepts 'zh' (default) or 'en'."""
    global _LANG
    _LANG = "en" if str(lang).lower().startswith("en") else "zh"


def get_lang() -> str:
    return _LANG


def t(zh: str, en: str) -> str:
    """Pick the active language. Pass the Chinese form first (default),
    English form second."""
    return en if _LANG == "en" else zh
