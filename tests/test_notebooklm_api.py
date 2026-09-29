import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ntu_cool_materials import cli
from ntu_cool_materials.notebooklm import NotebookLMError, STATE_NAME, build_import_plan, import_plan
from ntu_cool_materials.notebooklm_api import (
    NotebookLMAPIAdapter, run_api_import, resolve_api_storage, remember_api_storage,
    trigger_interactive_login, api_client_context
)

URL = "https://notebooklm.google.com/notebook/owned"
STATUS = SimpleNamespace(READY=2, PREPARING=5, PROCESSING=1, ERROR=3)


class FakeAPI:
    def __init__(self):
        self.rows = []
        self.uploads = 0
        self.created = 0
        self.fail_wait = False
        self.fail_upload = False
        self.renames = 0
        self.notebooks = SimpleNamespace(list=self.list_notebooks, create=self.create)
        self.sources = SimpleNamespace(list=self.list_sources, add_file=self.add_file,
                                       wait_until_ready=self.wait, rename=self.rename_source)

    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass

    async def rename_source(self, notebook_id, source_id, new_title):
        self.renames += 1
        row = next(r for r in self.rows if r.id == source_id)
        row.title = new_title
        return row

    async def list_notebooks(self):
        return [SimpleNamespace(id="owned", title="My course", is_owner=True),
                SimpleNamespace(id="featured", title="Recommendation", is_owner=False)]

    async def create(self, title):
        self.created += 1
        return SimpleNamespace(id="new", title=title, is_owner=True)

    async def list_sources(self, notebook_id, *, strict):
        assert strict
        return list(self.rows)

    async def add_file(self, notebook_id, path, *, title):
        self.uploads += 1
        assert path.read_bytes()
        if self.fail_upload:
            raise RuntimeError("secret-cookie-value")
        row = SimpleNamespace(id=f"source-{self.uploads}", title=title, status=1)
        self.rows.append(row)
        return row

    async def wait(self, notebook_id, source_id, *, timeout):
        if self.fail_wait:
            raise RuntimeError("secret-cookie-value")
        row = next(r for r in self.rows if r.id == source_id)
        row.status = 2
        return row


