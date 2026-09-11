from __future__ import annotations

import unittest
from pathlib import Path

from full_iut import Evidence, FullIUTInterview, Interpretation, InvalidInterpretation
from knowledge_service import KnowledgeService

ROOT = Path(__file__).resolve().parents[1]


class ScriptedInterpreter:
    def __init__(self, decisions): self.decisions = iter(decisions)
    def interpret(self, **kwargs): return next(self.decisions)


class FullIUTTests(unittest.TestCase):
    def knowledge(self):
        ks = KnowledgeService(ROOT, ROOT / "knowledge_service" / "registry.json")
        ks.build(); return ks

    def test_exact_protocol_and_session_are_pinned(self):
        interview = FullIUTInterview(self.knowledge(), ScriptedInterpreter([]))
        session, question = interview.start()
        self.assertEqual(session.source_path, "03 — Диагностика/ИУТ — измерение.md")
        self.assertTrue(session.source_sha)
        self.assertEqual(session.version, "v0.6")
        self.assertIn("последняя обычная неделя", question)
        self.assertEqual(session.canonical_text.encode(), (ROOT / session.source_path).read_bytes())

    def test_question_is_server_side_and_history_is_auditable(self):
        decision = Interpretation("Какие три вещи сейчас требуют больше всего сил и внимания? Почему?",
                                  {"Кр1":"uncertain"},
                                  (Evidence("Кр1","uncertain","Недостаточно данных об автономности",0),))
        interview = FullIUTInterview(self.knowledge(), ScriptedInterpreter([decision]))
        session, first = interview.start()
        out = interview.answer(session.session_id, "Обычная рабочая неделя...")
        self.assertEqual(session.history[0][0], first)
        self.assertEqual(session.pending_question, out["question"])
        self.assertEqual(session.evidence[0].answer_index, 0)

    def test_empty_answer_does_not_advance(self):
        interview = FullIUTInterview(self.knowledge(), ScriptedInterpreter([]))
        session, first = interview.start()
        out = interview.answer(session.session_id, "   ")
        self.assertEqual(out["question"], first)
        self.assertEqual(session.history, [])

    def test_sequence_law_is_deterministic(self):
        decision = Interpretation("next", {"Кр4":"supported"})
        interview = FullIUTInterview(self.knowledge(), ScriptedInterpreter([decision]))
        session, _ = interview.start()
        with self.assertRaises(InvalidInterpretation):
            interview.answer(session.session_id, "answer")

    def test_kr7_is_rejected(self):
        decision = Interpretation("next", {"Кр7":"supported"})
        interview = FullIUTInterview(self.knowledge(), ScriptedInterpreter([decision]))
        session, _ = interview.start()
        with self.assertRaises(InvalidInterpretation):
            interview.answer(session.session_id, "answer")

    def test_completed_result_has_canonical_shape_without_precise_iut(self):
        decision = Interpretation(None, {"Кр1":"supported"},
            (Evidence("Кр1","supporting","Самостоятельно поддерживает базовое функционирование",0),),
            stop=True, main_question="Насколько устойчива социальная автономность?")
        interview = FullIUTInterview(self.knowledge(), ScriptedInterpreter([decision]))
        session, _ = interview.start()
        out = interview.answer(session.session_id, "Конкретный биографический пример")
        result = out["result"]
        self.assertEqual(result["confirmed_level"], "L1 — Зависимость")
        self.assertEqual(result["iut_range"], "5–15")
        self.assertEqual(result["within_range"], "не операционализировано")
        self.assertIn("source_sha", result["provenance"])

    def test_interpreter_cannot_attach_evidence_to_future_answer(self):
        decision = Interpretation("next", evidence=(Evidence("Кр1","uncertain","fact",3),))
        interview = FullIUTInterview(self.knowledge(), ScriptedInterpreter([decision]))
        session, _ = interview.start()
        with self.assertRaises(InvalidInterpretation):
            interview.answer(session.session_id, "answer")


if __name__ == "__main__": unittest.main()
