import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from become_w9.chaos_recovery import BlindRetry,ChaosStore,Mutation
class TestChaos(unittest.TestCase):
 def setUp(self): self.t=tempfile.TemporaryDirectory(); self.s=ChaosStore(Path(self.t.name)/"c.sqlite"); self.s.reserve("chaos_effect_fixture","webhook")
 def tearDown(self): self.s.close(); self.t.cleanup()
 def test_duplicate_webhook(self):
  p={"mode":"SIMULATED","status":"success","receipt_ref":"mock_receipt"}; self.assertTrue(self.s.ingest("event_fixture","chaos_effect_fixture",p)); self.assertFalse(self.s.ingest("event_fixture","chaos_effect_fixture",p))
  with self.assertRaises(Mutation): self.s.ingest("event_fixture","chaos_effect_fixture",{**p,"status":"failed"})
 def test_unknown_no_blind_retry(self):
  p={"mode":"SIMULATED","status":"unknown","receipt_ref":"mock_unknown"}; self.s.ingest("event_fixture","chaos_effect_fixture",p)
  with self.assertRaises(BlindRetry): self.s.retry("chaos_effect_fixture")
  self.s.reconcile("chaos_effect_fixture","reconciliation_fixture",False)
 def test_payment_test_recovery(self):
  self.s.reserve("chaos_effect_payment_fixture","payment_test"); self.s.ingest("event_payment_fixture","chaos_effect_payment_fixture",{"mode":"TEST","status":"unknown","receipt_ref":"test_lost"}); self.s.reconcile("chaos_effect_payment_fixture","reconciliation_payment_fixture",True)
 def test_deploy_parity_evidence(self):
  c={"mode":"SIMULATED","retry":"reconcile_only","digest":"fixed"}; self.assertTrue(self.s.deploy_parity(c,dict(c))); self.assertFalse(self.s.deploy_parity(c,{**c,"retry":"blind"}))
if __name__=="__main__": unittest.main()
