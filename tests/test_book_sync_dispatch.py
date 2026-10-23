import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import json

from scripts.dispatch_book_sync import BOOK_PATH, DISPATCH_URL, book_changed, dispatch, main, NoRedirect


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "--quiet")
        self.write("other.md", "initial")
        self.before = self.commit()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, text=True, stderr=subprocess.DEVNULL).strip()

    def write(self, name, text):
        file = self.root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")

    def commit(self):
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "-m", "test")
        return self.git("rev-parse", "HEAD")

    def test_unrelated_and_adjacent_paths_do_not_dispatch(self):
        self.write("other.md", "changed")
        self.write("Сайт проекта HOS/Книга/Части-другие/chapter.md", "outside")
        self.assertFalse(book_changed(self.before, self.commit(), self.root))

    def test_add_modify_delete_and_rename_inside_book(self):
        name = BOOK_PATH + "Часть 1/глава.md"
        self.write(name, "draft text")
        added = self.commit()
        self.assertTrue(book_changed(self.before, added, self.root))
        self.write(name, "new text")
        modified = self.commit()
        self.assertTrue(book_changed(added, modified, self.root))
        (self.root / name).rename(self.root / (BOOK_PATH + "Часть 1/renamed.md"))
        renamed = self.commit()
        self.assertTrue(book_changed(modified, renamed, self.root))
        (self.root / (BOOK_PATH + "Часть 1/renamed.md")).unlink()
        self.assertTrue(book_changed(renamed, self.commit(), self.root))

    def test_full_push_diff_includes_earlier_commit(self):
        self.write(BOOK_PATH + "chapter.md", "text")
        self.commit()
        self.write("other.md", "unrelated last commit")
        self.assertTrue(book_changed(self.before, self.commit(), self.root))

    def test_reverted_book_content_has_no_net_change(self):
        name = BOOK_PATH + "chapter.md"
        self.write(name, "text")
        before = self.commit()
        self.write(name, "temporary")
        self.commit()
        self.write(name, "text")
        self.assertFalse(book_changed(before, self.commit(), self.root))

    def test_first_push_compares_with_empty_tree(self):
        self.assertFalse(book_changed("0" * 40, self.before, self.root))
        self.write(BOOK_PATH + "chapter.md", "text")
        self.assertTrue(book_changed("0" * 40, self.commit(), self.root))

    def test_invalid_or_unavailable_sha_fails_closed(self):
        for before, after in [("--help", self.before), (self.before, "0" * 40), ("f" * 40, self.before)]:
            with self.assertRaises((RuntimeError, ValueError)):
                book_changed(before, after, self.root)

    def test_more_than_path_filter_limits_does_not_hide_book(self):
        for i in range(3100):
            self.write(f"unrelated/{i}.md", "other")
        self.write(BOOK_PATH + "chapter.md", "book")
        self.assertTrue(book_changed(self.before, self.commit(), self.root))


class DispatchTests(unittest.TestCase):
    def test_fixed_endpoint_ref_and_headers_for_both_success_responses(self):
        for status in (200, 204):
            def opener(request, timeout):
                self.assertEqual(request.full_url, DISPATCH_URL)
                self.assertEqual(request.method, "POST")
                self.assertEqual(json.loads(request.data), {"ref": "main"})
                self.assertEqual(request.get_header("Authorization"), "Bearer test-only-token")
                self.assertEqual(timeout, 30)
                response = io.BytesIO(b"")
                response.status = status
                return response
            dispatch("test-only-token", opener)

    def test_missing_token_fails_before_network(self):
        for token in (None, "", "test-only-token\r\ninvalid"):
            with self.assertRaises(ValueError) as error:
                dispatch(token)
            self.assertNotIn("test-only-token", str(error.exception))

    def test_http_errors_are_sanitized_and_not_retried(self):
        for status in (401, 403, 404, 422, 429, 500):
            calls = []
            def opener(request, timeout):
                calls.append(request)
                raise urllib.error.HTTPError(DISPATCH_URL, status, "test-only-token", {}, io.BytesIO(b"test-only-token"))
            with self.assertRaises(RuntimeError) as error:
                dispatch("test-only-token", opener)
            self.assertIn(str(status), str(error.exception))
            self.assertNotIn("test-only-token", str(error.exception))
            self.assertEqual(len(calls), 1)

    def test_network_errors_warn_about_uncertain_delivery(self):
        def opener(request, timeout):
            raise urllib.error.URLError("test-only-token")
        with self.assertRaisesRegex(RuntimeError, "delivery may be uncertain") as error:
            dispatch("test-only-token", opener)
        self.assertNotIn("test-only-token", str(error.exception))

    def test_redirects_are_not_followed_with_authorization(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.invalid"))

    def test_detect_command_writes_false_and_never_dispatches(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output), "PUSH_BEFORE": "a" * 40, "PUSH_AFTER": "b" * 40}), patch("sys.argv", ["script", "detect"]), patch("scripts.dispatch_book_sync.book_changed", return_value=False), patch("scripts.dispatch_book_sync.dispatch") as send, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
                self.assertEqual(output.read_text(), "changed=false\n")
                send.assert_not_called()

    def test_detection_failure_does_not_emit_changed_or_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), patch("sys.argv", ["script", "detect"]), patch("scripts.dispatch_book_sync.book_changed", side_effect=RuntimeError("Unavailable push commit")), patch("scripts.dispatch_book_sync.dispatch") as send, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(), 1)
                self.assertFalse(output.exists())
                send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
