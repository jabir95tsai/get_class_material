from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import requests
from urllib3.exceptions import InsecureRequestWarning


USER_AGENT = "ntu-cool-materials/0.1"
SEGMENT_SUFFIX = ".m4s"
SEGMENT_FILENAME_RE = re.compile(r"^(?:audio|video)-.+-\d+\.m4s$")


class CoolVideoError(RuntimeError):
    """Raised when NTU COOL video segment download or muxing fails."""


@dataclass(frozen=True)
class SegmentSeries:
    label: str
    prefix: str
    parts: list[Path]


@dataclass(frozen=True)
class DownloadResult:
    video_segments: int
    audio_segments: int
    output_path: Path


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        result = download_cool_video(
            base_url=args.base_url,
            output_path=Path(args.output),
            quality=args.quality,
            start=args.start,
            work_dir=Path(args.work_dir) if args.work_dir else None,
            keep_temp=args.keep_temp,
            timeout=args.timeout,
            retries=args.retries,
            ffmpeg=args.ffmpeg,
            overwrite=args.overwrite,
            video_init_url=args.video_init_url,
            audio_init_url=args.audio_init_url,
            video_init_file=Path(args.video_init_file) if args.video_init_file else None,
            audio_init_file=Path(args.audio_init_file) if args.audio_init_file else None,
            verify_tls=not args.insecure,
            reuse_existing=args.reuse_existing,
        )
    except CoolVideoError as exc:
        print(f"error: {exc}")
        return 1
    except KeyboardInterrupt:
        print("interrupted")
        return 130

    print(
        "done: "
        f"{result.output_path} "
        f"({result.video_segments} video segments, {result.audio_segments} audio segments)"
    )
    return 0


def download_cool_video(
    *,
    base_url: str,
    output_path: Path,
    quality: str = "480",
    start: int = 1,
    work_dir: Path | None = None,
    keep_temp: bool = False,
    timeout: float = 30.0,
    retries: int = 3,
    ffmpeg: str = "ffmpeg",
    overwrite: bool = True,
    video_init_url: str | None = None,
    audio_init_url: str | None = None,
    video_init_file: Path | None = None,
    audio_init_file: Path | None = None,
    verify_tls: bool = True,
    reuse_existing: bool = False,
) -> DownloadResult:
    if start < 1:
        raise CoolVideoError("--start must be 1 or greater.")

    normalized_base_url = normalize_base_url(base_url)
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    session.verify = verify_tls
    if not verify_tls:
        warnings.simplefilter("ignore", InsecureRequestWarning)

    if keep_temp:
        root = (work_dir or output_path.with_suffix(".segments")).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return _download_in_work_dir(
            session=session,
            base_url=normalized_base_url,
            output_path=output_path,
            quality=quality,
            start=start,
            work_dir=root,
            timeout=timeout,
            retries=retries,
            ffmpeg=ffmpeg,
            overwrite=overwrite,
            video_init_url=video_init_url,
            audio_init_url=audio_init_url,
            video_init_file=video_init_file,
            audio_init_file=audio_init_file,
            verify_tls=verify_tls,
            reuse_existing=reuse_existing,
        )

    if work_dir:
        work_dir = work_dir.resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        temp_context = tempfile.TemporaryDirectory(prefix="ntu-cool-video-", dir=work_dir)
    else:
        temp_context = tempfile.TemporaryDirectory(prefix="ntu-cool-video-")

    with temp_context as temp_name:
        return _download_in_work_dir(
            session=session,
            base_url=normalized_base_url,
            output_path=output_path,
            quality=quality,
            start=start,
            work_dir=Path(temp_name),
            timeout=timeout,
            retries=retries,
            ffmpeg=ffmpeg,
            overwrite=overwrite,
            video_init_url=video_init_url,
            audio_init_url=audio_init_url,
            video_init_file=video_init_file,
            audio_init_file=audio_init_file,
            verify_tls=verify_tls,
            reuse_existing=reuse_existing,
        )


def normalize_base_url(base_url: str) -> str:
    stripped = base_url.strip()
    parsed = urllib.parse.urlparse(stripped)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CoolVideoError("base_url must be an absolute http(s) URL.")

    path = parsed.path
    filename = Path(path).name
    if SEGMENT_FILENAME_RE.match(filename):
        parent = path.rsplit("/", 1)[0] + "/"
        parsed = parsed._replace(path=parent, params="", query="", fragment="")
        return urllib.parse.urlunparse(parsed)

    return stripped.rstrip("/") + "/"


def segment_url(base_url: str, prefix: str, index: int) -> str:
    if index < 1:
        raise ValueError("segment index must be 1 or greater.")
    filename = urllib.parse.quote(f"{prefix}{index}{SEGMENT_SUFFIX}")
    return urllib.parse.urljoin(normalize_base_url(base_url), filename)


