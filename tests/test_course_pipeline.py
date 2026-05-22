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

import unittest
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


if __name__ == "__main__":
    unittest.main()
