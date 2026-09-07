"""Replaceable, standard-library-only retrieval components for the v0.1 experiment."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class KnowledgeServiceError(Exception):
    """Base class for explicit retrieval failures."""


class RegistryError(KnowledgeServiceError):
    """The experiment registry does not satisfy its technical contract."""


class StaleIndexError(KnowledgeServiceError):
    """Canonical source and indexed provenance disagree."""


def source_sha(raw: bytes) -> str:
    """Return a portable SHA-256 revision identifier for exact source bytes."""
    return hashlib.sha256(raw).hexdigest()


def _terms(text: str) -> list[str]:
    words = re.findall(r"[\wА-Яа-яЁё]+", text.casefold())
    # Light technical normalization, not an HOS ontology or semantic annotation.
    endings = ("иями", "ями", "ами", "ого", "ему", "ыми", "ами", "ах", "ях", "ов", "ев", "ы", "и", "а", "я")
    result = []
    for word in words:
        for ending in endings:
            if len(word) > len(ending) + 4 and word.endswith(ending):
                word = word[: -len(ending)]
                break
        result.append(word)
        # Character features tolerate inflection and nearby terminology without a
        # domain synonym list. They are an index implementation detail.
        result.extend("§" + word[index:index + 4] for index in range(len(word) - 3))
    return result


@dataclass(frozen=True)
class RegistryEntry:
    id: str
    source_path: str
    retrieval_mode: str


@dataclass(frozen=True)
class SourceDocument:
    entry: RegistryEntry
    title: str
    status: str
    sha: str
    text: str


@dataclass(frozen=True)
class Fragment:
    parent_id: str
    fragment_id: str
    source_path: str
    source_sha: str
    location: str
    text: str


class SourceLoader(Protocol):
    def load(self, entry: RegistryEntry) -> SourceDocument: ...


class Chunker(Protocol):
    def chunks(self, document: SourceDocument) -> list[Fragment]: ...


class SemanticIndex(Protocol):
    def build(self, fragments: list[Fragment]) -> None: ...
    def search(self, query: str, limit: int) -> list[Fragment]: ...


class FileSourceLoader:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def load(self, entry: RegistryEntry) -> SourceDocument:
        path = (self.root / entry.source_path).resolve()
        if self.root not in path.parents or not path.is_file():
            raise RegistryError(f"source_path is missing or invalid: {entry.source_path}")
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        status_match = re.search(r"^\*\*Статус:\*\*\s*(.+)$", text, re.MULTILINE)
        return SourceDocument(entry, title_match.group(1).strip() if title_match else path.stem,
                              status_match.group(1).strip() if status_match else "не указан",
                              source_sha(raw), text)


class MarkdownChunker:
    """Split on Markdown headings; whole-document objects remain indivisible."""

    def chunks(self, document: SourceDocument) -> list[Fragment]:
        if document.entry.retrieval_mode == "whole_document":
            sections = [("whole_document", document.text)]
        else:
            starts = list(re.finditer(r"^#{1,6}\s+.+$", document.text, re.MULTILINE))
            if not starts:
                sections = [("document", document.text)]
            else:
                sections = []
                if starts[0].start():
                    sections.append(("preamble", document.text[: starts[0].start()]))
                for number, match in enumerate(starts):
                    end = starts[number + 1].start() if number + 1 < len(starts) else len(document.text)
                    sections.append((match.group(0).lstrip("# ").strip(), document.text[match.start():end]))
        return [Fragment(document.entry.id, f"{document.entry.id}#{n}", document.entry.source_path,
                         document.sha, location, text.strip())
                for n, (location, text) in enumerate(sections, 1) if text.strip()]


class TfIdfIndex:
    """Small replaceable lexical TF-IDF index suitable for the experiment corpus."""

    def build(self, fragments: list[Fragment]) -> None:
        self.fragments = fragments
        self.vectors = [Counter(_terms(f.text + " " + f.location)) for f in fragments]
        document_frequency = Counter(term for vector in self.vectors for term in vector)
        self.idf = {term: math.log((len(fragments) + 1) / (frequency + 1)) + 1
                    for term, frequency in document_frequency.items()}

    def search(self, query: str, limit: int) -> list[Fragment]:
        query_vector = Counter(_terms(query))
        scored = []
        for number, vector in enumerate(self.vectors):
            score = sum(query_vector[t] * vector[t] * self.idf.get(t, 0) ** 2 for t in query_vector)
            if score:
                scored.append((score, -number, self.fragments[number]))
        scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
        return [item[2] for item in scored[:limit]]


def load_registry(path: Path) -> list[RegistryEntry]:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries, ids = [], set()
    for item in data.get("objects", []):
        entry = RegistryEntry(item["id"], item["source_path"], item["retrieval_mode"])
        if entry.id in ids:
            raise RegistryError(f"duplicate id: {entry.id}")
        if entry.retrieval_mode not in {"whole_document", "fragment"}:
            raise RegistryError(f"invalid retrieval_mode for {entry.id}")
        ids.add(entry.id)
        entries.append(entry)
    return entries


def discover_active_sources(root: Path, registered: list[RegistryEntry]) -> list[RegistryEntry]:
    """Discover active Markdown while keeping experiment-generated IDs visibly technical."""
    included_roots = ("01 — Канон", "02 — Линии", "03 — Диагностика", "04 — Гипотезы",
                      "05 — Эмпирика", "06 — Источники", "07 — Техники")
    known_paths = {entry.source_path for entry in registered}
    discovered = []
    for path in sorted(root.rglob("*.md")):
        relative = path.relative_to(root).as_posix()
        active = relative == "00 — Индекс HOS.md" or any(
            relative.startswith(prefix + "/") for prefix in included_roots
        )
        if relative in known_paths or not active:
            continue
        technical_id = "experiment.path." + hashlib.sha256(relative.encode()).hexdigest()[:20]
        discovered.append(RegistryEntry(technical_id, relative, "fragment"))
    return discovered


class KnowledgeService:
    def __init__(self, root: Path, registry_path: Path, *, default_limit: int = 5,
                 loader: SourceLoader | None = None, chunker: Chunker | None = None,
                 semantic_index: SemanticIndex | None = None):
        self.root = root.resolve()
        self.registry_path = registry_path
        self.default_limit = default_limit
        self.loader = loader or FileSourceLoader(self.root)
        self.chunker = chunker or MarkdownChunker()
        self.semantic_index = semantic_index or TfIdfIndex()
        self.documents: dict[str, SourceDocument] = {}

    def build(self) -> None:
        entries = load_registry(self.registry_path)
        entries += discover_active_sources(self.root, entries)
        documents = [self.loader.load(entry) for entry in entries]
        self.documents = {document.entry.id: document for document in documents}
        self.semantic_index.build([fragment for document in documents for fragment in self.chunker.chunks(document)])

    def _validate(self, document: SourceDocument) -> None:
        current = self.loader.load(document.entry)
        if current.sha != document.sha:
            raise StaleIndexError(f"stale index/provenance conflict: {document.entry.source_path}")

    def exact(self, object_id: str, version: str | None = None) -> dict:
        document = self.documents.get(object_id)
        if document is None or (version is not None and version != document.sha):
            return {"status": "not_found", "id": object_id, "version": version}
        self._validate(document)
        return {"status": "ok", "id": object_id, "source_path": document.entry.source_path,
                "source_sha": document.sha, "text": document.text}

    def search(self, query: str, limit: int | None = None) -> list[dict]:
        if not query or not query.strip():
            raise ValueError("semantic query must not be empty")
        results = self.semantic_index.search(query, self.default_limit if limit is None else limit)
        for fragment in results:
            self._validate(self.documents[fragment.parent_id])
        return [{"parent_id": f.parent_id, "source_path": f.source_path, "source_sha": f.source_sha,
                 "location": f.location, "text": f.text} for f in results]
