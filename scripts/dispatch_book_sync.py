"""Detect book-tree changes and dispatch the fixed hos-web workflow. Stdlib only."""

import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import json

BOOK_PATH = "Сайт проекта HOS/Книга/Части/"
DISPATCH_URL = "https://api.github.com/repos/Andreevvs/hos-web/actions/workflows/sync-book.yml/dispatches"


def git(*args, cwd=None, input=None):
    result = subprocess.run(
        ["git", *args], cwd=cwd, input=input, text=True, capture_output=True
    )
    if result.returncode:
        raise RuntimeError("Git operation failed; verify checkout history and push commit availability")
    return result.stdout.strip()


def book_changed(before, after, cwd=None):
    for sha in (before, after):
        if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
            raise ValueError("Push before/after must be full Git commit SHAs")
    if after == "0" * 40:
        raise ValueError("Branch deletion cannot dispatch book synchronization")
    git("cat-file", "-e", f"{after}^{{commit}}", cwd=cwd)
    if before == "0" * 40:
        before = git("hash-object", "-w", "-t", "tree", "--stdin", cwd=cwd, input="")
    else:
        git("cat-file", "-e", f"{before}^{{commit}}", cwd=cwd)
    result = subprocess.run(
        ["git", "diff", "--quiet", "--no-ext-diff", "--no-renames", before, after, "--", BOOK_PATH],
        cwd=cwd, capture_output=True,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError("Cannot compare the complete book directory; dispatch cancelled")
    return result.returncode == 1


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def dispatch(token, opener=None):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", token or ""):
        raise ValueError("Missing or invalid temporary HOS_WEB_DISPATCH_TOKEN")
    request = urllib.request.Request(
        DISPATCH_URL,
        data=json.dumps({"ref": "main"}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "HOS-book-sync-dispatcher",
        },
        method="POST",
    )
    if opener is None:
        opener = urllib.request.build_opener(NoRedirect()).open
    try:
        with opener(request, timeout=30) as response:
            if response.status not in (200, 204):
                raise RuntimeError(f"Unexpected dispatch HTTP status: {response.status}")
    except urllib.error.HTTPError as error:
        hints = {
            401: "check App credentials and token expiry",
            403: "check App Actions: write permission and repository Actions policy",
            404: "check App installation on hos-web and sync-book.yml on main",
            422: "check workflow_dispatch support and the main ref",
            429: "rate limit reached; inspect GitHub before retrying",
        }
        raise RuntimeError(f"Dispatch HTTP {error.code}: {hints.get(error.code, 'inspect GitHub Actions/API status')}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError("Dispatch network failure; delivery may be uncertain. Inspect hos-web Actions before a manual retry") from None


def main():
    try:
        command = sys.argv[1] if len(sys.argv) == 2 else ""
        if command == "detect":
            changed = book_changed(os.environ.get("PUSH_BEFORE"), os.environ.get("PUSH_AFTER"))
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write(f"changed={'true' if changed else 'false'}\n")
            print("Book directory changed: dispatch required" if changed else "Book directory unchanged: synchronization skipped; no App token requested")
        elif command == "dispatch":
            dispatch(os.environ.get("HOS_WEB_DISPATCH_TOKEN"))
            print("GitHub accepted sync-book.yml dispatch for Andreevvs/hos-web, ref main")
            print("Dispatch acceptance does not establish synchronization success. Inspect hos-web Actions and the resulting PR")
        else:
            raise ValueError("Usage: python3 scripts/dispatch_book_sync.py detect|dispatch")
    except (RuntimeError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
