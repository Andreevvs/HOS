"""Deterministic orchestration boundary for the canonical full HOS IUT interview."""
from __future__ import annotations
import re, threading, uuid
from dataclasses import dataclass, field
from typing import Protocol

PROTOCOL_ID="ИУТ — измерение"
CRISES=tuple(f"Кр{i}" for i in range(1,7))
STATES={"unexamined","uncertain","supported","contradicted"}
AREAS={"entrance","Кр3↔Кр4","Кр4↔Кр5","Кр5↔Кр6","activity"}
STATE_RU={"unexamined":"не проверен","uncertain":"неопределённо","supported":"подтверждён","contradicted":"не подтверждён"}

class FullIUTUnavailable(RuntimeError):pass
class InvalidInterpretation(RuntimeError):pass
class ExactRetriever(Protocol):
    def exact(self,object_id:str,version:str|None=None)->dict:...
@dataclass(frozen=True)
class Evidence:
    crisis:str;stance:str;fact:str;answer_index:int
@dataclass(frozen=True)
class Interpretation:
    next_question:str|None
    question_kind:str|None=None # bank | followup
    question_number:int|None=None
    diagnostic_area:str|None=None
    crisis_updates:dict[str,str]=field(default_factory=dict)
    evidence:tuple[Evidence,...]=()
    stop:bool=False
    main_question:str=""
class Interpreter(Protocol):
    def interpret(self,*,canonical_text:str,history:tuple[tuple[str,str],...],crisis_states:dict[str,str],allowed_areas:tuple[str,...])->Interpretation:...
@dataclass
class FullSession:
    session_id:str;source_path:str;source_sha:str;version:str;canonical_text:str;pending_question:str
    pending_question_kind:str="bank";pending_question_number:int|None=1;current_diagnostic_area:str="entrance";next_direction:str="entrance";sufficiency_state:str="continue"
    history:list[tuple[str,str]]=field(default_factory=list);crisis_states:dict[str,str]=field(default_factory=lambda:{c:"unexamined" for c in CRISES});evidence:list[Evidence]=field(default_factory=list);completed:bool=False;main_question:str=""

def _question_bank(text:str)->dict[int,str]:
    bank=re.search(r"^## 7\. Банк вопросов v0\.3\s*$([\s\S]*?)(?=^## 8\.)",text,re.MULTILINE)
    if not bank:raise FullIUTUnavailable("canonical question bank v0.3 missing")
    items={int(n):q.strip() for n,q in re.findall(r"^(\d+)\.\s+(.+)$",bank.group(1),re.MULTILINE)}
    if set(items)!=set(range(1,51)):raise FullIUTUnavailable("canonical bank must contain questions 1-50")
    entrance=re.search(r"^### Входной скрининг\s*$([\s\S]*?)(?=^### )",bank.group(1),re.MULTILINE)
    if not entrance or not re.search(r"^1\.\s+",entrance.group(1),re.MULTILINE):raise FullIUTUnavailable("canonical entrance screening missing")
    return items

def _area_for_question(n:int)->str:
    if 1<=n<=6:return "entrance"
    if 7<=n<=30:return "Кр3↔Кр4"
    if 31<=n<=38:return "Кр4↔Кр5"
    if 39<=n<=46:return "Кр5↔Кр6"
    return "activity"

def _allowed_areas(states:dict[str,str])->tuple[str,...]:
    areas=["entrance","Кр3↔Кр4","activity"]
    if states["Кр4"]=="supported":areas.append("Кр4↔Кр5")
    if states["Кр5"]=="supported":areas.append("Кр5↔Кр6")
    return tuple(areas)

