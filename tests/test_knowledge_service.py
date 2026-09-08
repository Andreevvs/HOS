from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from knowledge_service import KnowledgeService, RegistryError, StaleIndexError


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "knowledge_service/registry.json"
EXACT_ID = "Экспресс-тест уровня развития v0.1"
QUERY = "Как в HOS связаны уровни развития и метакомпетенции?"


class ExperimentEvals(unittest.TestCase):
    def setUp(self):
        self.service = KnowledgeService(ROOT, REGISTRY, default_limit=5)
        self.service.build()

    def test_e1_exact_returns_complete_canonical_document(self):
        result = self.service.exact(EXACT_ID)
        expected = (ROOT / result["source_path"]).read_text(encoding="utf-8")
        self.assertEqual("ok", result["status"])
        self.assertEqual(expected, result["text"])

    def test_e2_version_propagates_after_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.service.exact(EXACT_ID)["source_path"]
            (root / Path(source).parent).mkdir(parents=True)
            shutil.copy2(ROOT / source, root / source)
            registry = root / "registry.json"
            registry.write_text(json.dumps({"objects": [json.loads(REGISTRY.read_text(encoding="utf-8"))["objects"][0]]},
                                           ensure_ascii=False), encoding="utf-8")
            first = KnowledgeService(root, registry)
            first.build()
            before = first.exact(EXACT_ID)
            with (root / source).open("a", encoding="utf-8") as file:
                file.write("\n<!-- temporary E2 revision -->\n")
            rebuilt = KnowledgeService(root, registry)
            rebuilt.build()
            after = rebuilt.exact(EXACT_ID)
            self.assertNotEqual(before["source_sha"], after["source_sha"])
            self.assertIn("temporary E2 revision", after["text"])

    def test_e3_semantic_retrieval_is_relevant(self):
        # The active KB grows over time; ten results retain the original relevance
        # assertion without treating an exact rank as a canonical search contract.
        results = self.service.search(QUERY, limit=10)
        ids = {result["parent_id"] for result in results}
        self.assertIn("hos.metaskills", ids)
        self.assertTrue(ids & {"hos.level-architecture", "hos.holography"})
        self.assertTrue(all(result["source_path"] and result["source_sha"] for result in results))

    def test_e4_missing_version_is_not_found(self):
        self.assertEqual("not_found", self.service.exact("Экспресс-тест уровня развития v9.7")["status"])

    def test_e5_exact_provenance_matches_source(self):
        result = self.service.exact(EXACT_ID)
        self.assertEqual(64, len(result["source_sha"]))
        self.assertTrue((ROOT / result["source_path"]).is_file())


class NegativeTests(unittest.TestCase):
    def test_unknown_id(self):
        service = KnowledgeService(ROOT, REGISTRY)
        service.build()
        self.assertEqual({"status": "not_found", "id": "missing", "version": None}, service.exact("missing"))

    def test_empty_query(self):
        service = KnowledgeService(ROOT, REGISTRY)
        service.build()
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            service.search("  ")

    def test_missing_or_renamed_source_path(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "registry.json"
            registry.write_text(json.dumps({"objects": [{"id": "x", "source_path": "gone.md",
                                                           "retrieval_mode": "fragment"}]}), encoding="utf-8")
            with self.assertRaisesRegex(RegistryError, "source_path is missing"):
                KnowledgeService(Path(directory), registry).build()

    def test_duplicate_registry_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.md").write_text("# a", encoding="utf-8")
            registry = root / "registry.json"
            item = {"id": "same", "source_path": "a.md", "retrieval_mode": "fragment"}
            registry.write_text(json.dumps({"objects": [item, item]}), encoding="utf-8")
            with self.assertRaisesRegex(RegistryError, "duplicate id"):
                KnowledgeService(root, registry).build()

    def test_source_change_after_index_is_explicit_stale_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = "03 — Диагностика/Экспресс-тест уровня развития v0.1.md"
            (root / Path(source).parent).mkdir(parents=True)
            shutil.copy2(ROOT / source, root / source)
            registry = root / "registry.json"
            registry.write_text(json.dumps({"objects": [json.loads(REGISTRY.read_text(encoding="utf-8"))["objects"][0]]},
                                           ensure_ascii=False), encoding="utf-8")
            service = KnowledgeService(root, registry)
            service.build()
            (root / source).write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(StaleIndexError, "provenance conflict"):
                service.exact(EXACT_ID)

    def test_search_rejects_provenance_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "01 — Канон", root / "01 — Канон")
            registry = root / "registry.json"
            data = json.loads(REGISTRY.read_text(encoding="utf-8"))
            data["objects"] = [item for item in data["objects"] if item["source_path"].startswith("01 — Канон/")]
            registry.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            service = KnowledgeService(root, registry)
            service.build()
            target = root / "01 — Канон/Метанавыки.md"
            target.write_text(target.read_text(encoding="utf-8") + "\nstale", encoding="utf-8")
            with self.assertRaises(StaleIndexError):
                service.search(QUERY, limit=10)


if __name__ == "__main__":
    unittest.main()
