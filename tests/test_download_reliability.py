"""Offline regression tests for discovery, transport, identity and CLI results."""
import contextlib
import hashlib
import http.client
import io
import json
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ntu_cool_materials import cli, course_pipeline as p, http_io
from ntu_cool_materials.announcements import html_to_markdown, canvas_file_links
from ntu_cool_materials.canvas_client import CanvasClient, SessionExpiredError
from ntu_cool_materials.session_client import CanvasSessionClient
from ntu_cool_materials.storage import ManifestStore, atomic_write_text, sha256_file


class Response(io.BytesIO):
    def __init__(self, body=b"data", status=200, **headers):
        super().__init__(body)
        self.status = status
        self.headers = headers


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root / "file.pdf"
        self.sleep = patch.object(http_io.time, "sleep").start()
        self.addCleanup(patch.stopall)

    def test_short_real_http_response_does_not_replace_previous_file(self):
        class Socket:
            def makefile(self, *args):
                return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nabc")
        response = http.client.HTTPResponse(Socket())
        response.begin()
        self.target.write_bytes(b"previous")
        with self.assertRaises(http_io.DownloadError):
            http_io.stream_response(response, self.target)
        self.assertEqual(self.target.read_bytes(), b"previous")
        self.assertEqual(self.target.with_name("file.pdf.part").read_bytes(), b"abc")

    def test_wrong_range_and_html_are_not_promoted(self):
        for response, offset in [
            (Response(b"abc", 206, **{"Content-Range": "bytes 0-2/3", "Content-Length": "3"}), 3),
            (Response(b"<html>login</html>", **{"Content-Type": "text/html"}), 0),
            (Response(b"<html>login</html>"), 0),
        ]:
            with self.subTest(offset=offset):
                self.target.with_name("file.pdf.part").write_bytes(b"old")
                with self.assertRaises((http_io.DownloadError, SessionExpiredError)):
                    http_io.stream_response(response, self.target, offset=offset)
                self.assertFalse(self.target.exists())

    def test_resume_sends_validator_and_only_appends_correct_range(self):
        first = Response(b"abc", **{"Content-Length": "6", "ETag": '"v1"'})
        second = Response(b"def", 206, **{"Content-Length": "3", "ETag": '"v1"', "Content-Range": "bytes 3-5/6"})
        with patch.object(http_io, "open_url", side_effect=[first, second]) as opened:
            http_io.download("https://cdn.invalid/file", self.target, lambda _: {}, attempts=2)
        self.assertEqual(self.target.read_bytes(), b"abcdef")
        sent = opened.call_args.kwargs["extra_headers"]
        self.assertEqual(sent["Range"], "bytes=3-")
        self.assertEqual(sent["If-Range"], '"v1"')
        self.assertFalse(self.target.with_name("file.pdf.part.json").exists())

    def test_legacy_partial_without_validator_is_restarted(self):
        self.target.with_name("file.pdf.part").write_bytes(b"OLD")
        with patch.object(http_io, "open_url", return_value=Response(b"new", **{"Content-Length": "3"})) as opened:
            http_io.download("https://cdn.invalid/file", self.target, lambda _: {})
        self.assertNotIn("Range", opened.call_args.kwargs["extra_headers"])
        self.assertEqual(self.target.read_bytes(), b"new")

    def test_if_range_returns_200_restarts_without_concatenation(self):
        first = Response(b"abc", **{"Content-Length": "6", "ETag": '"v1"'})
        second = Response(b"new", **{"Content-Length": "3", "ETag": '"v2"'})
        with patch.object(http_io, "open_url", side_effect=[first, second]):
            http_io.download("https://cdn.invalid/file", self.target, lambda _: {}, attempts=2)
        self.assertEqual(self.target.read_bytes(), b"new")

    def test_416_restarts_without_range(self):
        first = Response(b"abc", **{"Content-Length": "6", "ETag": '"v1"'})
        error = urllib.error.HTTPError("https://cdn.invalid/file", 416, "range", {}, None)
        with patch.object(http_io, "open_url", side_effect=[first, error, Response(b"fresh")]) as opened:
            http_io.download("https://cdn.invalid/file", self.target, lambda _: {})
        self.assertEqual(self.target.read_bytes(), b"fresh")
        self.assertNotIn("Range", opened.call_args.kwargs["extra_headers"])

    def test_changed_validator_never_mixes_old_and_new_bytes(self):
        responses = [Response(b"abc", **{"Content-Length": "6", "ETag": '"v1"'}),
                     Response(b"XYZ", 206, **{"Content-Length": "3", "ETag": '"v2"', "Content-Range": "bytes 3-5/6"}),
                     Response(b"uvwxyz", **{"Content-Length": "6", "ETag": '"v2"'})]
        with patch.object(http_io, "open_url", side_effect=responses) as opened:
            http_io.download("https://cdn.invalid/file", self.target, lambda _: {})
        self.assertEqual(self.target.read_bytes(), b"uvwxyz")
        self.assertNotIn("Range", opened.call_args.kwargs["extra_headers"])

    def test_rejected_new_response_cannot_relabel_old_partial_with_new_validator(self):
        url = "https://cdn.invalid/file"
        part = self.target.with_name("file.pdf.part")
        part.write_bytes(b"OLD")
        part.with_name(part.name + ".json").write_text(json.dumps({
            "identity": hashlib.sha256(url.encode()).hexdigest(), "validator": '"v1"'}))
        html = Response(b"<html>login</html>", **{"ETag": '"v2"', "Content-Type": "text/html"})
        with patch.object(http_io, "open_url", return_value=html):
            with self.assertRaises(SessionExpiredError):
                http_io.download(url, self.target, lambda _: {})
        self.assertFalse(part.exists())
        self.assertFalse(self.target.exists())

    def test_retry_budget_is_bounded_and_signed_query_is_not_reported(self):
        error = urllib.error.HTTPError("https://cdn.invalid/file?signature=synthetic", 503, "busy", {}, None)
        with patch.object(http_io, "open_url", side_effect=error) as opened:
            with self.assertRaises(http_io.DownloadError) as raised:
                http_io.download(error.url, self.target, lambda _: {})
        self.assertEqual(opened.call_count, 3)
        self.assertNotIn("signature", str(raised.exception))

    def test_origin_safe_download_redirect_and_https_downgrade(self):
        for location in ("https://cdn.invalid/file", "http://canvas.invalid/file"):
            opener = Mock()
            opener.open.side_effect = [urllib.error.HTTPError("https://canvas.invalid/file", 302, "redirect", {"Location": location}, None), Response()]
            with patch.object(http_io.urllib.request, "build_opener", return_value=opener):
                headers = lambda url: {"Cookie": "synthetic"} if http_io.origin(url) == ("https", "canvas.invalid") else {}
                if location.startswith("http:"):
                    with self.assertRaises(http_io.DownloadError):
                        http_io.open_url("https://canvas.invalid/file", headers)
                    self.assertEqual(opener.open.call_count, 1)
                else:
                    http_io.open_url("https://canvas.invalid/file", headers).close()
                    self.assertFalse(opener.open.call_args.args[0].has_header("Cookie"))

    def test_both_json_clients_block_off_origin_redirect(self):
        clients = [CanvasClient("https://canvas.invalid", "synthetic"),
                   CanvasSessionClient("https://canvas.invalid", {"Cookie": "synthetic"})]
        for client in clients:
            opener = Mock()
            opener.open.side_effect = urllib.error.HTTPError("https://canvas.invalid/api", 302, "redirect", {"Location": "https://sso.invalid/login"}, None)
            with patch.object(http_io.urllib.request, "build_opener", return_value=opener):
                with self.assertRaises(SessionExpiredError):
                    client.get_json("/api/v1/users/self")
            self.assertEqual(opener.open.call_count, 1)

    def test_retry_after_and_no_retry_for_forbidden(self):
        for code, calls in ((429, 2), (403, 1)):
            error = urllib.error.HTTPError("https://canvas.invalid/api", code, "error", {"Retry-After": "2"}, None)
            with patch.object(http_io, "open_url", side_effect=[error, Response(b"[]")]) as opened:
                if code == 403:
                    with self.assertRaises(urllib.error.HTTPError):
                        http_io.request_json("https://canvas.invalid/api", lambda _: {})
                else:
                    self.assertEqual(http_io.request_json("https://canvas.invalid/api", lambda _: {})[0], [])
                    self.sleep.assert_called_with(2.0)
                self.assertEqual(opened.call_count, calls)

    def test_empty_and_metadata_size_mismatch_rejected(self):
        for response, size in [(Response(b""), None), (Response(b"abc", **{"Content-Length": "3"}), 4)]:
            with self.assertRaises(http_io.DownloadError):
                http_io.stream_response(response, self.target, expected_size=size)
        self.assertFalse(self.target.exists())

    def test_failed_atomic_write_preserves_existing_text(self):
        self.target.write_text("original", encoding="utf-8")
        with patch.object(Path, "replace", side_effect=OSError("synthetic disk error")):
            with self.assertRaises(OSError):
                atomic_write_text(self.target, "updated")
        self.assertEqual(self.target.read_text(), "original")
        self.assertFalse(list(self.root.glob("*.tmp")))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)
        self.client = Mock()
        self.client.headers = {}
        self.client.base_url = "https://cool.ntu.edu.tw"
        self.client.get_course.return_value = {"id": "1", "name": "Synthetic"}

    def plan(self, items):
        return p.CoursePlan({"id": "1", "name": "Synthetic"}, "1", self.root,
                            [p.WeekPlan("week1", {"id": 10, "items": items}, self.root / "week1")])

    def test_module_items_fallback_and_stable_duplicate_week_directories(self):
        modules = [{"id": 10, "name": "Week 1", "items_count": 1},
                   {"id": 11, "name": "Week 1", "items": [{"type": "Page"}]}]
        def listing(path, params=None):
            return iter(modules if path.endswith("/modules") else [{"type": "File", "content_id": 1}])
        self.client.list_paginated.side_effect = listing
        plan = p.plan_course(self.client, "1", self.root)
        self.assertEqual(len(plan.weeks), 2)
        self.assertNotEqual(plan.weeks[0].week_dir, plan.weeks[1].week_dir)
        old = {w.module["id"]: w.week_dir for w in plan.weeks}
        modules.reverse()
        again = p.plan_course(self.client, "1", self.root)
        self.assertEqual(old, {w.module["id"]: w.week_dir for w in again.weeks})

    def test_file_collision_update_size_corruption_and_hash_verification(self):
        items = [{"id": i, "content_id": i, "type": "File", "title": "Lecture.pdf"} for i in (1, 2)]
        version = ["v1"]
        self.client.get_json.side_effect = lambda _: {"display_name": "Lecture.pdf", "size": 3, "updated_at": version[0]}
        def download(fid, path, headers, **kwargs):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"abc")
        with patch.object(p, "_download_canvas_file", side_effect=download) as dl:
            plan = self.plan(items)
            self.assertEqual(p.download_files(plan, self.client).done, 2)
            self.assertEqual(len(list((self.root / "week1").glob("*.pdf"))), 2)
            self.assertEqual(p.download_files(self.plan(items), self.client).skipped, 2)
            version[0] = "v2"
            self.assertEqual(p.download_files(self.plan(items), self.client).done, 2)
            Path(items[0]["_local_path"]).write_bytes(b"x")
            self.assertEqual(p.download_files(self.plan(items), self.client).done, 1)
            Path(items[0]["_local_path"]).write_bytes(b"xyz")
            verified = self.plan(items)
            verified.verify_files = True
            self.assertEqual(p.download_files(verified, self.client).done, 1)

    def test_page_updates_preserve_links_and_download_canvas_attachments(self):
        item = {"id": 1, "type": "Page", "page_url": "notes", "title": "Notes"}
        body = ["<p>v1 <a href='/courses/1/files/7/download'>Sheet</a><img src='/files/8/preview' alt='Figure'></p>"]
        def get(path):
            if "/pages/" in path:
                return {"title": "Notes", "body": body[0]}
            return {"display_name": f"{path.rsplit('/', 1)[-1]}.pdf", "size": 3}
        self.client.get_json.side_effect = get
        def download(fid, path, headers, **kwargs):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"abc")
        with patch.object(p, "_download_canvas_file", side_effect=download) as dl:
            self.assertEqual(p.save_pages(self.plan([item]), self.client, "1").done, 1)
            text = Path(item["_local_path"]).read_text(encoding="utf-8")
            self.assertIn("[Sheet](<https://cool.ntu.edu.tw/courses/1/files/7/download>)", text)
            self.assertIn("![Figure]", text)
            self.assertEqual(dl.call_count, 2)
            body[0] = "<p>v2</p>"
            self.assertEqual(p.save_pages(self.plan([item]), self.client, "1").done, 1)
            self.assertIn("v2", Path(item["_local_path"]).read_text())

    def test_file_transfers_are_bounded_and_can_overlap(self):
        items = [{"id": i, "content_id": i, "type": "File", "title": f"{i}.pdf"} for i in range(1, 5)]
        self.client.get_json.return_value = {"size": 3}
        barrier = threading.Barrier(2)
        def download(fid, target, headers, **kwargs):
            barrier.wait(timeout=5)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"abc")
        plan = self.plan(items)
        plan.workers = 2
        with patch.object(p, "_download_canvas_file", side_effect=download):
            stats = p.download_files(plan, self.client)
        self.assertEqual(stats.done, 4)
        self.assertFalse(stats.failed)

    def test_failed_file_returns_nonzero_cli_and_structured_report(self):
        headers = self.root / "headers.txt"
        headers.write_text("User-Agent: synthetic")
        args = cli._build_parser().parse_args(["download-course", "--course-id", "1", "--out", str(self.root),
            "--headers-file", str(headers), "--skip-pages", "--skip-announcements", "--skip-youtube", "--skip-cool-videos"])
        with patch.object(p, "plan_course", return_value=self.plan([])), \
             patch.object(p, "download_files", return_value=p.StageStats(failed=["synthetic failure"])):
            self.assertEqual(cli._cmd_download_course(self.client.base_url, args), 1)
        report = json.loads((self.root / "download_report.json").read_text())
        self.assertFalse(report["successful"])
        self.assertTrue(report["stages"]["pages"]["disabled"])

    def test_session_recovery_reopens_browser_and_closes_it(self):
        browser = Mock()
        with patch.object(p, "plan_course", side_effect=[SessionExpiredError("expired"), self.plan([])]), \
             patch.object(p.sys.stdin, "isatty", return_value=True), \
             patch.object(p, "open_browser_session", return_value=browser) as opened, \
             patch.object(p, "_ensure_logged_in", return_value=True), \
             patch.object(p, "_dump_cookies_to_headers_file", return_value=True), \
             patch.object(p, "_build_session_client_from_file", return_value=self.client):
            p.download_course(course_id="1", output_dir=self.root, client=self.client,
                              skip_announcements=True, skip_pdfs=True, skip_pages=True,
                              skip_youtube=True, skip_cool_videos=True)
        opened.assert_called_once()
        browser.close.assert_called_once()

    def test_initial_login_browser_closes_if_header_loading_fails(self):
        browser = Mock()
        with patch.object(p, "open_browser_session", return_value=browser), \
             patch.object(p, "_dump_cookies_to_headers_file", return_value=True), \
             patch.object(p, "_build_session_client_from_file", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError):
                p.download_course(course_id="1", output_dir=self.root, refresh_session=True)
        browser.close.assert_called_once()

    def test_picker_does_not_mark_failed_course_done_and_retry_can_recover(self):
        headers = self.root / "headers.txt"
        headers.write_text("User-Agent: synthetic")
        self.client.list_courses.return_value = [{"id": "1", "name": "Synthetic"}]
        self.client.check_auth.return_value = "ok"
        args = cli._build_parser().parse_args(["pick", "--headers-file", str(headers), "--out", str(self.root),
                                              "--keep-terminal", "--no-notebooklm"])
        failed = self.plan([])
        failed.stats = p.CourseStats(pdfs=p.StageStats(failed=["synthetic failure"]))
        success = self.plan([])
        success.stats = p.CourseStats()
        for replies, plans, expected in [(["1", "q"], [failed], 1),
                                         (["1", "a", "q"], [failed, success], 0)]:
            with patch.object(cli, "ensure_ready", return_value=True), \
                 patch.object(cli, "check_for_update", return_value=None), \
                 patch.object(cli, "CanvasSessionClient", return_value=self.client), \
                 patch.object(cli, "download_course", side_effect=plans) as download, \
                 patch("builtins.input", side_effect=replies):
                self.assertEqual(cli._cmd_pick(self.client.base_url, args), expected)
            self.assertEqual(download.call_count, len(plans))

    def test_picker_import_has_only_one_confirmation(self):
        headers = self.root / "headers.txt"
        headers.write_text("User-Agent: synthetic")
        self.client.list_courses.return_value = [{"id": "1", "name": "Synthetic"}]
        self.client.check_auth.return_value = "ok"
        plan = self.plan([])
        plan.stats = p.CourseStats()
        for flag, answer, imports, prompts in [
            ([], "y", 1, 1), ([], "n", 0, 1),
            (["--notebooklm"], "n", 1, 0), (["--no-notebooklm"], "y", 0, 0),
        ]:
            args = cli._build_parser().parse_args([
                "pick", "--headers-file", str(headers), "--out", str(self.root),
                "--keep-terminal", *flag])
            with patch.object(cli, "ensure_ready", return_value=True), \
                 patch.object(cli, "check_for_update", return_value=None), \
                 patch.object(cli, "CanvasSessionClient", return_value=self.client), \
                 patch.object(cli, "download_course", return_value=plan), \
                 patch.object(cli.sys.stdin, "isatty", return_value=True), \
                 patch("ntu_cool_materials.notebooklm_flow.choose", return_value=answer) as choose, \
                 patch.object(cli, "_cmd_notebooklm", return_value=0) as do_import, \
                 patch("builtins.input", side_effect=["1", "q"]):
                self.assertEqual(cli._cmd_pick(self.client.base_url, args), 0)
            self.assertEqual(choose.call_count, prompts)
            self.assertEqual(do_import.call_count, imports)
            if imports:
                self.assertFalse(do_import.call_args.kwargs.get("guided", False))
                self.assertEqual(do_import.call_args.args[0], plan.course_dir)

    def test_unknown_real_extension_is_preserved_without_doubling(self):
        item = {"title": "exercise.py", "content_details": {"filename": "exercise.py"}}
        self.assertEqual(p._file_item_target_name(item, all_file_types=False), "exercise.py")

    def test_youtube_only_submits_missing_ids_and_reuses_across_weeks(self):
        items = [{"type": "ExternalUrl", "title": title, "external_url": f"https://youtu.be/{vid}"}
                 for title, vid in (("A", "aaaaaaaaaaa"), ("B", "bbbbbbbbbbb"))]
        plan = self.plan(items)
        batches = []
        def run(cmd, **kwargs):
            batch = Path(cmd[cmd.index("-a") + 1]).read_text().splitlines()
            batches.append(batch)
            cache = Path(cmd[cmd.index("-P") + 1])
            # First pass fails B; second pass downloads B only.
            for url in batch:
                vid = url.rsplit("/", 1)[-1]
                if len(batches) > 1 or vid == "aaaaaaaaaaa":
                    (cache / f"{vid}.mp4").write_bytes(b"synthetic mp4")
            return SimpleNamespace(returncode=1 if len(batches) == 1 else 0)
        with patch.object(p.shutil, "which", return_value="tool"), \
             patch.object(p.subprocess, "run", side_effect=run), \
             patch.object(p, "_valid_video", side_effect=lambda path: path.is_file()):
            first = p.download_youtube(plan, cookies_path=self.root / "none")
            self.assertEqual(len(first.failed), 1)
            second = p.download_youtube(plan, cookies_path=self.root / "none")
            self.assertEqual(batches[1], ["https://youtu.be/bbbbbbbbbbb"])
            self.assertFalse(second.failed)
            plan.weeks.append(p.WeekPlan("week2", {"id": 20, "items": items}, self.root / "week2"))
            third = p.download_youtube(plan, cookies_path=self.root / "none")
            self.assertEqual(len(batches), 2)
            self.assertEqual(third.done, 2)

    def test_missing_ffmpeg_is_reported_as_failure(self):
        plan = self.plan([{"type": "ExternalUrl", "title": "A", "external_url": "https://youtu.be/aaaaaaaaaaa"}])
        with patch.object(p.shutil, "which", return_value=None):
            self.assertEqual(len(p.download_youtube(plan, cookies_path=self.root / "none").failed), 1)

    def test_complete_cool_video_does_not_open_browser(self):
        plan = self.plan([{"id": 1, "type": "ExternalTool", "title": "A", "external_url": "https://cool-video.dlc.ntu.edu.tw/courses/1/videos/2"}])
        pending, _ = p._pending_cool_videos(plan)
        _, _, _, key, target, version = pending[0]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"verified video")
        with p._manifest(plan) as store:
            store.record_artifact(key, target, version)
        with patch("playwright.sync_api.sync_playwright") as browser:
            stats = p.capture_and_download_cool_videos(plan, course_id="1")
        browser.assert_not_called()
        self.assertEqual(stats.skipped, 1)

    def test_pre_manifest_files_are_adopted_not_downloaded_again(self):
        items = [{"id": i, "content_id": i, "type": "File", "title": title}
                 for i, title in ((1, "Kept.pdf"), (2, "Stale.pdf"))]
        self.client.get_json.side_effect = lambda path: {
            "display_name": "Kept.pdf" if path.endswith("/1") else "Stale.pdf", "size": 3, "updated_at": "v1"}
        week = self.root / "week1"
        week.mkdir()
        (week / "Kept.pdf").write_bytes(b"abc")
        (week / "Stale.pdf").write_bytes(b"old download")
        def download(fid, path, headers, **kwargs):
            path.write_bytes(b"new")
        with patch.object(p, "_download_canvas_file", side_effect=download) as dl:
            stats = p.download_files(self.plan(items), self.client)
            self.assertEqual((stats.skipped, stats.done), (1, 1))
            self.assertEqual([c.args[0] for c in dl.call_args_list], ["2"])
            self.assertEqual((week / "Kept.pdf").read_bytes(), b"abc")
            self.assertEqual(p.download_files(self.plan(items), self.client).skipped, 2)

    def test_pre_manifest_videos_are_adopted_without_downloader_or_browser(self):
        plan = self.plan([
            {"type": "ExternalUrl", "title": "Talk", "external_url": "https://youtu.be/aaaaaaaaaaa"},
            {"id": 1, "type": "ExternalTool", "title": "Lecture",
             "external_url": "https://cool-video.dlc.ntu.edu.tw/courses/1/videos/2"},
        ])
        week = self.root / "week1"
        week.mkdir()
        (week / "Talk.mp4").write_bytes(b"legacy youtube")
        (week / "Lecture.mp4").write_bytes(b"legacy cool video")
        with patch.object(p.subprocess, "run") as run, \
             patch.object(p, "_valid_video", side_effect=lambda path: path.is_file()), \
             patch("playwright.sync_api.sync_playwright") as browser:
            youtube = p.download_youtube(plan, cookies_path=self.root / "none")
            cool = p.capture_and_download_cool_videos(plan, course_id="1")
        run.assert_not_called()
        browser.assert_not_called()
        self.assertEqual((youtube.skipped, youtube.done, cool.skipped), (1, 0, 1))
        self.assertFalse((self.root / ".media-cache").exists())

    def test_manifest_detects_local_damage_even_when_remote_is_unchanged(self):
        path = self.root / "old.pdf"
        path.write_bytes(b"abc")
        info = {"id": "1", "size": 3, "updated_at": "v1"}
        with ManifestStore(self.root / "manifest.sqlite3") as store:
            store.upsert_file(file_info=info, course_id="1", course_name="Test", local_path=path, sha256=sha256_file(path))
            path.write_bytes(b"x")
            self.assertTrue(store.needs_download(info, path))
            path.write_bytes(b"xyz")
            self.assertTrue(store.needs_download(info, path, verify_hash=True))

    def test_external_links_are_preserved_but_not_crawled(self):
        html = '<a href="https://other.invalid/files/123">External</a>'
        self.assertIn("https://other.invalid/files/123", html_to_markdown(html))
        self.assertEqual(canvas_file_links(html, self.client.base_url), {})


if __name__ == "__main__":
    unittest.main()
