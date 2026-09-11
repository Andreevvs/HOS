"""Stateful execution of a protocol retrieved from the Canonical KB.

The application contains no copy of the diagnostic questions. Interpretation rules here
are only the minimal executable projection of the canonical v0.1 specification.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class ExactRetriever(Protocol):
    def exact(self, object_id: str, version: str | None = None) -> dict: ...


class ProtocolUnavailable(RuntimeError):
    pass


class ProtocolFormatError(RuntimeError):
    pass


class SessionStatus(str, Enum):
    INITIALIZED = "initialized"
    IN_PROGRESS = "in_progress"
    AWAITING_FEEDBACK = "awaiting_feedback"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass(frozen=True)
class ParsedProtocol:
    intro: str
    questions: tuple[str, ...]
    dimensions: tuple[str, ...]
    feedback_prompts: tuple[str, ...]


@dataclass
class DiagnosticSession:
    session_id: str
    protocol_id: str
    source_path: str
    source_sha: str
    protocol_text: str
    protocol: ParsedProtocol | None
    current_question: int = 0
    answers: list[int] = field(default_factory=list)
    status: SessionStatus = SessionStatus.INITIALIZED
    feedback: list[str] = field(default_factory=list)
    result_text: str | None = None
    error: str | None = None


def parse_protocol(text: str) -> ParsedProtocol:
    intro_match = re.search(r"^1\. Перед началом сообщить человеку, что (.+)$", text, re.MULTILINE)
    question_matches = list(re.finditer(r"^## Вопрос (\d+)\s*$", text, re.MULTILINE))
    interpretation = re.search(r"^## Интерпретация\s*$", text, re.MULTILINE)
    if not intro_match or len(question_matches) != 12 or not interpretation:
        raise ProtocolFormatError("canonical protocol must contain intro, Q1-Q12 and interpretation")
    if [int(m.group(1)) for m in question_matches] != list(range(1, 13)):
        raise ProtocolFormatError("canonical questions are not ordered Q1-Q12")

    questions = []
    for index, match in enumerate(question_matches):
        end = question_matches[index + 1].start() if index < 11 else interpretation.start()
        questions.append(text[match.start():end].strip())

    interpretation_text = text[interpretation.start():]
    labels = dict((int(number), label.strip().rstrip(";.")) for number, label in re.findall(r"^- Q(\d+) — (.+)$", interpretation_text, re.MULTILINE))
    if set(labels) != set(range(1, 13)):
        raise ProtocolFormatError("canonical interpretation must describe Q1-Q12")

    feedback_match = re.search(r"^\d+\. После интерпретации попросить человека:\s*\n((?:\s+- .+\n?){3})", text, re.MULTILINE)
    if not feedback_match:
        raise ProtocolFormatError("canonical feedback instructions are missing")
    feedback = tuple(item.strip().rstrip(";.") for item in re.findall(r"^\s+- (.+)$", feedback_match.group(1), re.MULTILINE))
    return ParsedProtocol(intro_match.group(1).strip(), tuple(questions), tuple(labels[n] for n in range(1, 13)), feedback)


class ProtocolExecutor:
    """In-memory technical session manager pinned to exact retrieval snapshots."""

    def __init__(self, knowledge_service: ExactRetriever, protocol_id: str):
        self.knowledge_service = knowledge_service
        self.protocol_id = protocol_id
        self.sessions: dict[str, DiagnosticSession] = {}

    def start(self) -> tuple[DiagnosticSession, str]:
        try:
            result = self.knowledge_service.exact(self.protocol_id)
            if result.get("status") != "ok":
                raise ProtocolUnavailable("exact canonical protocol is unavailable")
            required = ("source_path", "source_sha", "text")
            if any(not result.get(key) for key in required):
                raise ProtocolUnavailable("exact result has incomplete provenance")
            protocol = parse_protocol(result["text"])
            session = DiagnosticSession(str(uuid.uuid4()), self.protocol_id, result["source_path"], result["source_sha"], result["text"], protocol)
            self.sessions[session.session_id] = session
            session.status = SessionStatus.IN_PROGRESS
            return session, protocol.intro + "\n\n" + protocol.questions[0]
        except Exception as exc:
            session = DiagnosticSession(str(uuid.uuid4()), self.protocol_id, "", "", "", None, status=SessionStatus.ERROR, error=str(exc))
            self.sessions[session.session_id] = session
            return session, "Диагностика не запущена: канонический протокол недоступен или не прошёл проверку."

    def respond(self, session_id: str, message: str) -> str:
        session = self.sessions[session_id]
        if session.status == SessionStatus.ERROR:
            return "Диагностика недоступна из-за ошибки канонического протокола."
        if session.status == SessionStatus.COMPLETED:
            return "Сеанс уже завершён."
        if session.status == SessionStatus.AWAITING_FEEDBACK:
            session.feedback.append(message.strip())
            if len(session.feedback) < len(session.protocol.feedback_prompts):
                return session.protocol.feedback_prompts[len(session.feedback)]
            session.status = SessionStatus.COMPLETED
            return "Спасибо, обратная связь сохранена. Тест завершён."

        value = self._number(message)
        if value is None:
            return "Нужен ответ одним числом от 0 до 10; вопрос нельзя пропустить, шкалу изменить или интерпретировать ответ до завершения.\n\n" + session.protocol.questions[session.current_question]
        session.answers.append(value)
        session.current_question += 1
        if session.current_question < 12:
            return session.protocol.questions[session.current_question]
        session.status = SessionStatus.AWAITING_FEEDBACK
        session.result_text = self._interpret(session)
        return session.result_text

    @staticmethod
    def _number(message: str) -> int | None:
        if not re.fullmatch(r"\s*(?:10|[0-9])\s*", message):
            return None
        return int(message.strip())

    @staticmethod
    def _zone(value: int) -> str:
        if value <= 3: return "low"
        if value <= 6: return "mid"
        return "high"

    @classmethod
    def _level_hypothesis(cls, a: list[int]) -> tuple[str, str, str, str]:
        q3, q5, q6, q7, q9 = a[2], a[4], a[5], a[6], a[8]
        z3, z5, z6, z7, z9 = map(cls._zone, (q3, q5, q6, q7, q9))
        if z6 == "low" and z7 == "low":
            return "L2 или ниже", "при гипотезе L2 — 15–25; более низкие уровни этот тест надёжно не различает", "средняя", "Q6 и Q7 находятся в низкой зоне: самостоятельная организация и реализация намерений выражены слабо."
        if "low" in (z6, z7):
            return "пограничная зона L2→L3", "около границы 25; точнее этот тест не позиционирует", "низкая", "Q6 и Q7 расходятся: признаки самостоятельности и результативности противоречивы."
        authorship = z5 == "high" and z7 == "high"
        support = z3 == "high" or z9 == "high"
        if not authorship:
            confidence = "высокая" if z5 == "low" and z6 == "high" and z7 == "high" else "средняя"
            return "L3 — Необходимость", "25–40", confidence, "Q6/Q7 указывают на самостоятельность и реализацию, но картина Авторства по Q5/Q7 и поддержке Q3/Q9 недостаточно выражена."
        if z3 == z5 == z6 == z7 == z9 == "high":
            return "L5 — Интегрированная личность", "60–80", "средняя", "Согласованно выражены Авторство, значимая направленность и реализация (Q3/Q5/Q6/Q7/Q9). Это даёт предварительную гипотезу L5; Интеграция Кр5 проверяется здесь только косвенно."
        confidence = "средняя" if support else "низкая"
        return "L4 — Увлекающаяся личность", "40–60", confidence, "Q5/Q7 дают выраженные признаки Авторства; Q6/Q9 уточняют качество реализации и направления. Для предварительной гипотезы L5 нужна согласованно высокая конфигурация Q3/Q5/Q6/Q7/Q9."

    @classmethod
    def _interpret(cls, session: DiagnosticSession) -> str:
        answers = session.answers
        level, iut, confidence, why = cls._level_hypothesis(answers)
        ranked_high = sorted(range(12), key=lambda i: (-answers[i], i))[:3]
        ranked_low = sorted(range(12), key=lambda i: (answers[i], i))[:3]
        describe = lambda i: f"Q{i + 1}={answers[i]} — {session.protocol.dimensions[i]}"
        vector = ", ".join(f"Q{i + 1}={value}" for i, value in enumerate(answers))
        q1, q4, q8, q10 = answers[0], answers[3], answers[7], answers[9]
        q2, q11, q12 = answers[1], answers[10], answers[11]
        current = f"Контекст текущей жизни: удовлетворённость Q1={q1}, отношения Q4={q4}, энергия Q8={q8}, тональность жизни Q10={q10}. Динамика и траектория: Q2={q2}, Q11={q11}, Q12={q12}."
        return (
            "Предварительная оценка развития HOS\n\n" + f"Полный вектор: {vector}\n\n" +
            f"Наиболее вероятная зона: {level}.\nОриентировочный диапазон ИУТ: {iut}.\nУверенность: {confidence}.\n\nПочему: {why}\n\n" +
            "Сильные показатели:\n- " + "\n- ".join(describe(i) for i in ranked_high) +
            "\n\nНизкие или контрастные показатели:\n- " + "\n- ".join(describe(i) for i in ranked_low) +
            f"\n\nТекущая жизнь: {current}\n\n" +
            "Ограничение: это экспресс-гипотеза по самооценке текущей жизни. Она не подтверждает прохождение кризисов HOS; для подтверждения уровня и более точной оценки ИУТ используется полная диагностика HOS. Для L5 Интеграция Кр5 оценивается здесь только косвенно. L6 этим тестом не диагностируется.\n\n" + session.protocol.feedback_prompts[0]
        )
