"""SIMULATED referral-link delivery with redacted status projection."""
import hashlib, json, sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

FROZEN_DIGEST="099f8b5bca93a3558ce9fa9b1ec4019304b126810c06b65384e3e71cd09c24e5"
STATUSES={"accepted","delivered","failed","unknown"}
class ReplayMutation(ValueError): pass
class BlindRetryForbidden(RuntimeError): pass
def _sha(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(",",":")).encode()).hexdigest()
def _iso(v):
    if v.tzinfo is None: raise ValueError("timezone-aware timestamp required")
    return v.astimezone(timezone.utc).isoformat().replace("+00:00","Z")
def _ref(v,p):
    if not isinstance(v,str) or not v.startswith(p) or len(v)>180: raise ValueError(f"opaque {p} ref required")
@dataclass(frozen=True)
class Claim: delivery_ref:str; provider_state:str; created:bool
class ReferralDeliveryStore:
    def __init__(self,database:str|Path):
        self.db=sqlite3.connect(str(database),isolation_level=None); self.db.row_factory=sqlite3.Row
        self.db.executescript("""
        CREATE TABLE delivery(delivery_ref TEXT PRIMARY KEY,idempotency_key TEXT UNIQUE,referral_edge_ref TEXT,candidate_ref TEXT,signed_link_sha256 TEXT,provider_state TEXT CHECK(provider_state IN ('reserved','in_flight','settled','unknown')),status TEXT,reconcile INTEGER DEFAULT 0,created_at TEXT);
        CREATE TABLE event(event_ref TEXT PRIMARY KEY,delivery_ref TEXT,status TEXT,receipt_sha256 TEXT,created_at TEXT);
        CREATE TABLE reconciliation(delivery_ref TEXT PRIMARY KEY,status TEXT,receipt_ref TEXT,updated_at TEXT);
        """)
    def close(self): self.db.close()
    def reserve(self,e,at):
        req={"delivery_ref","idempotency_key","referral_edge_ref","candidate_ref","signed_link_sha256","mode"}
        if set(e)!=req or e["mode"]!="SIMULATED" or len(e["signed_link_sha256"])!=64: raise ValueError("exact redacted SIMULATED envelope required")
        for k,p in (("delivery_ref","referral_delivery_"),("idempotency_key","idempotency_"),("referral_edge_ref","referral_edge_"),("candidate_ref","candidate_")): _ref(e[k],p)
        old=self.db.execute("SELECT * FROM delivery WHERE idempotency_key=?",(e["idempotency_key"],)).fetchone()
        expected=(e["delivery_ref"],e["referral_edge_ref"],e["candidate_ref"],e["signed_link_sha256"])
        if old:
            if (old["delivery_ref"],old["referral_edge_ref"],old["candidate_ref"],old["signed_link_sha256"])!=expected: raise ReplayMutation("mutated delivery")
            return Claim(old["delivery_ref"],old["provider_state"],False)
        self.db.execute("INSERT INTO delivery VALUES(?,?,?,?,?,'reserved','queued',0,?)",(e["delivery_ref"],e["idempotency_key"],e["referral_edge_ref"],e["candidate_ref"],e["signed_link_sha256"],_iso(at)))
        return Claim(e["delivery_ref"],"reserved",True)
    def observe(self,event_ref,delivery_ref,status,synthetic_provider_ref,at):
        _ref(event_ref,"provider_event_"); _ref(synthetic_provider_ref,"synthetic_provider_")
        if status not in STATUSES: raise ValueError("bounded status required")
        digest=_sha({"event_ref":event_ref,"status":status,"synthetic_provider_ref":synthetic_provider_ref})
        old=self.db.execute("SELECT delivery_ref,status,receipt_sha256 FROM event WHERE event_ref=?",(event_ref,)).fetchone()
        if old:
            if tuple(old)!=(delivery_ref,status,digest): raise ReplayMutation("mutated provider event")
            return self.projection(delivery_ref)
        state={"accepted":"in_flight","delivered":"settled","failed":"reserved","unknown":"unknown"}[status]
        changed=self.db.execute("UPDATE delivery SET provider_state=?,status=?,reconcile=? WHERE delivery_ref=?",(state,status,int(status=="unknown"),delivery_ref)).rowcount
        if changed!=1: raise KeyError(delivery_ref)
        self.db.execute("INSERT INTO event VALUES(?,?,?,?,?)",(event_ref,delivery_ref,status,digest,_iso(at)))
        if status=="unknown": self.db.execute("INSERT OR REPLACE INTO reconciliation VALUES(?,'required',NULL,?)",(delivery_ref,_iso(at)))
        return self.projection(delivery_ref)|{"provider_receipt_sha256":digest}
    def projection(self,delivery_ref):
        r=self.db.execute("SELECT delivery_ref,referral_edge_ref,candidate_ref,status,reconcile FROM delivery WHERE delivery_ref=?",(delivery_ref,)).fetchone()
        if not r: raise KeyError(delivery_ref)
        return {"schema":"gate.referral-acceptance@1","delivery_ref":r["delivery_ref"],"referral_edge_ref":r["referral_edge_ref"],"candidate_ref":r["candidate_ref"],"status":r["status"],"reconciliation_required":bool(r["reconcile"]),"frozen_digest":FROZEN_DIGEST}
    def retry(self,delivery_ref):
        s=self.db.execute("SELECT provider_state FROM delivery WHERE delivery_ref=?",(delivery_ref,)).fetchone()
        if not s: raise KeyError(delivery_ref)
        if s[0] in {"in_flight","settled","unknown"}: raise BlindRetryForbidden(s[0])
    def reconcile(self,delivery_ref,receipt_ref,effect_proven,at):
        _ref(receipt_ref,"reconciliation_receipt_")
        if self.db.execute("SELECT provider_state FROM delivery WHERE delivery_ref=?",(delivery_ref,)).fetchone()[0]!="unknown": raise ValueError("unknown required")
        state,status=("settled","delivered") if effect_proven else ("reserved","failed")
        self.db.execute("UPDATE delivery SET provider_state=?,status=?,reconcile=0 WHERE delivery_ref=?",(state,status,delivery_ref)); self.db.execute("UPDATE reconciliation SET status='resolved',receipt_ref=?,updated_at=? WHERE delivery_ref=?",(receipt_ref,_iso(at),delivery_ref))
