import json,threading,unittest,urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from diagnostic_service.executor import ProtocolExecutor,SessionStatus
from diagnostic_service.web import PROTOCOL_ID,handler_for
from knowledge_service import KnowledgeService
ROOT=Path(__file__).resolve().parents[1]
def make_executor():
    service=KnowledgeService(ROOT,ROOT/"knowledge_service"/"registry.json");service.build();return ProtocolExecutor(service,PROTOCOL_ID)
def finish(executor,session,values):
    result=""
    for value in values:result=executor.respond(session.session_id,str(value))
    return result
class ExpressDiagnosticE2E(unittest.TestCase):
    def test_exact_start_and_sequence(self):
        e=make_executor();s,m=e.start();self.assertEqual(s.status,SessionStatus.IN_PROGRESS);self.assertIn("## Вопрос 1",m);self.assertIn("## Вопрос 2",e.respond(s.session_id,"5"))
    def test_l5_is_preliminary_and_l6_is_not_diagnosed(self):
        e=make_executor();s,_=e.start();r=finish(e,s,[10]*12);self.assertIn("L5 — Интегрированная личность",r);self.assertIn("60–80",r);self.assertIn("L6 этим тестом не диагностируется",r);self.assertNotIn("Наиболее вероятная зона: L6",r)
    def test_context_does_not_demote_l5(self):
        e=make_executor();s,_=e.start();r=finish(e,s,[2,6,9,5,9,9,9,2,9,2,5,4]);self.assertIn("L5 — Интегрированная личность",r)
    def test_low_q6_q7_gate_higher_levels(self):
        e=make_executor();s,_=e.start();r=finish(e,s,[9,9,9,9,10,2,2,9,10,9,9,9]);self.assertIn("L2 или ниже",r);self.assertNotIn("L4 —",r);self.assertNotIn("L5 —",r)
    def test_result_is_stored_and_contains_no_feedback_prompt(self):
        e=make_executor();s,_=e.start();r=finish(e,s,[5]*12)
        self.assertEqual(s.result_text,r);self.assertNotIn(s.protocol.feedback_prompts[0],r)
        for i,answer in enumerate(("8","Точно","Ничего")):
            message=e.respond(s.session_id,answer);self.assertEqual(s.result_text,r)
            if i<2:self.assertEqual(message,s.protocol.feedback_prompts[i+1])
        self.assertEqual(s.status,SessionStatus.COMPLETED)
    def test_web_keeps_result_separate_across_all_feedback_turns(self):
        e=make_executor();server=ThreadingHTTPServer(("127.0.0.1",0),handler_for(e));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def post(url,body):
            req=urllib.request.Request(url,data=json.dumps(body).encode(),headers={"Content-Type":"application/json"},method="POST");return json.loads(urllib.request.urlopen(req).read())
        try:
            base=f"http://127.0.0.1:{server.server_port}";started=post(base+"/sessions",{});sid=started["session_id"]
            for _ in range(12):last=post(base+f"/sessions/{sid}/messages",{"message":"5"})
            saved=last["result"];session=e.sessions[sid]
            self.assertIn("Предварительная оценка развития HOS",saved);self.assertNotIn(session.protocol.feedback_prompts[0],saved);self.assertEqual(last["message"],session.protocol.feedback_prompts[0])
            for i,answer in enumerate(("8","Точно","Ничего")):
                response=post(base+f"/sessions/{sid}/messages",{"message":answer});self.assertEqual(response["result"],saved)
                if i<2:self.assertEqual(response["message"],session.protocol.feedback_prompts[i+1])
            self.assertEqual(session.status,SessionStatus.COMPLETED)
        finally:server.shutdown();server.server_close();thread.join()
if __name__=="__main__":unittest.main()
