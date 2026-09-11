"""Deterministic orchestration boundary for the canonical full HOS IUT interview.

This module deliberately does not infer crisis passage from free text. That judgement
belongs to a replaceable interpreter constrained by the pinned canonical snapshot.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Protocol

PROTOCOL_ID = "ИУТ — измерение"
CRISES = tuple(f"Кр{i}" for i in range(1, 7))
STATES = {"unexamined", "uncertain", "supported", "contradicted"}

class FullIUTUnavailable(RuntimeError): pass
class InvalidInterpretation(RuntimeError): pass

class ExactRetriever(Protocol):
    def exact(self, object_id: str, version: str | None = None) -> dict: ...

@dataclass(frozen=True)
class Evidence:
    crisis: str
    stance: str  # supporting | contradictory | uncertain
    fact: str
    answer_index: int

@dataclass(frozen=True)
class Interpretation:
    next_question: str | None
    crisis_updates: dict[str, str] = field(default_factory=dict)
    evidence: tuple[Evidence, ...] = ()
    stop: bool = False
    main_question: str = ""

class Interpreter(Protocol):
    def interpret(self, *, canonical_text: str, history: tuple[tuple[str, str], ...],
                  crisis_states: dict[str, str]) -> Interpretation: ...

@dataclass
class FullSession:
    session_id: str
    source_path: str
    source_sha: str
    version: str
    canonical_text: str
    history: list[tuple[str, str]] = field(default_factory=list)
    crisis_states: dict[str, str] = field(default_factory=lambda: {c: "unexamined" for c in CRISES})
    evidence: list[Evidence] = field(default_factory=list)
    completed: bool = False
    main_question: str = ""

class FullIUTInterview:
    def __init__(self, knowledge: ExactRetriever, interpreter: Interpreter):
        self.knowledge, self.interpreter = knowledge, interpreter
        self.sessions: dict[str, FullSession] = {}

    def start(self) -> tuple[FullSession, str]:
        obj = self.knowledge.exact(PROTOCOL_ID)
        if obj.get("status") != "ok" or any(not obj.get(k) for k in ("source_path", "source_sha", "text")):
            raise FullIUTUnavailable("exact canonical full-IUT protocol unavailable or provenance incomplete")
        version = re.search(r"^\*\*Версия:\*\*\s*([^\\\n]+)", obj["text"], re.MULTILINE)
        if not version:
            raise FullIUTUnavailable("canonical protocol version missing")
        first = re.search(r"^1\.\s+(.+)$", obj["text"], re.MULTILINE)
        if not first:
            raise FullIUTUnavailable("canonical entrance screening missing")
        session = FullSession(str(uuid.uuid4()), obj["source_path"], obj["source_sha"],
                              version.group(1).strip(), obj["text"])
        self.sessions[session.session_id] = session
        return session, first.group(1).strip()

    def answer(self, session_id: str, question: str, answer: str) -> dict:
        s = self.sessions[session_id]
        if s.completed: return {"status": "completed", "result": self.result(s)}
        s.history.append((question.strip(), answer.strip()))
        decision = self.interpreter.interpret(canonical_text=s.canonical_text,
            history=tuple(s.history), crisis_states=dict(s.crisis_states))
        self._apply(s, decision)
        if decision.stop:
            s.completed = True
            return {"status": "completed", "result": self.result(s)}
        if not decision.next_question:
            raise InvalidInterpretation("interpreter must provide next_question unless stopping")
        return {"status": "in_progress", "question": decision.next_question}

    def _apply(self, s: FullSession, d: Interpretation) -> None:
        for crisis, state in d.crisis_updates.items():
            if crisis not in CRISES or state not in STATES:
                raise InvalidInterpretation("unknown crisis/state; Кр7 is not operationalized")
            n = int(crisis[2:])
            if state == "supported" and any(s.crisis_states[f"Кр{i}"] != "supported" for i in range(1, n)):
                raise InvalidInterpretation("sequence law violation")
            s.crisis_states[crisis] = state
        for e in d.evidence:
            if e.crisis not in CRISES or e.stance not in {"supporting", "contradictory", "uncertain"}:
                raise InvalidInterpretation("invalid evidence")
            s.evidence.append(e)
        s.main_question = d.main_question or s.main_question

    @staticmethod
    def result(s: FullSession) -> dict:
        highest = 0
        for i in range(1, 7):
            if s.crisis_states[f"Кр{i}"] == "supported": highest = i
            else: break
        levels = {
            0:("L0 — Беспомощность","0–5","Кр1 самообслуживания"),
            1:("L1 — Зависимость","5–15","Кр2 социализации"),
            2:("L2 — Подчинение","15–25","Кр3 самостоятельности"),
            3:("L3 — Необходимость","25–40","Кр4 интереса"),
            4:("L4 — Увлекающаяся личность","40–60","Кр5 интеграции"),
            5:("L5 — Интегрированная личность","60–80","Кр6 смысла"),
            6:("L6 — Реализация Смысла","80–95","Кр7 просветления"),
        }
        level, rng, nxt = levels[highest]
        supporting=[e.fact for e in s.evidence if e.stance=="supporting"]
        uncertain=[e.fact for e in s.evidence if e.stance!="supporting"]
        return {"confirmed_level":level,"iut_range":rng,"next_crisis":nxt,
                "transition_status":s.crisis_states.get(f"Кр{highest+1}","не операционализировано"),
                "within_range":"не операционализировано","supporting_evidence":supporting,
                "contradictory_or_uncertain_evidence":uncertain,"main_question":s.main_question,
                "provenance":{"source_path":s.source_path,"source_sha":s.source_sha,"version":s.version}}