def merge_segments(parts: Iterable[Path], target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with target_path.open("wb") as output:
        for part in parts:
            with part.open("rb") as input_file:
                shutil.copyfileobj(input_file, output, length=1024 * 1024)


def probe_top_level_mp4_boxes(path: Path, limit_bytes: int = 256 * 1024) -> set[str]:
    data = path.read_bytes()[:limit_bytes]
    offset = 0
    boxes: set[str] = set()

    while offset + 8 <= len(data):
        size = int.from_bytes(data[offset : offset + 4], "big")
        box_type_bytes = data[offset + 4 : offset + 8]
        try:
            box_type = box_type_bytes.decode("ascii")
        except UnicodeDecodeError:
            break

        if not box_type.isprintable():
            break

        boxes.add(box_type)
        if size == 1 and offset + 16 <= len(data):
            size = int.from_bytes(data[offset + 8 : offset + 16], "big")
        elif size == 0:
            break

        if size < 8:
            break
        offset += size

    return boxes


def _download_in_work_dir(
    *,
    session: requests.Session,
    base_url: str,
    output_path: Path,
    quality: str,
    start: int,
    work_dir: Path,
    timeout: float,
    retries: int,
    ffmpeg: str,
    overwrite: bool,
    video_init_url: str | None,
    audio_init_url: str | None,
    video_init_file: Path | None,
    audio_init_file: Path | None,
    verify_tls: bool,
    reuse_existing: bool,
) -> DownloadResult:
    _validate_init_inputs(video_init_url, video_init_file, "video")
    _validate_init_inputs(audio_init_url, audio_init_file, "audio")

    video_prefix = f"video-{quality}-"
    audio_prefix = f"audio-{quality}-"

    print(f"base: {base_url}")
    print(f"temp: {work_dir}")
    if not verify_tls:
        print("tls: certificate verification disabled by --insecure")

    video = _download_series(
        session=session,
        base_url=base_url,
        label="video",
        prefix=video_prefix,
        init_url=video_init_url,
        init_file=video_init_file,
        start=start,
        target_dir=work_dir / "video",
        timeout=timeout,
        retries=retries,
        reuse_existing=reuse_existing,
    )
    audio = _download_series(
        session=session,
        base_url=base_url,
        label="audio",
        prefix=audio_prefix,
        init_url=audio_init_url,
        init_file=audio_init_file,
        start=start,
        target_dir=work_dir / "audio",
        timeout=timeout,
        retries=retries,
        reuse_existing=reuse_existing,
    )

    _print_init_hint(video)
    _print_init_hint(audio)

    merged_video = work_dir / "merged-video.mp4"
    merged_audio = work_dir / "merged-audio.m4a"
    merge_segments(video.parts, merged_video)
    merge_segments(audio.parts, merged_audio)

    _mux_with_ffmpeg(
        video_path=merged_video,
        audio_path=merged_audio,
        output_path=output_path,
        ffmpeg=ffmpeg,
        overwrite=overwrite,
    )

    return DownloadResult(
        video_segments=len(video.parts),
        audio_segments=len(audio.parts),
        output_path=output_path,
    )


def _download_series(
    *,
    session: requests.Session,
    base_url: str,
    label: str,
    prefix: str,
    init_url: str | None,
    init_file: Path | None,
    start: int,
    target_dir: Path,
    timeout: float,
    retries: int,
    reuse_existing: bool,
) -> SegmentSeries:
    target_dir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []

    if init_url:
        init_path = target_dir / f"{label}-init{SEGMENT_SUFFIX}"
        print(f"{label}: downloading explicit init segment")
        _download_required_url(session, init_url, init_path, timeout=timeout, retries=retries)
        parts.append(init_path)
    elif init_file:
        init_path = target_dir / f"{label}-init{SEGMENT_SUFFIX}"
        print(f"{label}: copying explicit init segment")
        if not init_file.exists():
            raise CoolVideoError(f"{label} init file does not exist: {init_file}")
        shutil.copyfile(init_file, init_path)
        parts.append(init_path)

    index = start
    while True:
        url = segment_url(base_url, prefix, index)
        path = target_dir / f"{prefix}{index}{SEGMENT_SUFFIX}"
        if reuse_existing and path.exists() and path.stat().st_size > 0:
            parts.append(path)
            if len(parts) == 1 or len(parts) % 25 == 0:
                print(f"{label}: reused {len(parts)} existing segment(s)")
            index += 1
            continue

        found = _download_optional_url(session, url, path, timeout=timeout, retries=retries)
        if not found:
            break

        parts.append(path)
        if len(parts) == 1 or len(parts) % 25 == 0:
            print(f"{label}: downloaded {len(parts)} segment(s)")
        index += 1

    if not parts:
        raise CoolVideoError(f"No {label} segments found for prefix {prefix!r}.")

    print(f"{label}: stopped at 404 after {len(parts)} segment(s)")
    return SegmentSeries(label=label, prefix=prefix, parts=parts)


def _download_optional_url(
    session: requests.Session,
    url: str,
    target_path: Path,
    *,
    timeout: float,
    retries: int,
) -> bool:
    response = _get_with_retries(session, url, timeout=timeout, retries=retries)
    if response.status_code == 404:
        response.close()
        return False

    try:
        _raise_for_status(response, url)
        _write_response(response, target_path)
    finally:
        response.close()
    return True


def _download_required_url(
    session: requests.Session,
    url: str,
    target_path: Path,
    *,
    timeout: float,
    retries: int,
) -> None:
    response = _get_with_retries(session, url, timeout=timeout, retries=retries)
    try:
        _raise_for_status(response, url)
        _write_response(response, target_path)
    finally:
        response.close()


def _get_with_retries(
    session: requests.Session,
    url: str,
    *,
    timeout: float,
    retries: int,
) -> requests.Response:
    last_error: requests.RequestException | None = None
    attempts = max(1, retries + 1)

    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, stream=True, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(2.0, 0.25 * attempt))
                continue
            raise CoolVideoError(f"Request failed for {url}: {exc}") from exc

        if response.status_code < 500 or attempt == attempts:
            return response

        response.close()
        time.sleep(min(2.0, 0.25 * attempt))

    raise CoolVideoError(f"Request failed for {url}: {last_error}")


