import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
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


class ExpressDiagnosticE2E(unittest.TestCase):
    def test_e1_e2_exact_retrieval_and_start(self):
        executor, service = make_executor()
        exact = service.exact(PROTOCOL_ID)
        self.assertEqual(exact["text"], (ROOT / SOURCE).read_text(encoding="utf-8"))  # E1
        session, message = executor.start()
        self.assertEqual(session.status, SessionStatus.IN_PROGRESS)  # E2
        self.assertIn(session.protocol.questions[0], message)

    def test_e3_e4_sequence_and_answer_storage(self):
        executor, _ = make_executor()
        session, _ = executor.start()
        for number in range(12):
            response = executor.respond(session.session_id, str(number % 11))
            if number < 11:
                self.assertIn(f"## Вопрос {number + 2}", response)  # E3
        self.assertEqual(session.answers, list(range(11)) + [0])  # E4

    def test_e5_intermediate_interpretation_is_refused(self):
        executor, _ = make_executor(); session, _ = executor.start()
        for value in (5, 6, 7): executor.respond(session.session_id, str(value))
        before = (session.current_question, list(session.answers))
        reply = executor.respond(session.session_id, "Что уже можно сказать обо мне?")
        self.assertEqual((session.current_question, session.answers), before)
        self.assertIn("нельзя", reply)
        self.assertIn("## Вопрос 4", reply)

    def test_e6_invalid_answer_does_not_move_state(self):
        executor, _ = make_executor(); session, _ = executor.start()
        for invalid in ("11", "-1", "не знаю", "15"):
            self.assertIn("от 0 до 10", executor.respond(session.session_id, invalid))
            self.assertEqual(session.current_question, 0)
            self.assertEqual(session.answers, [])

    def test_e7_skip_is_forbidden(self):
        executor, _ = make_executor(); session, _ = executor.start()
        reply = executor.respond(session.session_id, "Пропустим Q4")
        self.assertIn("нельзя пропустить", reply)
        self.assertEqual(session.current_question, 0)

    def test_e8_scale_change_is_forbidden(self):
        executor, _ = make_executor(); session, _ = executor.start()
        reply = executor.respond(session.session_id, "Давайте шкалу 1–5")
        self.assertIn("шкалу изменить", reply)
        self.assertEqual(session.current_question, 0)

    def test_e9_e10_e11_result_constraints_and_feedback(self):
        executor, _ = make_executor(); session, _ = executor.start()
        result = ""
        for value in range(12): result = executor.respond(session.session_id, str(value % 11))
        self.assertIn("Q12=0", result)  # E9 full vector
        for heading in ("Сильные показатели", "Низкие или контрастные показатели", "Конфигурация", "Оговорка"):
            self.assertIn(heading, result)  # E10
        self.assertNotIn("среднее", result.casefold())
        self.assertNotIn("уровень L:", result)
        self.assertIn("оценить по шкале 0–10", result)  # E11
        self.assertEqual(session.status, SessionStatus.AWAITING_FEEDBACK)

    def test_e12_e13_revision_propagation_and_session_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / SOURCE.parent).mkdir(parents=True)
            (root / "knowledge_service").mkdir()
            shutil.copy(ROOT / SOURCE, root / SOURCE)
            registry = json.loads((ROOT / "knowledge_service/registry.json").read_text(encoding="utf-8"))
            (root / "knowledge_service/registry.json").write_text(
                json.dumps({"objects": [registry["objects"][0]]}, ensure_ascii=False), encoding="utf-8")
            executor, service = make_executor(root)
            old, old_message = executor.start()
            old_sha, old_question = old.source_sha, old.protocol.questions[0]
            path = root / SOURCE
            path.write_text(path.read_text(encoding="utf-8").replace("## Вопрос 1", "## Вопрос 1\n\nМАРКЕР НОВОЙ РЕДАКЦИИ", 1), encoding="utf-8")
            service.build()
            new, new_message = executor.start()
            self.assertNotEqual(new.source_sha, old_sha)  # E12
            self.assertIn("МАРКЕР НОВОЙ РЕДАКЦИИ", new_message)
            self.assertEqual(old.source_sha, old_sha)  # E13
            self.assertEqual(old.protocol.questions[0], old_question)
            self.assertNotIn("МАРКЕР", executor.respond(old.session_id, "5"))

    def test_e14_missing_exact_protocol_is_controlled_failure(self):
        executor, _ = make_executor()
        executor.protocol_id = "missing"
        session, reply = executor.start()
        self.assertEqual(session.status, SessionStatus.ERROR)
        self.assertIn("не запущена", reply)

    def test_e15_application_has_no_hardcoded_question_copy(self):
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
        for _ in range(12): executor.respond(session.session_id, "5")
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