class FullIUTInterview:
    def __init__(self,knowledge:ExactRetriever,interpreter:Interpreter):
        self.knowledge,self.interpreter=knowledge,interpreter;self.sessions={};self._lock=threading.RLock()
    def start(self):
        obj=self.knowledge.exact(PROTOCOL_ID)
        if obj.get("status")!="ok" or any(not obj.get(k) for k in ("source_path","source_sha","text")):raise FullIUTUnavailable("exact canonical full-IUT protocol unavailable or provenance incomplete")
        version=re.search(r"^\*\*Версия:\*\*\s*([^\n]+)",obj["text"],re.MULTILINE)
        if not version:raise FullIUTUnavailable("canonical protocol version missing")
        questions=_question_bank(obj["text"]);question=questions[1]
        s=FullSession(str(uuid.uuid4()),obj["source_path"],obj["source_sha"],version.group(1).strip(),obj["text"],question)
        with self._lock:self.sessions[s.session_id]=s
        return s,question
    def answer(self,session_id:str,answer:str)->dict:
        with self._lock:
            s=self.sessions[session_id]
            if s.completed:return {"status":"completed","result":self.result(s)}
            answer=answer.strip()
            if not answer:return {"status":"in_progress","question":s.pending_question,"error":"Ответ не должен быть пустым."}
            s.history.append((s.pending_question,answer));allowed=_allowed_areas(s.crisis_states)
            d=self.interpreter.interpret(canonical_text=s.canonical_text,history=tuple(s.history),crisis_states=dict(s.crisis_states),allowed_areas=allowed)
            self._apply(s,d)
            if d.stop:
                s.completed=True;s.pending_question="";s.sufficiency_state="sufficient";return {"status":"completed","result":self.result(s)}
            self._validate_next_question(s,d)
            s.pending_question=d.next_question or "";s.pending_question_kind=d.question_kind or "";s.pending_question_number=d.question_number;s.current_diagnostic_area=d.diagnostic_area or s.current_diagnostic_area;s.next_direction=s.current_diagnostic_area;s.sufficiency_state="continue"
            return {"status":"in_progress","question":s.pending_question}
    def _validate_next_question(self,s:FullSession,d:Interpretation):
        if not d.next_question or d.question_kind not in {"bank","followup"} or d.diagnostic_area not in AREAS:raise InvalidInterpretation("next question requires kind and diagnostic area")
        allowed=_allowed_areas(s.crisis_states)
        if d.diagnostic_area not in allowed:raise InvalidInterpretation("adaptive route violation: diagnostic area is not yet allowed")
        if d.question_kind=="bank":
            if d.question_number is None:raise InvalidInterpretation("bank question requires canonical question number")
            bank=_question_bank(s.canonical_text);canonical=bank.get(d.question_number)
            if canonical is None or canonical!=d.next_question.strip():raise InvalidInterpretation("bank question must exactly match pinned canonical bank")
            if _area_for_question(d.question_number)!=d.diagnostic_area:raise InvalidInterpretation("bank question assigned to wrong diagnostic area")
        elif d.question_number is not None:raise InvalidInterpretation("follow-up must not claim canonical bank number")
    def _apply(self,s:FullSession,d:Interpretation):
        proposed=dict(s.crisis_states)
        for crisis,state in d.crisis_updates.items():
            if crisis not in CRISES or state not in STATES:raise InvalidInterpretation("unknown crisis/state; Кр7 is not operationalized")
            proposed[crisis]=state
        for n in range(1,7):
            if proposed[f"Кр{n}"]=="supported" and any(proposed[f"Кр{i}"]!="supported" for i in range(1,n)):raise InvalidInterpretation("sequence law violation")
        s.crisis_states=proposed
        for e in d.evidence:
            if e.crisis not in CRISES or e.stance not in {"supporting","contradictory","uncertain"} or not e.fact:raise InvalidInterpretation("invalid evidence")
            if e.answer_index<0 or e.answer_index>=len(s.history):raise InvalidInterpretation("evidence points outside interview history")
            s.evidence.append(e)
        s.main_question=d.main_question or s.main_question
    @staticmethod
    def result(s:FullSession)->dict:
        highest=0
        for i in range(1,7):
            if s.crisis_states[f"Кр{i}"]=="supported":highest=i
            else:break
        levels={0:("L0 — Беспомощность","0–5","Кр1 самообслуживания"),1:("L1 — Зависимость","5–15","Кр2 социализации"),2:("L2 — Подчинение","15–25","Кр3 самостоятельности"),3:("L3 — Необходимость","25–40","Кр4 интереса"),4:("L4 — Увлекающаяся личность","40–60","Кр5 интеграции"),5:("L5 — Интегрированная личность","60–80","Кр6 смысла"),6:("L6 — Реализация Смысла","80–95","Кр7 не операционализирован и не диагностируется")}
        level,rng,nxt=levels[highest];next_state=STATE_RU.get(s.crisis_states.get(f"Кр{highest+1}"),"не операционализировано")
        return {"confirmed_level":level,"iut_range":rng,"next_crisis":nxt,"transition_status":next_state,"within_range":"не операционализировано","supporting_evidence":[e.fact for e in s.evidence if e.stance=="supporting"],"contradictory_or_uncertain_evidence":[e.fact for e in s.evidence if e.stance!="supporting"],"main_question":s.main_question,"provenance":{"source_path":s.source_path,"source_sha":s.source_sha,"version":s.version}}