def _raise_for_status(response: requests.Response, url: str) -> None:
    if response.status_code >= 400:
        body = response.text[:500] if response.text else ""
        detail = f": {body}" if body else ""
        raise CoolVideoError(f"HTTP {response.status_code} while downloading {url}{detail}")


def _write_response(response: requests.Response, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_name(f"{target_path.name}.part")
    with temp_path.open("wb") as output:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                output.write(chunk)

    if temp_path.stat().st_size == 0:
        temp_path.unlink(missing_ok=True)
        raise CoolVideoError(f"Downloaded empty segment from {response.url}")

    temp_path.replace(target_path)


def _mux_with_ffmpeg(
    *,
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    ffmpeg: str,
    overwrite: bool,
) -> None:
    command = [
        ffmpeg,
        "-y" if overwrite else "-n",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    print("ffmpeg: muxing video + audio")
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        hint = (
            "\nHint: if ffmpeg says the input has no moov/traf/trex metadata, "
            "the numbered segments probably do not include the init segment. "
            "Find the init segment URL in DevTools and pass --video-init-url and --audio-init-url."
        )
        raise CoolVideoError(f"ffmpeg failed with exit code {completed.returncode}:\n{stderr}{hint}")


def _validate_init_inputs(init_url: str | None, init_file: Path | None, label: str) -> None:
    if init_url and init_file:
        raise CoolVideoError(f"Use either --{label}-init-url or --{label}-init-file, not both.")


def _print_init_hint(series: SegmentSeries) -> None:
    boxes = probe_top_level_mp4_boxes(series.parts[0])
    if {"ftyp", "moov"}.issubset(boxes):
        print(f"{series.label}: first segment looks like an init segment ({', '.join(sorted(boxes))})")
    elif "moof" in boxes or "mdat" in boxes:
        print(
            f"{series.label}: first segment looks like media data only; "
            "ffmpeg may need an explicit init segment"
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="download_cool_video.py",
        description="Download NTU COOL CDN MPEG-DASH .m4s video/audio segments and mux them into MP4.",
    )
    parser.add_argument("base_url", help="Video base URL ending at the UUID directory.")
    parser.add_argument("-o", "--output", default="output.mp4", help="Output MP4 path.")
    parser.add_argument("--quality", default="480", help="Segment quality in video-{quality}-{n}.m4s.")
    parser.add_argument("--start", type=int, default=1, help="First numbered segment index.")
    parser.add_argument("--work-dir", default=None, help="Directory for temporary segment files.")
    parser.add_argument("--keep-temp", action="store_true", help="Keep downloaded .m4s files for debugging.")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds.")
    parser.add_argument("--retries", type=int, default=3, help="Retries for transient request failures.")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg executable path.")
    parser.add_argument("--no-overwrite", dest="overwrite", action="store_false", help="Do not overwrite output.")
    parser.add_argument("--video-init-url", default=None, help="Optional explicit video init segment URL.")
    parser.add_argument("--audio-init-url", default=None, help="Optional explicit audio init segment URL.")
    parser.add_argument("--video-init-file", default=None, help="Optional local video init segment file.")
    parser.add_argument("--audio-init-file", default=None, help="Optional local audio init segment file.")
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="Reuse existing non-empty numbered segments in --work-dir instead of downloading them again.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification for CDN requests.",
    )
    parser.set_defaults(overwrite=True)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
