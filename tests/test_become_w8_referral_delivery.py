import json,sys,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from become_w8.referral_delivery import BlindRetryForbidden,ReferralDeliveryStore,ReplayMutation
ROOT=Path(__file__).resolve().parents[1]; FIX=ROOT/"contracts/proof-loop/v8/fixtures/gate-referral-w8.json"; T0=datetime(2026,8,1,7,tzinfo=timezone.utc)
class TestReferralDelivery(unittest.TestCase):
 def setUp(self): self.t=tempfile.TemporaryDirectory(); self.s=ReferralDeliveryStore(Path(self.t.name)/"d.sqlite"); self.f=json.loads(FIX.read_text())
 def tearDown(self): self.s.close(); self.t.cleanup()
 def test_matrix_and_redaction(self):
  for i,status in enumerate(("accepted","delivered","failed","unknown")):
   e={**self.f["delivery"],"delivery_ref":f"referral_delivery_{i}_fixture","idempotency_key":f"idempotency_{i}_fixture"}; self.s.reserve(e,T0); p=self.s.observe(f"provider_event_{i}_fixture",e["delivery_ref"],status,"synthetic_provider_fixture",T0); self.assertNotIn("provider_id",p)
 def test_duplicate_and_mutation(self):
  c=self.s.reserve(self.f["delivery"],T0); self.assertFalse(self.s.reserve(self.f["delivery"],T0).created); self.s.observe("provider_event_fixture",c.delivery_ref,"accepted","synthetic_provider_fixture",T0)
  with self.assertRaises(ReplayMutation): self.s.observe("provider_event_fixture",c.delivery_ref,"delivered","synthetic_provider_fixture",T0)
 def test_unknown_no_retry_reconcile(self):
  c=self.s.reserve(self.f["delivery"],T0); self.s.observe("provider_event_fixture",c.delivery_ref,"unknown","synthetic_provider_fixture",T0)
  with self.assertRaises(BlindRetryForbidden): self.s.retry(c.delivery_ref)
  self.s.reconcile(c.delivery_ref,"reconciliation_receipt_fixture",True,T0)
 def test_raw_provider_or_media_rejected(self):
  with self.assertRaises(ValueError): self.s.reserve({**self.f["delivery"],"provider_id":"raw"},T0)
  with self.assertRaises(ValueError): self.s.reserve({**self.f["delivery"],"media_content":"raw"},T0)
if __name__=="__main__": unittest.main()
