#!/usr/bin/env python3
"""Build HOS_KB_Runtime.md from the canonical repository tree."""

from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "HOS_KB_Runtime.md"

INCLUDE_ROOT_FILE = "00 — Индекс HOS.md"
INCLUDE_DIRS = (
    "01 — Канон",
    "02 — Линии",
    "03 — Диагностика",
    "04 — Гипотезы",
    "05 — Эмпирика",
    "06 — Источники",
    "07 — Техники",
)
EXCLUDED_TOP_FILES = {
    "00 — Аудит миграции v0.2.md",
    "00 — Закрытие миграции legacy → KB v0.3.md",
}
EXCLUDED_PREFIXES = ("99 — Архив/", "07 — Экспорт/")
ASSET_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf"}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def included(path: Path) -> bool:
    p = rel(path)
    if p == OUTPUT.name or p in EXCLUDED_TOP_FILES:
        return False
    if any(p.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return False
    if p == INCLUDE_ROOT_FILE:
        return True
    return any(p.startswith(d + "/") for d in INCLUDE_DIRS)


def blob_sha(path: Path) -> str:
    # Git blob SHA, not a plain file hash.
    return git("hash-object", rel(path))


def main() -> None:
    files = sorted((p for p in ROOT.rglob("*") if p.is_file() and included(p)), key=rel)
    md_files = [p for p in files if p.suffix.lower() == ".md"]
    assets = [p for p in files if p.suffix.lower() in ASSET_EXTENSIONS]

    if not md_files:
        raise SystemExit("No Markdown source files found")

    commit_sha = os.getenv("GITHUB_SHA") or git("rev-parse", "HEAD")
    build_date = datetime.now(timezone.utc).date().isoformat()

    parts: list[str] = []
    parts += [
        "# HOS Runtime Knowledge Base\n",
        "Generated automatically from Canonical KB.\n",
        "Source branch: `main`\n",
        f"Source commit SHA: `{commit_sha}`\n",
        f"Build date: `{build_date}`\n",
        "Status: generated runtime representation; not an independent canonical version.\n",
        "Excluded: `99 — Архив/**`, `07 — Экспорт/**`, `00 — Аудит миграции v0.2.md`, `00 — Закрытие миграции legacy → KB v0.3.md`. Binary assets are listed but not embedded.\n",
        f"Included Markdown documents: **{len(md_files)}**. Non-text assets: **{len(assets)}**.\n",
        "## Source manifest\n",
    ]

    for p in md_files:
        parts.append(f"- `{rel(p)}` — {p.stat().st_size} bytes\n")

    parts.append("\n## Asset manifest\n")
    if assets:
        for p in assets:
            parts.append(f"- `{rel(p)}` — {p.stat().st_size} bytes — Git blob SHA `{blob_sha(p)}`\n")
    else:
        parts.append("- none\n")

    for p in md_files:
        raw = p.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SystemExit(f"Source is not UTF-8: {rel(p)}: {exc}")
        parts += [
            "\n---\n\n<!-- HOS_SOURCE_BEGIN -->\n",
            f"SOURCE_PATH: {rel(p)}\n\n",
            f"SOURCE_BLOB_SHA: {blob_sha(p)}\n\n",
            f"SOURCE_SIZE_BYTES: {len(raw)}\n\n---\n\n",
            text.rstrip() + "\n",
            "<!-- HOS_SOURCE_END -->\n",
        ]

    output = "".join(parts)
    OUTPUT.write_text(output, encoding="utf-8", newline="\n")

    # Structural validation.
    built = OUTPUT.read_text(encoding="utf-8")
    if built.count("<!-- HOS_SOURCE_BEGIN -->") != len(md_files):
        raise SystemExit("Validation failed: source section count mismatch")
    for p in md_files:
        marker = f"SOURCE_PATH: {rel(p)}"
        if built.count(marker) != 1:
            raise SystemExit(f"Validation failed: {marker!r} occurs {built.count(marker)} times")
    for forbidden in EXCLUDED_TOP_FILES:
        if f"SOURCE_PATH: {forbidden}" in built:
            raise SystemExit(f"Validation failed: excluded source embedded: {forbidden}")

    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    print(f"Built {OUTPUT.name}")
    print(f"Markdown documents: {len(md_files)}")
    print(f"Assets: {len(assets)}")
    print(f"Output bytes: {OUTPUT.stat().st_size}")
    print(f"SHA256: {digest}")


if __name__ == "__main__":
    main()
