"""Repository-only provider/payment chaos recovery for W9."""
import hashlib,json,sqlite3
from pathlib import Path
FROZEN="eb934dbafad99831091d9170f46da78c33b25ab00011bf6fd49bab8210a0aff6"
class Mutation(ValueError): pass
class BlindRetry(RuntimeError): pass
def sha(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(",",":")).encode()).hexdigest()
class ChaosStore:
 def __init__(self,p:str|Path): self.db=sqlite3.connect(str(p),isolation_level=None); self.db.row_factory=sqlite3.Row; self.db.executescript("CREATE TABLE effect(ref TEXT PRIMARY KEY,kind TEXT,state TEXT,receipt TEXT);CREATE TABLE event(event_ref TEXT PRIMARY KEY,effect_ref TEXT,payload_sha TEXT);CREATE TABLE reconcile(ref TEXT PRIMARY KEY,receipt TEXT);")
 def close(self): self.db.close()
 def reserve(self,ref,kind):
  if kind not in {"webhook","payment_test"} or not ref.startswith("chaos_effect_"): raise ValueError("bounded effect")
  self.db.execute("INSERT INTO effect VALUES(?,?,'reserved',NULL)",(ref,kind))
 def ingest(self,event_ref,effect_ref,payload):
  if set(payload)!={"mode","status","receipt_ref"} or payload["mode"] not in {"SIMULATED","TEST"}: raise ValueError("exact chaos payload")
  d=sha(payload); old=self.db.execute("SELECT effect_ref,payload_sha FROM event WHERE event_ref=?",(event_ref,)).fetchone()
  if old:
   if tuple(old)!=(effect_ref,d): raise Mutation("mutated duplicate")
   return False
  state={"success":"settled","unknown":"unknown","failed":"reserved"}[payload["status"]]
  self.db.execute("UPDATE effect SET state=?,receipt=? WHERE ref=?",(state,payload["receipt_ref"],effect_ref)); self.db.execute("INSERT INTO event VALUES(?,?,?)",(event_ref,effect_ref,d)); return True
 def retry(self,ref):
  s=self.db.execute("SELECT state FROM effect WHERE ref=?",(ref,)).fetchone()[0]
  if s in {"settled","unknown","in_flight"}: raise BlindRetry(s)
 def reconcile(self,ref,receipt,effect_proven):
  if self.db.execute("SELECT state FROM effect WHERE ref=?",(ref,)).fetchone()[0]!="unknown": raise ValueError("unknown required")
  self.db.execute("UPDATE effect SET state=? WHERE ref=?",("settled" if effect_proven else "reserved",ref)); self.db.execute("INSERT INTO reconcile VALUES(?,?)",(ref,receipt))
 def deploy_parity(self,test_config,deploy_config): return sha(test_config)==sha(deploy_config)
