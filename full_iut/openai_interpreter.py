"""Replaceable OpenAI Responses API interpreter for full IUT; not HOS methodology."""
from __future__ import annotations
import json,os
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen
from .interview import Evidence,Interpretation,InvalidInterpretation
class InterpreterUnavailable(RuntimeError):pass
class OpenAIInterpreter:
    endpoint="https://api.openai.com/v1/responses"
    def __init__(self,api_key:str|None=None,model:str|None=None):
        self.api_key=api_key or os.environ.get("OPENAI_API_KEY","");self.model=model or os.environ.get("HOS_INTERPRETER_MODEL","gpt-5.6")
        if not self.api_key:raise InterpreterUnavailable("OPENAI_API_KEY is not configured")
    def interpret(self,*,canonical_text:str,history:tuple[tuple[str,str],...],crisis_states:dict[str,str],allowed_areas:tuple[str,...])->Interpretation:
        payload={"model":self.model,"input":self._prompt(canonical_text,history,crisis_states,allowed_areas)}
        req=Request(self.endpoint,data=json.dumps(payload,ensure_ascii=False).encode(),headers={"Authorization":f"Bearer {self.api_key}","Content-Type":"application/json"},method="POST")
        try:
            with urlopen(req,timeout=90) as r:body=json.loads(r.read().decode())
        except (HTTPError,URLError,TimeoutError,json.JSONDecodeError) as exc:raise InterpreterUnavailable(f"LLM interpreter request failed: {exc}") from exc
        return self._decode(self._output_text(body),len(history)-1)
    @staticmethod
    def _prompt(canonical_text,history,crisis_states,allowed_areas):
        transcript="\n\n".join(f"Вопрос {i+1}: {q}\nОтвет {i+1}: {a}" for i,(q,a) in enumerate(history))
        return f'''Ты — интерпретирующий слой полной диагностики ИУТ HOS. Единственный источник методики — КАНОНИЧЕСКИЙ SNAPSHOT. Не добавляй сущности, критерии, пороги или формулы. Не сообщай промежуточный уровень/диапазон. Один ответ не является доказательством кризиса. Кр7 не диагностируется.

Оркестратор разрешает сейчас ТОЛЬКО диагностические области: {json.dumps(allowed_areas,ensure_ascii=False)}. Не выбирай область вне списка.
Следующий вопрос имеет ровно один тип:
1) bank — дословный вопрос из §7 банка v0.3. Тогда обязательно укажи его номер question_number и diagnostic_area; next_question должен дословно совпадать с каноном.
2) followup — уточнение к уже полученному ответу только по цепочке факт → контекст → мотив → длительность → последствия → цена выбора → проверка реальностью. Для followup question_number=null. Не маскируй новый произвольный диагностический вопрос под followup.
Области банка: 1–6 entrance; 7–30 Кр3↔Кр4; 31–38 Кр4↔Кр5; 39–46 Кр5↔Кр6; 47–50 activity.
stop=true только когда новые вопросы дают существенно меньше информации.

Верни ТОЛЬКО JSON:
{{"next_question":string|null,"question_kind":"bank"|"followup"|null,"question_number":integer|null,"diagnostic_area":"entrance"|"Кр3↔Кр4"|"Кр4↔Кр5"|"Кр5↔Кр6"|"activity"|null,"crisis_updates":{{}},"evidence":[{{"crisis":"Кр1".."Кр6","stance":"supporting"|"contradictory"|"uncertain","fact":"конкретный факт"}}],"stop":boolean,"main_question":string}}
При stop=true поля следующего вопроса должны быть null.

КАНОНИЧЕСКИЙ SNAPSHOT:\n---\n{canonical_text}\n---
СОСТОЯНИЯ КРИЗИСОВ: {json.dumps(crisis_states,ensure_ascii=False)}
ИСТОРИЯ:\n{transcript}'''
    @staticmethod
    def _output_text(body):
        if isinstance(body.get("output_text"),str):return body["output_text"]
        parts=[c.get("text") for i in body.get("output",[]) for c in i.get("content",[]) if isinstance(c.get("text"),str)]
        if not parts:raise InvalidInterpretation("LLM response contains no text output")
        return "\n".join(parts)
    @staticmethod
    def _decode(text,answer_index):
        try:raw=json.loads(text.strip())
        except json.JSONDecodeError as exc:raise InvalidInterpretation("LLM response is not valid JSON") from exc
        if not isinstance(raw,dict):raise InvalidInterpretation("LLM interpretation must be a JSON object")
        updates=raw.get("crisis_updates",{});eraw=raw.get("evidence",[])
        if not isinstance(updates,dict) or not isinstance(eraw,list):raise InvalidInterpretation("invalid interpretation structure")
        evidence=tuple(Evidence(str(e.get("crisis","")),str(e.get("stance","")),str(e.get("fact","")).strip(),answer_index) for e in eraw if isinstance(e,dict))
        q=raw.get("next_question");q=str(q).strip() or None if q is not None else None
        n=raw.get("question_number")
        if n is not None and (isinstance(n,bool) or not isinstance(n,int)):raise InvalidInterpretation("question_number must be integer or null")
        return Interpretation(next_question=q,question_kind=raw.get("question_kind"),question_number=n,diagnostic_area=raw.get("diagnostic_area"),crisis_updates={str(k):str(v) for k,v in updates.items()},evidence=evidence,stop=bool(raw.get("stop",False)),main_question=str(raw.get("main_question","")).strip())
