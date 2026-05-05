"""For one week: download PDF Files, save Page content (json+md). YouTube + CDN handled by other scripts."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ntu_cool_materials.announcements import html_to_text
from ntu_cool_materials.canvas_client import NoRedirectHandler
from ntu_cool_materials.media_naming import sanitize_teacher_title
from ntu_cool_materials.session_client import DROP_REQUEST_HEADER_NAMES, read_headers_file


CANVAS_NETLOC = "cool.ntu.edu.tw"


def session_headers(headers_path: Path) -> dict[str, str]:
    raw = read_headers_file(headers_path)
    h = {n: v for n, v in raw.items() if n.lower() not in DROP_REQUEST_HEADER_NAMES}
    h["Accept"] = "application/json, text/plain, */*"
    h.setdefault("User-Agent", "ntu-cool-materials/0.1")
    return h


def request_json(url: str, headers: dict[str, str], timeout: float = 30.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def download_canvas_file(file_id: str, target_path: Path, headers: dict[str, str]) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = target_path.with_name(target_path.name + ".part")
    opener = urllib.request.build_opener(NoRedirectHandler)
    current = f"https://{CANVAS_NETLOC}/files/{file_id}/download?download_frd=1"

    for _ in range(10):
        parsed = urllib.parse.urlparse(current)
        h = dict(headers) if parsed.netloc.lower() == CANVAS_NETLOC else {"User-Agent": "ntu-cool-materials/0.1"}
        req = urllib.request.Request(current, headers=h)
        try:
            resp = opener.open(req, timeout=60)
            break
        except urllib.error.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308}:
                current = urllib.parse.urljoin(current, exc.headers["Location"])
                continue
            raise
    else:
        raise RuntimeError("too many redirects for file " + file_id)

    with resp, tmp.open("wb") as f:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(target_path)


def page_markdown(page_json: dict[str, Any]) -> str:
    title = page_json.get("title") or "(untitled)"
    body = html_to_text(page_json.get("body"))
    parts = [f"# {title}", ""]
    if body:
        parts.append(body)
    return "\n".join(parts).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week-items", required=True)
    parser.add_argument("--out-dir", required=True, help="Per-week directory (contains files/, pages/, metadata/).")
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--headers-file", default=".secrets/ntu_cool_headers.txt")
    args = parser.parse_args()

    week_items = json.loads(Path(args.week_items).read_text(encoding="utf-8"))
    items = (week_items.get("module") or {}).get("items", [])
    headers = session_headers(Path(args.headers_file))

    out = Path(args.out_dir)
    files_dir = out / "files"
    pages_dir = out / "pages"
    files_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)

    for item in items:
        kind = item.get("type")
        title = str(item.get("title") or "").strip() or f"item-{item.get('id')}"
        safe_title = sanitize_teacher_title(title.removesuffix(".pdf"))

        if kind == "File":
            file_id = str(item.get("content_id") or "")
            if not file_id:
                continue
            target = files_dir / f"{safe_title}.pdf"
            if target.exists():
                print(f"  [File] already present: {target.name}")
                continue
            print(f"  [File] downloading {target.name}")
            download_canvas_file(file_id, target, headers)

        elif kind == "Page":
            page_url = item.get("page_url")
            if not page_url:
                continue
            url = f"https://{CANVAS_NETLOC}/api/v1/courses/{args.course_id}/pages/{urllib.parse.quote(str(page_url), safe='')}"
            target_json = pages_dir / f"{safe_title}.json"
            target_md = pages_dir / f"{safe_title}.md"
            if target_json.exists() and target_md.exists():
                print(f"  [Page] already present: {target_md.name}")
                continue
            print(f"  [Page] fetching {target_md.name}")
            page_json = request_json(url, headers)
            target_json.write_text(json.dumps(page_json, ensure_ascii=False, indent=2), encoding="utf-8")
            target_md.write_text(page_markdown(page_json), encoding="utf-8")

    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
