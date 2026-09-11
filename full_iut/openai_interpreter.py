"""Replaceable OpenAI Responses API interpreter for the full IUT interview.

This is application infrastructure, not HOS methodology. The pinned canonical snapshot
is the sole methodological source supplied to the model for each decision.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .interview import Evidence, Interpretation, InvalidInterpretation


class InterpreterUnavailable(RuntimeError):
    pass


class OpenAIInterpreter:
    """Interpret free-text answers while keeping deterministic HOS guards outside the LLM."""

    endpoint = "https://api.openai.com/v1/responses"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model or os.environ.get("HOS_INTERPRETER_MODEL", "gpt-5.6")
        if not self.api_key:
            raise InterpreterUnavailable("OPENAI_API_KEY is not configured")

    def interpret(self, *, canonical_text: str, history: tuple[tuple[str, str], ...],
                  crisis_states: dict[str, str]) -> Interpretation:
        prompt = self._prompt(canonical_text, history, crisis_states)
        payload = {
            "model": self.model,
            "input": prompt,
        }
        req = Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=90) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise InterpreterUnavailable(f"LLM interpreter request failed: {exc}") from exc
        text = self._output_text(body)
        return self._decode(text, len(history) - 1)

    @staticmethod
    def _prompt(canonical_text: str, history: tuple[tuple[str, str], ...],
                crisis_states: dict[str, str]) -> str:
        transcript = "\n\n".join(
            f"Вопрос {i + 1}: {q}\nОтвет {i + 1}: {a}" for i, (q, a) in enumerate(history)
        )
        return f"""Ты — интерпретирующий слой полной диагностики ИУТ HOS.
Единственный источник методики — КАНОНИЧЕСКИЙ SNAPSHOT ниже. Не добавляй критерии,
уровни, кризисы, числовые пороги или формулы. Соблюдай закон последовательности.
Не сообщай пользователю промежуточный уровень или диапазон ИУТ.
Банк вопросов — банк, а не обязательная последовательность 1–50. Выбирай следующий
наиболее информативный канонический вопрос либо допустимое уточнение по принципу
факт → контекст → мотив → длительность → последствия → цена выбора → проверка реальностью.
Один ответ не считать доказательством кризиса. При недостатке данных оставляй uncertain.
Кр7 не диагностировать. stop=true только когда дальнейшие вопросы существенно меньше
увеличивают определённость.

Верни ТОЛЬКО один JSON-объект без markdown со схемой:
{{"next_question": string|null,
  "crisis_updates": {{"Кр1"|"Кр2"|"Кр3"|"Кр4"|"Кр5"|"Кр6": "unexamined"|"uncertain"|"supported"|"contradicted"}},
  "evidence": [{{"crisis":"Кр1".."Кр6","stance":"supporting"|"contradictory"|"uncertain","fact":"краткий конкретный факт из ответа"}}],
  "stop": boolean,
  "main_question": string}}
Указывай в crisis_updates только реально переоценённые кризисы. Evidence должен опираться
на фактический ответ, а не на предположение.

КАНОНИЧЕСКИЙ SNAPSHOT:
---
{canonical_text}
---

ТЕКУЩИЕ ТЕХНИЧЕСКИЕ СОСТОЯНИЯ КРИЗИСОВ:
{json.dumps(crisis_states, ensure_ascii=False)}

ИСТОРИЯ ИНТЕРВЬЮ:
{transcript}
"""

    @staticmethod
    def _output_text(body: dict) -> str:
        if isinstance(body.get("output_text"), str):
            return body["output_text"]
        parts = []
        for item in body.get("output", []):
            for content in item.get("content", []):
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if not parts:
            raise InvalidInterpretation("LLM response contains no text output")
        return "\n".join(parts)

    @staticmethod
    def _decode(text: str, answer_index: int) -> Interpretation:
        try:
            raw = json.loads(text.strip())
        except json.JSONDecodeError as exc:
            raise InvalidInterpretation("LLM response is not valid JSON") from exc
        if not isinstance(raw, dict):
            raise InvalidInterpretation("LLM interpretation must be a JSON object")
        updates = raw.get("crisis_updates", {})
        evidence_raw = raw.get("evidence", [])
        if not isinstance(updates, dict) or not isinstance(evidence_raw, list):
            raise InvalidInterpretation("invalid interpretation structure")
        evidence = tuple(
            Evidence(str(e.get("crisis", "")), str(e.get("stance", "")),
                     str(e.get("fact", "")).strip(), answer_index)
            for e in evidence_raw if isinstance(e, dict)
        )
        next_question = raw.get("next_question")
        if next_question is not None:
            next_question = str(next_question).strip() or None
        return Interpretation(
            next_question=next_question,
            crisis_updates={str(k): str(v) for k, v in updates.items()},
            evidence=evidence,
            stop=bool(raw.get("stop", False)),
            main_question=str(raw.get("main_question", "")).strip(),
        )
