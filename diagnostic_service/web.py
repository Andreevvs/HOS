"""Minimal replaceable HTTP UI for the express diagnostic."""
from __future__ import annotations
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from knowledge_service import KnowledgeService
from .executor import ProtocolExecutor, SessionStatus
PROTOCOL_ID="Экспресс-тест уровня развития v0.1"
def create_executor(root):
    knowledge=KnowledgeService(root,root/"knowledge_service"/"registry.json");knowledge.build();return ProtocolExecutor(knowledge,PROTOCOL_ID)
PAGE="""<!doctype html><html lang=ru><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>HOS — экспресс-тест</title><style>body{font:16px system-ui;max-width:760px;margin:2rem auto;padding:0 1rem;background:#f5f7fb;color:#172033}#result,#chat{white-space:pre-wrap;background:white;border-radius:12px;padding:1.2rem;margin-top:1rem;box-shadow:0 2px 12px #ccd3df}#result:empty{display:none}form{display:flex;gap:.5rem;margin-top:1rem}input{flex:1;padding:.8rem}button{padding:.8rem 1.2rem}</style><h1>HOS — экспресс-тест</h1><div id=result></div><div id=chat>Нажмите «Начать тест».</div><form><input id=answer autocomplete=off placeholder='Ваш ответ'><button>Начать тест</button></form><script>let sid=null;const form=document.querySelector('form'),chat=document.querySelector('#chat'),result=document.querySelector('#result'),input=document.querySelector('#answer'),button=document.querySelector('button');form.onsubmit=async e=>{e.preventDefault();let url=sid?'/sessions/'+sid+'/messages':'/sessions',body=sid?{message:input.value}:{};let r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),d=await r.json();sid=d.session_id;if(d.result)result.textContent=d.result;chat.textContent=d.message;input.value='';button.textContent='Ответить';input.focus();};</script></html>"""
def handler_for(executor):
    class Handler(BaseHTTPRequestHandler):
        def _json(self,status,value):
            data=json.dumps(value,ensure_ascii=False).encode();self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8");self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
        def do_GET(self):
            if urlparse(self.path).path!="/":return self._json(404,{"error":"not found"})
            data=PAGE.encode();self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
        def do_POST(self):
            path=urlparse(self.path).path;length=int(self.headers.get("Content-Length",0))
            try:body=json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:return self._json(400,{"error":"invalid JSON"})
            if path=="/sessions":
                session,message=executor.start();return self._json(503 if session.error else 201,{"session_id":session.session_id,"message":message,"result":session.result_text})
            parts=path.strip("/").split("/")
            if len(parts)==3 and parts[0]=="sessions" and parts[2]=="messages":
                if parts[1] not in executor.sessions:return self._json(404,{"error":"unknown session"})
                session=executor.sessions[parts[1]];message=executor.respond(parts[1],str(body.get("message","")));result=session.result_text if session.status in (SessionStatus.AWAITING_FEEDBACK,SessionStatus.COMPLETED) else None;display=message
                if result and message==result:display=session.protocol.feedback_prompts[0]
                return self._json(200,{"session_id":parts[1],"message":display,"result":result})
            return self._json(404,{"error":"not found"})
        def log_message(self,format,*args):pass
    return Handler
def main():
    root=Path(__file__).resolve().parents[1];server=ThreadingHTTPServer(("0.0.0.0",8080),handler_for(create_executor(root)));print("HOS diagnostic UI: http://localhost:8080");server.serve_forever()
if __name__=="__main__":main()
