"""Tests for course_pipeline._ensure_logged_in.

These mock the Playwright Page object entirely — no actual browser is
launched. The function under test is a thin policy layer over Playwright,
so what matters is that we route correctly for each combination of
(goto outcome, current URL) we might see during a real SSO redirect.

Regression covered: an earlier version used `wait_until="domcontentloaded"`
with a 120s timeout; NTU's SAML chain plus a slow user typing credentials
sometimes never fires DOM-ready inside 120s, and the function crashed
instead of falling through to wait_for_url's 600s budget.
"""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from playwright.sync_api import TimeoutError as PWTimeout

from ntu_cool_materials import course_pipeline


def _make_page(*, url: str, goto_raises: Exception | None = None,
               wait_for_url_raises: Exception | None = None,
               url_after_wait: str | None = None):
    """Build a mock Page with the URL state at each step of the flow.

    `url` is what page.url reports when first read (after goto returns).
    `url_after_wait` is what page.url reports after wait_for_url returns —
    simulates the user finishing SSO. Defaults to keeping the same URL.
    """
    page = mock.MagicMock()

    # page.url is a property that can change over time. Use PropertyMock so
    # we can swap the underlying value mid-test.
    url_state = {"value": url}

    def get_url():
        return url_state["value"]

    type(page).url = mock.PropertyMock(side_effect=get_url)

    if goto_raises is not None:
        page.goto.side_effect = goto_raises
    else:
        page.goto.return_value = None

    def _wait_for_url(*args, **kwargs):
        if wait_for_url_raises is not None:
            raise wait_for_url_raises
        if url_after_wait is not None:
            url_state["value"] = url_after_wait

    page.wait_for_url.side_effect = _wait_for_url
    return page


class EnsureLoggedInTests(unittest.TestCase):
    def setUp(self) -> None:
        # `_ensure_logged_in` print()s status updates in Traditional Chinese.
        # The CLI normally calls _force_utf8_streams() to reconfigure stdout
        # before any such print, but unit tests bypass that path — and the
        # GitHub Windows runner ships with cp1252 stdout, which can't encode
        # CJK characters. Redirect stdout to a StringIO so the prints happen
        # against an in-memory UTF-8-capable stream. (No assertion looks at
        # captured output; we only care about the function's return value
        # and which Playwright methods it called.)
        self._stdout_ctx = contextlib.redirect_stdout(io.StringIO())
        self._stdout_ctx.__enter__()
        self.addCleanup(self._stdout_ctx.__exit__, None, None, None)

    def test_already_logged_in_returns_true(self) -> None:
        """Happy path: cookies are fresh, goto lands directly on /courses."""
        page = _make_page(url="https://cool.ntu.edu.tw/courses")
        ok = course_pipeline._ensure_logged_in(page, course_id=None, sso_timeout_sec=600)
        self.assertTrue(ok)
        page.goto.assert_called_once()
        page.wait_for_url.assert_not_called()

    def test_redirects_to_sso_then_user_logs_in(self) -> None:
        """Stale cookies: goto lands on SSO, wait_for_url returns once the user
        finishes typing creds. Function returns True."""
        page = _make_page(
            url="https://web2.cc.ntu.edu.tw/saml/sso?...",
            url_after_wait="https://cool.ntu.edu.tw/courses",
        )
        ok = course_pipeline._ensure_logged_in(page, course_id=None, sso_timeout_sec=600)
        self.assertTrue(ok)
        page.wait_for_url.assert_called_once()

    def test_goto_timeout_but_already_on_sso_still_proceeds(self) -> None:
        """THE REGRESSION GUARD: goto raised TimeoutError because DOM never
        fired inside its short budget, but the navigation DID commit to the
        SSO page. wait_for_url should still run and succeed."""
        page = _make_page(
            url="https://web2.cc.ntu.edu.tw/saml/sso?...",
            goto_raises=PWTimeout("Page.goto: Timeout 60000ms exceeded."),
            url_after_wait="https://cool.ntu.edu.tw/courses",
        )
        ok = course_pipeline._ensure_logged_in(page, course_id=None, sso_timeout_sec=600)
        self.assertTrue(ok, "must fall through to wait_for_url instead of bubbling TimeoutError")
        page.wait_for_url.assert_called_once()

    def test_goto_timeout_and_page_never_committed_returns_false(self) -> None:
        """Real network failure: goto timed out AND the page is still on
        about:blank (didn't commit any navigation). Bail with a clear
        error rather than waiting 10 minutes on a dead URL."""
        page = _make_page(
            url="about:blank",
            goto_raises=PWTimeout("Page.goto: Timeout 60000ms exceeded."),
        )
        ok = course_pipeline._ensure_logged_in(page, course_id=None, sso_timeout_sec=600)
        self.assertFalse(ok)

    def test_sso_login_actually_times_out(self) -> None:
        """User opens the browser but never finishes SSO. wait_for_url
        timeout should propagate as a False return, not crash."""
        page = _make_page(
            url="https://web2.cc.ntu.edu.tw/saml/sso?...",
            wait_for_url_raises=PWTimeout("Page.wait_for_url: Timeout exceeded."),
        )
        ok = course_pipeline._ensure_logged_in(page, course_id=None, sso_timeout_sec=600)
        self.assertFalse(ok)

    def test_uses_course_specific_url_when_course_id_given(self) -> None:
        """When called from the bulk download pipeline with a specific course,
        we should navigate to /courses/<id> rather than /courses."""
        page = _make_page(url="https://cool.ntu.edu.tw/courses/60804")
        course_pipeline._ensure_logged_in(page, course_id="60804", sso_timeout_sec=600)
        called_url = page.goto.call_args.args[0]
        self.assertIn("/courses/60804", called_url)

    def test_uses_commit_wait_until_not_domcontentloaded(self) -> None:
        """Explicit regression guard against reverting to domcontentloaded.
        The whole point of this fix is that we DON'T wait for DOM ready,
        because SAML's redirect chain can take longer than any reasonable
        timeout to settle into a stable DOM."""
        page = _make_page(url="https://cool.ntu.edu.tw/courses")
        course_pipeline._ensure_logged_in(page, course_id=None, sso_timeout_sec=600)
        self.assertEqual(page.goto.call_args.kwargs.get("wait_until"), "commit")


class CourseFileHandlingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._stdout_ctx = contextlib.redirect_stdout(io.StringIO())
        self._stdout_ctx.__enter__()
        self.addCleanup(self._stdout_ctx.__exit__, None, None, None)

    def test_non_pdf_file_keeps_pdf_extension_by_default(self) -> None:
        """Default mode forces every download to land as .pdf on disk so the
        output folder looks uniform for downstream AI tools. --all-file-types
        opts into the real extension (.pptx in this case)."""
        item = {
            "id": 1,
            "type": "File",
            "title": "week1 slides.pptx",
            "content_details": {"display_name": "week1 slides.pptx"},
        }

        self.assertEqual(
            course_pipeline._file_item_target_name(item, all_file_types=False),
            "week1 slides.pdf",
        )
        self.assertEqual(
            course_pipeline._file_item_target_name(item, all_file_types=True),
            "week1 slides.pptx",
        )

    def test_pdf_file_unchanged_in_both_modes(self) -> None:
        """A real PDF should produce the same filename regardless of mode —
        guards against the .pdf.pdf double-extension regression."""
        item = {
            "id": 2,
            "type": "File",
            "title": "syllabus.pdf",
            "content_details": {"display_name": "syllabus.pdf"},
        }
        self.assertEqual(
            course_pipeline._file_item_target_name(item, all_file_types=False),
            "syllabus.pdf",
        )
        self.assertEqual(
            course_pipeline._file_item_target_name(item, all_file_types=True),
            "syllabus.pdf",
        )

    def test_youtube_command_omits_missing_cookie_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = course_pipeline.CoursePlan(
                course={"id": "1", "name": "Course"},
                course_id="1",
                course_dir=root,
                weeks=[
                    course_pipeline.WeekPlan(
                        label="week1",
                        module={
                            "items": [
                                {
                                    "id": 10,
                                    "type": "ExternalUrl",
                                    "title": "1-1 video",
                                    "external_url": "https://www.youtube.com/watch?v=BT4w_3QsrQ8",
                                }
                            ]
                        },
                        week_dir=root / "week1",
                    )
                ],
            )

            run_result = mock.Mock(returncode=0)
            with (
                mock.patch.object(course_pipeline.shutil, "which", return_value="tool"),
                mock.patch.object(course_pipeline.subprocess, "run", return_value=run_result) as run,
                mock.patch.object(course_pipeline, "rename_downloaded_videos", return_value=[]),
            ):
                course_pipeline.download_youtube(
                    plan,
                    cookies_path=root / "missing-youtube-cookies.txt",
                    yt_dlp="yt-dlp",
                )

            cmd = run.call_args.args[0]
            self.assertNotIn("--cookies", cmd)

    def test_youtube_command_uses_cookie_file_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cookies = root / "youtube-cookies.txt"
            cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            plan = course_pipeline.CoursePlan(
                course={"id": "1", "name": "Course"},
                course_id="1",
                course_dir=root,
                weeks=[
                    course_pipeline.WeekPlan(
                        label="week1",
                        module={
                            "items": [
                                {
                                    "id": 10,
                                    "type": "ExternalUrl",
                                    "title": "1-1 video",
                                    "external_url": "https://www.youtube.com/watch?v=BT4w_3QsrQ8",
                                }
                            ]
                        },
                        week_dir=root / "week1",
                    )
                ],
            )

            run_result = mock.Mock(returncode=0)
            with (
                mock.patch.object(course_pipeline.shutil, "which", return_value="tool"),
                mock.patch.object(course_pipeline.subprocess, "run", return_value=run_result) as run,
                mock.patch.object(course_pipeline, "rename_downloaded_videos", return_value=[]),
            ):
                course_pipeline.download_youtube(plan, cookies_path=cookies, yt_dlp="yt-dlp")

            cmd = run.call_args.args[0]
            self.assertIn("--cookies", cmd)
            self.assertEqual(cmd[cmd.index("--cookies") + 1], str(cookies))

    def test_course_overview_uses_real_extension_for_all_file_types(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            week_dir = root / "week1"
            week_dir.mkdir()
            (week_dir / "week1 slides.pptx").write_bytes(b"pptx")
            plan = course_pipeline.CoursePlan(
                course={"id": "1", "name": "Course"},
                course_id="1",
                course_dir=root,
                weeks=[
                    course_pipeline.WeekPlan(
                        label="week1",
                        module={
                            "name": "Week 1",
                            "items": [
                                {
                                    "id": 1,
                                    "type": "File",
                                    "title": "week1 slides.pptx",
                                    "content_details": {"display_name": "week1 slides.pptx"},
                                }
                            ],
                        },
                        week_dir=week_dir,
                    )
                ],
            )

            overview = course_pipeline._write_course_overview(plan, all_file_types=True)
            text = overview.read_text(encoding="utf-8")
            self.assertIn("week1%20slides.pptx", text)
            self.assertNotIn("week1%20slides.pdf", text)


class MaybeRetryYoutubeWithLoginTests(unittest.TestCase):
    """Tests for the post-failure YouTube cookie capture prompt.

    Strategy reminder: yt-dlp runs first without cookies. If any downloads
    failed AND we don't have cookies yet AND we're interactive AND in a TTY,
    THEN we prompt. Most users (public-video courses) never see this.
    """

    def setUp(self) -> None:
        self._stdout_ctx = contextlib.redirect_stdout(io.StringIO())
        self._stdout_ctx.__enter__()
        self.addCleanup(self._stdout_ctx.__exit__, None, None, None)

    def test_skips_when_cookies_already_exist(self) -> None:
        """If we already have cookies, the failures aren't auth-related —
        retrying with the same cookies wouldn't fix anything."""
        with tempfile.TemporaryDirectory() as temp:
            cookies = Path(temp) / "yt.txt"
            cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            calls = []
            result = course_pipeline.maybe_retry_youtube_with_login(
                cookies, failed_count=3,
                input_fn=lambda p: calls.append(p) or "y",
            )
        self.assertFalse(result)
        self.assertEqual(calls, [], "must not prompt when cookies already exist")

    def test_skips_when_no_failures(self) -> None:
        """Everything downloaded fine — nothing to retry, nothing to ask."""
        with tempfile.TemporaryDirectory() as temp:
            cookies = Path(temp) / "yt.txt"
            calls = []
            result = course_pipeline.maybe_retry_youtube_with_login(
                cookies, failed_count=0,
                input_fn=lambda p: calls.append(p) or "y",
            )
        self.assertFalse(result)
        self.assertEqual(calls, [])

    def test_skips_when_interactive_false(self) -> None:
        """Batch / CI callers opt out of all prompts via interactive=False."""
        with tempfile.TemporaryDirectory() as temp:
            cookies = Path(temp) / "yt.txt"
            calls = []
            result = course_pipeline.maybe_retry_youtube_with_login(
                cookies, failed_count=5,
                interactive=False,
                input_fn=lambda p: calls.append(p) or "y",
            )
        self.assertFalse(result)
        self.assertEqual(calls, [])

    def test_user_declines_returns_false(self) -> None:
        """User presses Enter / N: skip the retry, leave failures as-is."""
        with tempfile.TemporaryDirectory() as temp:
            cookies = Path(temp) / "yt.txt"
            result = course_pipeline.maybe_retry_youtube_with_login(
                cookies, failed_count=3, input_fn=lambda p: "n",
            )
        self.assertFalse(result)
        self.assertFalse(cookies.exists())

    def test_user_accepts_runs_export(self) -> None:
        """User says 'y': delegates to youtube_cookies.export_youtube_cookies
        with the right cookies_path. Mock the export so we don't spawn a
        real browser — we're verifying the wiring, not the browser flow."""
        with tempfile.TemporaryDirectory() as temp:
            cookies = Path(temp) / "yt.txt"

            captured_kwargs = {}

            def fake_export(**kwargs):
                captured_kwargs.update(kwargs)
                kwargs["cookies_path"].write_text("# fake cookies\n", encoding="utf-8")
                return mock.Mock(cookies_path=kwargs["cookies_path"], cookie_count=42, current_url="x")

            with mock.patch("ntu_cool_materials.youtube_cookies.export_youtube_cookies",
                            side_effect=fake_export):
                result = course_pipeline.maybe_retry_youtube_with_login(
                    cookies, failed_count=3, input_fn=lambda p: "y",
                )
        self.assertTrue(result)
        self.assertEqual(captured_kwargs.get("cookies_path"), cookies)
        self.assertTrue(captured_kwargs.get("wait_for_login"))

    def test_eof_during_prompt_returns_false(self) -> None:
        """User Ctrl-D'd through the prompt mid-typing. Don't crash."""
        def raising_input(_prompt):
            raise EOFError
        with tempfile.TemporaryDirectory() as temp:
            cookies = Path(temp) / "yt.txt"
            result = course_pipeline.maybe_retry_youtube_with_login(
                cookies, failed_count=3, input_fn=raising_input,
            )
        self.assertFalse(result)

    def test_export_failure_swallowed_returns_false(self) -> None:
        """Browser flow blows up: warn, return False, let the outer pipeline
        continue with the original (without-cookies) failure list intact."""
        with tempfile.TemporaryDirectory() as temp:
            cookies = Path(temp) / "yt.txt"
            with mock.patch("ntu_cool_materials.youtube_cookies.export_youtube_cookies",
                            side_effect=RuntimeError("playwright went boom")):
                result = course_pipeline.maybe_retry_youtube_with_login(
                    cookies, failed_count=3, input_fn=lambda p: "y",
                )
        self.assertFalse(result)
        self.assertFalse(cookies.exists())

    def test_prompt_quotes_concrete_failure_count(self) -> None:
        """The whole point of post-failure prompting is to make the question
        concrete. Sanity-check the prompt actually includes the number."""
        with tempfile.TemporaryDirectory() as temp:
            cookies = Path(temp) / "yt.txt"
            captured = []
            course_pipeline.maybe_retry_youtube_with_login(
                cookies, failed_count=7,
                input_fn=lambda p: captured.append(p) or "n",
            )
        self.assertTrue(any("7" in c for c in captured),
                        f"failure count not in prompt; got prompts: {captured}")


class CountYoutubeUrlsInPlanTests(unittest.TestCase):
    def test_counts_distinct_video_ids(self) -> None:
        """Same video appearing twice should count once.

        Note: YouTube IDs are exactly 11 chars of [A-Za-z0-9_-] — short fake
        IDs like 'abc' won't match extract_youtube_ids' regex. Use full-length
        fixtures so the test exercises the real code path."""
        VID_A = "dQw4w9WgXcQ"  # 11 chars
        VID_B = "oHg5SJYRHA0"  # 11 chars, distinct
        plan = course_pipeline.CoursePlan(
            course={"id": "1", "name": "Course"}, course_id="1", course_dir=Path("."),
            weeks=[
                course_pipeline.WeekPlan(
                    label="w1",
                    module={"items": [
                        {"id": 1, "type": "ExternalUrl", "title": "v1", "external_url": f"https://youtu.be/{VID_A}"},
                        # Same video via different URL form — should dedupe.
                        {"id": 2, "type": "ExternalUrl", "title": "v2", "external_url": f"https://www.youtube.com/watch?v={VID_A}"},
                        {"id": 3, "type": "ExternalUrl", "title": "v3", "external_url": f"https://youtu.be/{VID_B}"},
                    ]},
                    week_dir=Path("./w1"),
                )
            ],
        )
        self.assertEqual(course_pipeline._count_youtube_urls_in_plan(plan), 2)

    def test_ignores_non_youtube_external_urls(self) -> None:
        plan = course_pipeline.CoursePlan(
            course={"id": "1", "name": "Course"}, course_id="1", course_dir=Path("."),
            weeks=[
                course_pipeline.WeekPlan(
                    label="w1",
                    module={"items": [
                        {"id": 1, "type": "ExternalUrl", "title": "doc", "external_url": "https://drive.google.com/file/d/xyz"},
                        {"id": 2, "type": "File", "title": "slides.pdf"},
                    ]},
                    week_dir=Path("./w1"),
                )
            ],
        )
        self.assertEqual(course_pipeline._count_youtube_urls_in_plan(plan), 0)


if __name__ == "__main__":
    unittest.main()
