"""Build extension icons and an allow-listed GitHub Release/store ZIP."""
from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "extensions" / "notebooklm"
DIST = ROOT / "dist"
FILES = ("manifest.json", "worker.js", "panel.html", "panel.css", "panel.mjs", "page.mjs", "README.md")


def icon(size: int, target: Path) -> None:
    scale = 4
    image = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    s = size * scale
    navy = (31, 55, 78, 255)
    teal = (31, 157, 139, 255)
    paper = (245, 249, 247, 255)
    # A neutral open course book with a downward import arrow.
    draw.rounded_rectangle((s*.10, s*.16, s*.90, s*.88), radius=s*.13, fill=navy)
    draw.polygon([(s*.18,s*.27),(s*.46,s*.33),(s*.46,s*.76),(s*.18,s*.69)], fill=paper)
    draw.polygon([(s*.82,s*.27),(s*.54,s*.33),(s*.54,s*.76),(s*.82,s*.69)], fill=paper)
    draw.rounded_rectangle((s*.44,s*.08,s*.56,s*.52), radius=s*.04, fill=teal)
    draw.polygon([(s*.30,s*.43),(s*.70,s*.43),(s*.50,s*.65)], fill=teal)
    image.resize((size, size), Image.Resampling.LANCZOS).save(target, "PNG", optimize=True)


def build(output: Path, *, store: bool = False) -> Path:
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    required = {"name", "version", "description", "icons"}
    missing = required - manifest.keys()
    if missing:
        raise ValueError(f"manifest missing store fields: {sorted(missing)}")
    if store:
        manifest.pop("key", None)
    icons = SOURCE / "icons"
    icons.mkdir(exist_ok=True)
    for size in (16, 32, 48, 128):
        icon(size, icons / f"icon-{size}.png")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            if name == "manifest.json":
                archive.writestr(name, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            else:
                archive.write(SOURCE / name, name)
        for size in (16, 32, 48, 128):
            name = f"icons/icon-{size}.png"
            archive.write(SOURCE / name, name)
        archive.write(ROOT / "LICENSE", "LICENSE")
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("Extension ZIP failed integrity check")
        packaged = json.loads(archive.read("manifest.json"))
        for name in [*packaged["icons"].values(), packaged["background"]["service_worker"]]:
            if name not in archive.namelist():
                raise ValueError(f"Missing extension asset: {name}")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--store", action="store_true", help="Omit the development key for Chrome Web Store upload")
    args = parser.parse_args()
    output = args.output or DIST / ("ntu-cool-to-notebooklm-store.zip" if args.store else "ntu-cool-to-notebooklm.zip")
    print(build(output, store=args.store).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