class StorageTests(unittest.TestCase):
    def test_remembered_login_works_from_another_directory(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder)
            login = root / "project/storage_state.json"
            login.parent.mkdir()
            login.write_text("synthetic-secret")
            with patch("ntu_cool_materials.notebooklm_api.Path.home", return_value=root), \
                 patch("ntu_cool_materials.notebooklm_api.Path.cwd", return_value=root / "elsewhere"):
                remember_api_storage(login)
                self.assertEqual(resolve_api_storage(), login)
                config = root / ".ntu-cool-gcm/notebooklm-storage.json"
                self.assertNotIn("synthetic-secret", config.read_text())
                with patch.dict(os.environ, {"NOTEBOOKLM_PROFILE": "other"}):
                    self.assertIsNone(resolve_api_storage())
                login.unlink()
                self.assertIsNone(resolve_api_storage())
                config.write_text("broken")
                self.assertIsNone(resolve_api_storage())

    def test_existing_launcher_login_and_explicit_overrides(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder)
            login = root / ".secrets/notebooklm-api/profiles/default/storage_state.json"
            login.parent.mkdir(parents=True)
            login.write_text("{}")
            with patch("ntu_cool_materials.notebooklm_api.Path.cwd", return_value=root):
                self.assertEqual(resolve_api_storage(), login)
                explicit = root / "explicit.json"
                self.assertEqual(resolve_api_storage(explicit), explicit)
                for name in ("NOTEBOOKLM_HOME", "NOTEBOOKLM_PROFILE", "NOTEBOOKLM_AUTH_JSON"):
                    with patch.dict(os.environ, {name: "configured"}):
                        self.assertIsNone(resolve_api_storage())
                        self.assertEqual(resolve_api_storage(explicit), explicit)

    def test_home_fallback_and_sdk_default(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder)
            with patch("ntu_cool_materials.notebooklm_api.Path.cwd", return_value=root / "cwd"), \
                 patch("ntu_cool_materials.notebooklm_api.Path.home", return_value=root):
                self.assertIsNone(resolve_api_storage())
                login = root / ".ntu-cool-gcm/.secrets/notebooklm-api/profiles/default/storage_state.json"
                login.parent.mkdir(parents=True)
                login.write_text("{}")
                self.assertEqual(resolve_api_storage(), login)


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "lecture.md").write_text("Course material", encoding="utf-8")
        self.plan = build_import_plan(self.root)
        self.api = FakeAPI()
        self.enterContext(patch.dict("sys.modules", {"notebooklm.types": SimpleNamespace(SourceStatus=STATUS)}))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def adapter(self, **kwargs):
        return NotebookLMAPIAdapter({s.title for s in self.plan.sources},
                                     context_factory=lambda path: self.api, **kwargs)

    def run_import(self):
        with self.adapter() as adapter:
            return import_plan(self.plan, adapter, notebook_url=URL)

    def test_upload_verified_then_rerun_deduplicates(self):
        self.assertEqual(self.run_import().uploaded, 1)
        self.assertEqual(self.run_import().unchanged, 1)
        self.assertEqual(self.api.uploads, 1)
        self.assertIn(URL, json.loads((self.root / STATE_NAME).read_text())["notebooks"])

    def test_timeout_keeps_pending_then_reconciles_without_upload(self):
        self.api.fail_wait = True
        with self.assertRaisesRegex(NotebookLMError, "pending"):
            self.run_import()
        self.api.fail_wait = False
        self.assertEqual(self.run_import().unchanged, 1)
        self.assertEqual(self.api.uploads, 1)

    def test_unknown_upload_is_not_replayed_and_secret_not_displayed(self):
        self.api.fail_upload = True
        with self.assertRaises(NotebookLMError) as failure:
            self.run_import()
        self.assertNotIn("secret", str(failure.exception))
        with self.assertRaisesRegex(NotebookLMError, "先前上傳結果不明"):
            self.run_import()
        self.assertEqual(self.api.uploads, 1)

    def test_unowned_notebook_refused_before_upload(self):
        with self.adapter() as adapter:
            with self.assertRaisesRegex(NotebookLMError, "我的筆記本"):
                import_plan(self.plan, adapter, notebook_url=URL.replace("owned", "featured"))
        self.assertEqual(self.api.uploads, 0)

    def test_same_title_error_source_refuses_duplicate(self):
        self.api.rows = [SimpleNamespace(id="failed", title=self.plan.sources[0].title, status=3)]
        with self.assertRaisesRegex(NotebookLMError, "狀態錯誤"):
            self.run_import()
        self.assertEqual(self.api.uploads, 0)

    def test_capacity_counts_all_api_sources(self):
        self.api.rows = [SimpleNamespace(id=str(i), title=str(i), status=2) for i in range(50)]
        with self.assertRaisesRegex(NotebookLMError, "來源數"):
            self.run_import()
        self.assertEqual(self.api.uploads, 0)

    def test_user_cancellation_never_creates_or_uploads(self):
        with self.adapter(interactive=True) as adapter, patch("builtins.input", return_value="q"):
            with self.assertRaisesRegex(NotebookLMError, "取消"):
                import_plan(self.plan, adapter)
        self.assertEqual(self.api.created, 0)
        self.assertEqual(self.api.uploads, 0)

    def test_dry_run_never_initializes_api(self):
        with patch("ntu_cool_materials.notebooklm_api.api_client_context") as factory:
            run_api_import(self.root, dry_run=True)
        factory.assert_not_called()
        self.assertFalse((self.root / STATE_NAME).exists())

    def test_cli_defaults_to_api_and_passes_storage(self):
        with patch("ntu_cool_materials.notebooklm_api.run_api_import") as run:
            self.assertEqual(cli.main(["notebooklm", "--course-dir", str(self.root),
                                       "--notebooklm-storage", "auth.json"]), 0)
        self.assertEqual(run.call_args.kwargs["storage_path"], Path("auth.json"))

    def test_cli_legacy_browser_remains_explicit(self):
        with patch("ntu_cool_materials.notebooklm.run_import") as run:
            self.assertEqual(cli.main(["notebooklm", "--course-dir", str(self.root), "--browser"]), 0)
        run.assert_called_once()

    def test_course_id_selects_exact_folder_without_prompt(self):
        current = self.root / 'Current course (64660)'
        current.mkdir()
        (self.root / 'Old course (6466)').mkdir()
        with patch('builtins.input', side_effect=AssertionError('must not prompt')), \
             patch('ntu_cool_materials.notebooklm_api.run_api_import') as run:
            code = cli.main(['notebooklm', '--course-id', '64660', '--out', str(self.root)])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.args[0], current)

    def test_ambiguous_course_id_stops_without_prompt_or_upload(self):
        (self.root / 'One (64660)').mkdir()
        (self.root / 'Two (64660)').mkdir()
        with patch('builtins.input', side_effect=AssertionError('must not prompt')), \
             patch('ntu_cool_materials.notebooklm_api.run_api_import') as run:
            self.assertEqual(cli.main(['notebooklm', '--course-id', '64660', '--out', str(self.root)]), 1)
        run.assert_not_called()

    def test_cmd_launchers_forward_arguments_and_never_pause(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('Start-NotebookLM.cmd', 'Download-and-Import.cmd'):
            text = (root / name).read_text(encoding='utf-8')
            self.assertIn('%*', text)
            self.assertNotIn('\npause', text)

    def test_verify_only_missing_source_cannot_upload_or_write_journal(self):
        with patch('ntu_cool_materials.notebooklm_api.api_client_context', return_value=self.api):
            with self.assertRaisesRegex(NotebookLMError, '未重送'):
                run_api_import(self.root, notebook_url=URL, verify_only=True)
        self.assertEqual(self.api.uploads, 0)
        self.assertEqual(self.api.created, 0)
        self.assertFalse((self.root / STATE_NAME).exists())

    def test_verify_only_ready_sources_preserves_journal_bytes(self):
        self.run_import()
        before = (self.root / STATE_NAME).read_bytes()
        with patch('ntu_cool_materials.notebooklm_api.api_client_context', return_value=self.api):
            result = run_api_import(self.root, verify_only=True)
        self.assertEqual(result.unchanged, 1)
        self.assertEqual(self.api.uploads, 1)
        self.assertEqual((self.root / STATE_NAME).read_bytes(), before)

    def test_readonly_adapter_refuses_creation_and_upload(self):
        with self.adapter() as adapter:
            adapter.read_only = True
            with self.assertRaises(NotebookLMError):
                adapter.open_notebook(None, 'test')
            with self.assertRaises(NotebookLMError):
                adapter.upload(self.root / 'lecture.md', 'test')
        self.assertEqual(self.api.created, 0)
        self.assertEqual(self.api.uploads, 0)

    def test_interactive_auto_login_retries_on_auth_failure(self):
        calls = []
        def failing_then_succeeding_context(storage_path):
            calls.append("called")
            if len(calls) == 1:
                raise RuntimeError("login expired")
            return self.api

        with patch("sys.stdin.isatty", return_value=True), \
             patch("ntu_cool_materials.notebooklm_api.api_client_context", side_effect=failing_then_succeeding_context), \
             patch("ntu_cool_materials.notebooklm_api.trigger_interactive_login", return_value=True) as login_mock:
            adapter = NotebookLMAPIAdapter({s.title for s in self.plan.sources})
            with adapter:
                self.assertIsNotNone(adapter.client)
            login_mock.assert_called_once()
            self.assertEqual(len(calls), 2)

    def test_interactive_auto_login_failure_raises_notebooklm_error(self):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("ntu_cool_materials.notebooklm_api.api_client_context", side_effect=RuntimeError("no credentials")), \
             patch("ntu_cool_materials.notebooklm_api.trigger_interactive_login", return_value=False) as login_mock:
            adapter = NotebookLMAPIAdapter({s.title for s in self.plan.sources})
            with self.assertRaisesRegex(NotebookLMError, "API 登入未就緒或已過期"):
                with adapter:
                    pass
            login_mock.assert_called_once()

    def test_interactive_select_existing_notebook(self):
        with self.adapter(interactive=True) as adapter, patch("builtins.input", return_value="1"):
            result = import_plan(self.plan, adapter)
            self.assertEqual(result.notebook_url, "https://notebook.google.com/notebook/owned")
            self.assertEqual(self.api.created, 0)
            self.assertEqual(self.api.uploads, 1)


    def test_interactive_select_create_new_notebook(self):
        with self.adapter(interactive=True) as adapter, patch("builtins.input", return_value="0"):
            result = import_plan(self.plan, adapter)
            self.assertEqual(result.notebook_url, "https://notebook.google.com/notebook/new")
            self.assertEqual(self.api.created, 1)
            self.assertEqual(self.api.uploads, 1)

    def test_legacy_hashed_sources_are_automatically_renamed(self):
        legacy_title = "lecture [11b37d6cafc0].md"
        self.api.rows.append(SimpleNamespace(id="src-old", title=legacy_title, status=2))
        with self.adapter() as adapter:
            ready = adapter.ready_titles()
            self.assertIn("lecture.md", ready)
            self.assertEqual(self.api.renames, 1)
            self.assertEqual(self.api.rows[0].title, "lecture.md")

    def test_imported_sources_do_not_contain_hash_code(self):
        with self.adapter() as adapter:
            import_plan(self.plan, adapter)
            self.assertEqual(self.api.uploads, 1)
            self.assertEqual(self.api.rows[0].title, "lecture.md")
            self.assertNotIn("[", self.api.rows[0].title)

    def test_legacy_announcements_title_is_automatically_renamed(self):
        adapter = NotebookLMAPIAdapter({"announcements"}, context_factory=lambda path: self.api)
        self.api.rows.append(SimpleNamespace(id="src-ann", title="announcements - announcements.md", status=2))
        with adapter:
            ready = adapter.ready_titles()
            self.assertIn("announcements", ready)
            self.assertEqual(self.api.rows[-1].title, "announcements")




