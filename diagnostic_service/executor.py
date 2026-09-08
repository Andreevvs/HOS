"""Stateful execution of a protocol retrieved from the Canonical KB.

This module intentionally contains no diagnostic question or interpretation label.
Those values are parsed from the exact whole-document result at session creation.
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
    """The canonical executable protocol cannot be retrieved or verified."""


class ProtocolFormatError(RuntimeError):
    """The retrieved document does not expose the required v0.1 structure."""


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
    disclaimer: str
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
    error: str | None = None


def parse_protocol(text: str) -> ParsedProtocol:
    intro_match = re.search(r"^1\. Перед началом сообщить человеку, что (.+)$", text, re.MULTILINE)
    question_matches = list(re.finditer(r"^## Вопрос (\d+)\s*$", text, re.MULTILINE))
    interpretation = re.search(r"^## Интерпретация\s*$", text, re.MULTILINE)
    if not intro_match or len(question_matches) != 12 or not interpretation:
        raise ProtocolFormatError("canonical protocol must contain Q1-Q12 and interpretation")
    numbers = [int(match.group(1)) for match in question_matches]
    if numbers != list(range(1, 13)):
        raise ProtocolFormatError("canonical questions are not ordered Q1-Q12")
    questions = []
    for index, match in enumerate(question_matches):
        end = question_matches[index + 1].start() if index < 11 else interpretation.start()
        questions.append(text[match.start():end].strip())

    interpretation_text = text[interpretation.start():]
    labels = dict(
        (int(number), label.strip().rstrip(";."))
        for number, label in re.findall(r"^- Q(\d+) — (.+)$", interpretation_text, re.MULTILINE)
    )
    if set(labels) != set(range(1, 13)):
        raise ProtocolFormatError("canonical interpretation must describe Q1-Q12")
    disclaimer_match = re.search(
        r"явную оговорку, что (.+?);\s*$", interpretation_text, re.MULTILINE
    )
    if not disclaimer_match:
        raise ProtocolFormatError("canonical interpretation disclaimer is missing")
    feedback_match = re.search(r"^9\. После интерпретации попросить человека:\s*\n"
                               r"((?:\s+- .+\n?){3})", text, re.MULTILINE)
    if not feedback_match:
        raise ProtocolFormatError("canonical feedback instructions are missing")
    feedback = tuple(item.strip().rstrip(";.") for item in
                     re.findall(r"^\s+- (.+)$", feedback_match.group(1), re.MULTILINE))
    return ParsedProtocol(intro_match.group(1).strip(), tuple(questions),
                          tuple(labels[n] for n in range(1, 13)),
                          disclaimer_match.group(1).strip(), feedback)


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
            session = DiagnosticSession(str(uuid.uuid4()), self.protocol_id,
                                        result["source_path"], result["source_sha"],
                                        result["text"], protocol)
            self.sessions[session.session_id] = session
            session.status = SessionStatus.IN_PROGRESS
            return session, protocol.intro + "\n\n" + protocol.questions[0]
        except Exception as exc:
            session = DiagnosticSession(str(uuid.uuid4()), self.protocol_id, "", "", "",
                                        None, status=SessionStatus.ERROR, error=str(exc))
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
            return ("Нужен ответ одним числом от 0 до 10; вопрос нельзя пропустить, "
                    "шкалу изменить или интерпретировать ответ до завершения.\n\n"
                    + session.protocol.questions[session.current_question])
        session.answers.append(value)
        session.current_question += 1
        if session.current_question < 12:
            return session.protocol.questions[session.current_question]
        session.status = SessionStatus.AWAITING_FEEDBACK
        return self._interpret(session)

    @staticmethod
    def _number(message: str) -> int | None:
        if not re.fullmatch(r"\s*(?:10|[0-9])\s*", message):
            return None
        return int(message.strip())

    @staticmethod
    def _interpret(session: DiagnosticSession) -> str:
        ranked_high = sorted(range(12), key=lambda i: (-session.answers[i], i))[:3]
        ranked_low = sorted(range(12), key=lambda i: (session.answers[i], i))[:3]
        describe = lambda i: f"Q{i + 1}={session.answers[i]} — {session.protocol.dimensions[i]}"
        vector = ", ".join(f"Q{i + 1}={value}" for i, value in enumerate(session.answers))
        gap = session.answers[ranked_high[0]] - session.answers[ranked_low[0]]
        configuration = (
            f"Контраст между самым высоким и низким показателями составляет {gap} пунктов; "
            f"сочетание «{session.protocol.dimensions[ranked_high[0]]}» и "
            f"«{session.protocol.dimensions[ranked_low[0]]}» описывает неоднородный "
            "контекстный профиль, а не единую шкалу."
        )
        return (f"Полный вектор: {vector}\n\nСильные показатели:\n- "
                + "\n- ".join(describe(i) for i in ranked_high)
                + "\n\nНизкие или контрастные показатели:\n- "
                + "\n- ".join(describe(i) for i in ranked_low)
                + f"\n\nКонфигурация: {configuration}\n\nОговорка: "
                + session.protocol.disclaimer
                + "\n\n" + session.protocol.feedback_prompts[0])
