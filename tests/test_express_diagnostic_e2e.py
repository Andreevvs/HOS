import json
import shutil
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from diagnostic_service.executor import ProtocolExecutor, SessionStatus
from diagnostic_service.web import PROTOCOL_ID, handler_for
from http.server import ThreadingHTTPServer
from knowledge_service import KnowledgeService


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("03 — Диагностика/Экспресс-тест уровня развития v0.1.md")


def make_executor(root=ROOT):
    service = KnowledgeService(root, root / "knowledge_service" / "registry.json")
    service.build()
    return ProtocolExecutor(service, PROTOCOL_ID), service


def finish(executor, session, values):
    result = ""
    for value in values:
        result = executor.respond(session.session_id, str(value))
    return result


class ExpressDiagnosticE2E(unittest.TestCase):
    def test_e1_e2_exact_retrieval_and_start(self):
        executor, service = make_executor()
        exact = service.exact(PROTOCOL_ID)
        self.assertEqual(exact["text"], (ROOT / SOURCE).read_text(encoding="utf-8"))
        session, message = executor.start()
        self.assertEqual(session.status, SessionStatus.IN_PROGRESS)
        self.assertIn(session.protocol.questions[0], message)

    def test_e3_e4_sequence_and_answer_storage(self):
        executor, _ = make_executor()
        session, _ = executor.start()
        for number in range(12):
            response = executor.respond(session.session_id, str(number % 11))
            if number < 11:
                self.assertIn(f"## Вопрос {number + 2}", response)
        self.assertEqual(session.answers, list(range(11)) + [0])

    def test_intermediate_interpretation_is_refused(self):
        executor, _ = make_executor(); session, _ = executor.start()
        for value in (5, 6, 7): executor.respond(session.session_id, str(value))
        before = (session.current_question, list(session.answers))
        reply = executor.respond(session.session_id, "Что уже можно сказать обо мне?")
        self.assertEqual((session.current_question, session.answers), before)
        self.assertIn("нельзя", reply)
        self.assertIn("## Вопрос 4", reply)

    def test_invalid_answer_does_not_move_state(self):
        executor, _ = make_executor(); session, _ = executor.start()
        for invalid in ("11", "-1", "не знаю", "15"):
            self.assertIn("от 0 до 10", executor.respond(session.session_id, invalid))
            self.assertEqual(session.current_question, 0)
            self.assertEqual(session.answers, [])

    def test_skip_and_scale_change_are_forbidden(self):
        executor, _ = make_executor(); session, _ = executor.start()
        self.assertIn("нельзя пропустить", executor.respond(session.session_id, "Пропустим"))
        self.assertIn("шкалу изменить", executor.respond(session.session_id, "Давайте шкалу 1–5"))
        self.assertEqual(session.current_question, 0)

    def test_result_contains_approved_structure(self):
        executor, _ = make_executor(); session, _ = executor.start()
        result = finish(executor, session, [5] * 12)
        for text in (
            "Предварительная оценка развития HOS",
            "Наиболее вероятная зона",
            "Ориентировочный диапазон ИУТ",
            "Уверенность",
            "Почему",
            "Сильные показатели",
            "Низкие или контрастные показатели",
            "Текущая жизнь",
            "Ограничение",
            "полная диагностика HOS",
        ):
            self.assertIn(text, result)
        self.assertNotIn("ИУТ =", result)
        self.assertIn("оценить по шкале 0–10", result)
        self.assertEqual(session.status, SessionStatus.AWAITING_FEEDBACK)

    def test_profile_successful_l3_not_promoted_by_good_context(self):
        executor, _ = make_executor(); session, _ = executor.start()
        # Good life/context, strong realization and control, but low interest signal.
        result = finish(executor, session, [10, 10, 8, 10, 2, 9, 9, 10, 6, 10, 10, 10])
        self.assertIn("L3 — Необходимость", result)
        self.assertIn("25–40", result)
        self.assertNotIn("возможного L5", result)

    def test_profile_bright_chaotic_l4_is_not_demoted(self):
        executor, _ = make_executor(); session, _ = executor.start()
        result = finish(executor, session, [6, 7, 5, 7, 9, 5, 9, 6, 5, 7, 6, 6])
        self.assertIn("L4 — Увлекающаяся личность", result)
        self.assertIn("40–60", result)

    def test_profile_l5_hard_period_stays_possible_not_confirmed(self):
        executor, _ = make_executor(); session, _ = executor.start()
        result = finish(executor, session, [2, 6, 9, 5, 9, 9, 9, 2, 9, 2, 5, 4])
        self.assertIn("признаками возможного L5", result)
        self.assertIn("L5 (60–80) этим тестом не подтверждается", result)
        self.assertNotIn("Наиболее вероятная зона: L5", result)

    def test_all_tens_do_not_produce_l6_or_confirmed_l5(self):
        executor, _ = make_executor(); session, _ = executor.start()
        result = finish(executor, session, [10] * 12)
        self.assertIn("L4 — Увлекающаяся личность, с признаками возможного L5", result)
        self.assertIn("L6 этим тестом не диагностируется", result)
        self.assertNotIn("Наиболее вероятная зона: L5", result)
        self.assertNotIn("Наиболее вероятная зона: L6", result)

    def test_low_q6_q7_gates_higher_level(self):
        executor, _ = make_executor(); session, _ = executor.start()
        result = finish(executor, session, [9, 9, 9, 9, 10, 2, 2, 9, 10, 9, 9, 9])
        self.assertIn("L2 или ниже", result)
        self.assertNotIn("L4 —", result)

    def test_revision_propagation_and_session_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / SOURCE.parent).mkdir(parents=True)
            (root / "knowledge_service").mkdir()
            shutil.copy(ROOT / SOURCE, root / SOURCE)
            registry = json.loads((ROOT / "knowledge_service/registry.json").read_text(encoding="utf-8"))
            (root / "knowledge_service/registry.json").write_text(
                json.dumps({"objects": [registry["objects"][0]]}, ensure_ascii=False), encoding="utf-8")
            executor, service = make_executor(root)
            old, _ = executor.start()
            old_sha, old_question = old.source_sha, old.protocol.questions[0]
            path = root / SOURCE
            path.write_text(path.read_text(encoding="utf-8").replace("## Вопрос 1", "## Вопрос 1\n\nМАРКЕР НОВОЙ РЕДАКЦИИ", 1), encoding="utf-8")
            service.build()
            new, new_message = executor.start()
            self.assertNotEqual(new.source_sha, old_sha)
            self.assertIn("МАРКЕР НОВОЙ РЕДАКЦИИ", new_message)
            self.assertEqual(old.source_sha, old_sha)
            self.assertEqual(old.protocol.questions[0], old_question)
            self.assertNotIn("МАРКЕР", executor.respond(old.session_id, "5"))

    def test_missing_exact_protocol_is_controlled_failure(self):
        executor, _ = make_executor(); executor.protocol_id = "missing"
        session, reply = executor.start()
        self.assertEqual(session.status, SessionStatus.ERROR)
        self.assertIn("не запущена", reply)

    def test_application_has_no_hardcoded_question_copy(self):
        canonical = (ROOT / SOURCE).read_text(encoding="utf-8")
        distinctive = "Если представить, что **следующие 3 года ваша жизнь"
        self.assertIn(distinctive, canonical)
        application = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "diagnostic_service").glob("*.py"))
        self.assertNotIn(distinctive, application)

    def test_unrelated_question_preserves_session(self):
        executor, _ = make_executor(); session, _ = executor.start()
        executor.respond(session.session_id, "4")
        before = (session.current_question, list(session.answers), session.source_sha)
        executor.respond(session.session_id, "Какая сегодня погода?")
        self.assertEqual((session.current_question, session.answers, session.source_sha), before)
        self.assertIn("## Вопрос 3", executor.respond(session.session_id, "5"))

    def test_feedback_completes_session(self):
        executor, _ = make_executor(); session, _ = executor.start()
        finish(executor, session, [5] * 12)
        executor.respond(session.session_id, "8")
        executor.respond(session.session_id, "Баланс")
        reply = executor.respond(session.session_id, "Не согласен с контрастом")
        self.assertEqual(session.status, SessionStatus.COMPLETED)
        self.assertIn("завершён", reply)

    def test_minimal_web_api(self):
        executor, _ = make_executor()
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(executor))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            self.assertIn("HOS", urllib.request.urlopen(base).read().decode())
            request = urllib.request.Request(base + "/sessions", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
            started = json.loads(urllib.request.urlopen(request).read())
            self.assertIn("## Вопрос 1", started["message"])
            payload = json.dumps({"message": "5"}).encode()
            request = urllib.request.Request(base + f"/sessions/{started['session_id']}/messages", data=payload, headers={"Content-Type": "application/json"}, method="POST")
            self.assertIn("## Вопрос 2", json.loads(urllib.request.urlopen(request).read())["message"])
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__":
    unittest.main()
