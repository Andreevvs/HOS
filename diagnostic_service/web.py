"""Minimal replaceable HTTP UI for HOS express and full diagnostics."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from knowledge_service import KnowledgeService
from full_iut import FullIUTInterview, FullIUTUnavailable, InvalidInterpretation
from full_iut.openai_interpreter import OpenAIInterpreter, InterpreterUnavailable
from .executor import ProtocolExecutor

EXPRESS_PROTOCOL_ID = "Экспресс-тест уровня развития v0.1"

STYLE = """body{font:16px system-ui;max-width:800px;margin:2rem auto;padding:0 1rem;background:#f5f7fb;color:#172033}a,button{font:inherit}.card,#chat{background:white;border-radius:12px;padding:1.2rem;box-shadow:0 2px 12px #ccd3df}.actions{display:flex;gap:1rem;flex-wrap:wrap}.actions a{display:inline-block;padding:1rem 1.3rem;background:#172033;color:white;text-decoration:none;border-radius:10px}#chat{white-space:pre-wrap;min-height:18rem}form{display:flex;gap:.5rem;margin-top:1rem}input,textarea{flex:1;padding:.8rem}textarea{min-height:7rem}button{padding:.8rem 1.2rem}.meta{font-size:.85rem;color:#596579;margin:.7rem 0}"""

HOME = f"""<!doctype html><html lang=ru><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>HOS — диагностика</title><style>{STYLE}</style><h1>HOS — диагностика развития</h1><div class=card><p>Выберите режим диагностики.</p><div class=actions><a href='/express'>Экспресс-тест</a><a href='/full'>Полный тест ИУТ</a></div></div></html>"""

EXPRESS_PAGE = f"""<!doctype html><html lang=ru><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>HOS — экспресс-тест</title><style>{STYLE}</style><p><a href='/'>← На главную</a></p><h1>HOS — экспресс-тест</h1><div id=chat>Нажмите «Начать тест».</div><form><input id=answer autocomplete=off placeholder='Ваш ответ'><button>Начать тест</button></form><script>let sid=null;const form=document.querySelector('form'),chat=document.querySelector('#chat'),input=document.querySelector('#answer'),button=document.querySelector('button');form.onsubmit=async e=>{{e.preventDefault();let url=sid?'/sessions/'+sid+'/messages':'/sessions';let body=sid?{{message:input.value}}:{{}};let r=await fetch(url,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}}),d=await r.json();sid=d.session_id;chat.textContent=d.message;input.value='';button.textContent='Ответить';input.focus();}};</script></html>"""

FULL_PAGE = f"""<!doctype html><html lang=ru><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>HOS — полный тест ИУТ</title><style>{STYLE}</style><p><a href='/'>← На главную</a></p><h1>HOS — полный тест ИУТ</h1><div id=meta class=meta></div><div id=chat>Нажмите «Начать интервью».</div><form><textarea id=answer placeholder='Расскажите своими словами' disabled></textarea><button>Начать интервью</button></form><script>let sid=null,q=null;const form=document.querySelector('form'),chat=document.querySelector('#chat'),input=document.querySelector('#answer'),button=document.querySelector('button'),meta=document.querySelector('#meta');function resultText(r){{return `Подтверждённый уровень: ${{r.confirmed_level}}\nДиапазон ИУТ/УРЛ: ${{r.iut_range}}\nСледующий кризис: ${{r.next_crisis}}\nСтатус перехода: ${{r.transition_status}}\nПоложение внутри диапазона: ${{r.within_range}}\n\nПодтверждающие свидетельства:\n- ${{r.supporting_evidence.join('\n- ')||'нет достаточных данных'}}\n\nПротиворечащие/неопределённые свидетельства:\n- ${{r.contradictory_or_uncertain_evidence.join('\n- ')||'нет'}}\n\nГлавный вопрос дальнейшей проверки: ${{r.main_question||'не определён'}}`;}}form.onsubmit=async e=>{{e.preventDefault();button.disabled=true;try{{let url=sid?'/api/full/sessions/'+sid+'/answers':'/api/full/sessions';let body=sid?{{answer:input.value}}:{{}};let r=await fetch(url,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}}),d=await r.json();if(!r.ok){{chat.textContent=d.error||'Диагностика недоступна';return;}}sid=d.session_id||sid;if(d.provenance)meta.textContent=`Методика: ${{d.provenance.version}} · SHA ${{d.provenance.source_sha.slice(0,12)}}…`;if(d.status==='completed'){{chat.textContent=resultText(d.result);input.disabled=true;button.style.display='none';return;}}q=d.question;chat.textContent=q;input.value='';input.disabled=false;button.textContent='Ответить';input.focus();}}finally{{button.disabled=false;}}}};</script></html>"""


def create_services(root: Path):
    knowledge = KnowledgeService(root, root / "knowledge_service" / "registry.json")
    knowledge.build()
    express = ProtocolExecutor(knowledge, EXPRESS_PROTOCOL_ID)
    try:
        full = FullIUTInterview(knowledge, OpenAIInterpreter())
        full_error = None
    except InterpreterUnavailable as exc:
        full, full_error = None, str(exc)
    return express, full, full_error


def handler_for(executor: ProtocolExecutor, full: FullIUTInterview | None = None,
                full_error: str | None = None):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, value: dict):
            data=json.dumps(value,ensure_ascii=False).encode();self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8");self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
        def _html(self, value: str):
            data=value.encode();self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
        def do_GET(self):
            path=urlparse(self.path).path
            if path=="/": return self._html(HOME)
            if path=="/express": return self._html(EXPRESS_PAGE)
            if path=="/full": return self._html(FULL_PAGE)
            return self._json(404,{"error":"not found"})
        def do_POST(self):
            path=urlparse(self.path).path;length=int(self.headers.get("Content-Length",0))
            try: body=json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError: return self._json(400,{"error":"invalid JSON"})
            if path=="/sessions":
                session,message=executor.start();return self._json(503 if session.error else 201,{"session_id":session.session_id,"message":message})
            parts=path.strip("/").split("/")
            if len(parts)==3 and parts[0]=="sessions" and parts[2]=="messages":
                if parts[1] not in executor.sessions:return self._json(404,{"error":"unknown session"})
                return self._json(200,{"session_id":parts[1],"message":executor.respond(parts[1],str(body.get("message","")))})
            if path=="/api/full/sessions":
                if full is None:return self._json(503,{"error":"Полная диагностика не настроена: "+(full_error or "interpreter unavailable")})
                try: session,question=full.start()
                except FullIUTUnavailable as exc:return self._json(503,{"error":str(exc)})
                return self._json(201,{"session_id":session.session_id,"status":"in_progress","question":question,"provenance":{"source_path":session.source_path,"source_sha":session.source_sha,"version":session.version}})
            if len(parts)==5 and parts[:3]==["api","full","sessions"] and parts[4]=="answers":
                if full is None:return self._json(503,{"error":"Полная диагностика не настроена"})
                sid=parts[3]
                if sid not in full.sessions:return self._json(404,{"error":"unknown session"})
                try: result=full.answer(sid,str(body.get("answer","")))
                except (InvalidInterpretation, InterpreterUnavailable) as exc:return self._json(502,{"error":"Ошибка интерпретирующего слоя: "+str(exc)})
                result["session_id"]=sid
                return self._json(200,result)
            return self._json(404,{"error":"not found"})
        def log_message(self, format, *args): pass
    return Handler


def main():
    root=Path(__file__).resolve().parents[1]
    express,full,error=create_services(root)
    server=ThreadingHTTPServer(("0.0.0.0",8080),handler_for(express,full,error))
    print("HOS diagnostic UI: http://localhost:8080")
    server.serve_forever()

if __name__=="__main__":main()
