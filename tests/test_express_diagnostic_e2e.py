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
    def test_result_is_stored_through_feedback(self):
        e=make_executor();s,_=e.start();r=finish(e,s,[5]*12);self.assertEqual(s.result_text,r);e.respond(s.session_id,"8");self.assertEqual(s.result_text,r);self.assertEqual(s.status,SessionStatus.AWAITING_FEEDBACK)
    def test_web_keeps_result_separate_from_feedback_prompt(self):
        e=make_executor();server=ThreadingHTTPServer(("127.0.0.1",0),handler_for(e));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base=f"http://127.0.0.1:{server.server_port}";req=urllib.request.Request(base+"/sessions",data=b"{}",headers={"Content-Type":"application/json"},method="POST");started=json.loads(urllib.request.urlopen(req).read());sid=started["session_id"]
            for _ in range(12):
                payload=json.dumps({"message":"5"}).encode();req=urllib.request.Request(base+f"/sessions/{sid}/messages",data=payload,headers={"Content-Type":"application/json"},method="POST");last=json.loads(urllib.request.urlopen(req).read())
            self.assertIn("Предварительная оценка развития HOS",last["result"]);self.assertNotEqual(last["message"],last["result"]);saved=last["result"]
            payload=json.dumps({"message":"8"}).encode();req=urllib.request.Request(base+f"/sessions/{sid}/messages",data=payload,headers={"Content-Type":"application/json"},method="POST");feedback=json.loads(urllib.request.urlopen(req).read());self.assertEqual(saved,feedback["result"]);self.assertNotEqual(saved,feedback["message"])
        finally:server.shutdown();server.server_close();thread.join()
if __name__=="__main__":unittest.main()
