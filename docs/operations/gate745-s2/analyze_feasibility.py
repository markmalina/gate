"""Offline S2 evidence audit and conditional cost arithmetic; no network or effects."""

import argparse
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--sources-dir", type=Path, default=ROOT,
                    help="Scratch directory containing canonical copies at source-manifest paths")
args = parser.parse_args()
results = []
for item in json.loads((ROOT / "sources.json").read_text()):
    raw = (args.sources_dir / item["local_path"]).read_bytes()
    actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if actual != item["blob"]:
        raise SystemExit(f"Canonical blob mismatch: {item['local_path']}")
    results.append({"path": item["local_path"], "blob": actual,
                    "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})

schema = json.loads((ROOT / "schema-excerpt.json").read_text())
types = {item["name"]: item for item in schema["types"]}


def fields(name, key="fields"):
    return {item["name"]: item for item in types[name][key]}


assert "sizeMB" not in fields("VolumeCreateInput", "inputFields")
assert "sizeMB" not in fields("VolumeUpdateInput", "inputFields")
assert types["ServiceInstanceLimit"]["kind"] == "SCALAR"
assert {"memoryGB", "vCPUs"} <= fields("ServiceInstanceLimitsUpdateInput", "inputFields").keys()
assert "deploymentStopped" in fields("Deployment")
assert "instances" in fields("Deployment")

def type_name(t):
    return t["name"] if t.get("name") else type_name(t["ofType"])

mutation = fields("Mutation")
assert type_name(mutation["volumeInstanceBackupCreate"]["type"]) == "WorkflowId"
assert type_name(mutation["volumeInstanceBackupDelete"]["type"]) == "WorkflowId"
assert type_name(mutation["volumeInstanceBackupLock"]["type"]) == "Boolean"
assert "baseConfigEtag" in {x["name"] for x in mutation["environmentApplyChangeSet"]["args"]}

gib_bytes = 2 ** 30
decimal_gb = 10 ** 9
cpu_minutes = D(2) * D(120)
ram_gb_minutes = D(2 * gib_bytes) / D(decimal_gb) * D(120)
compute = cpu_minutes * D("0.000463") + ram_gb_minutes * D("0.000231")
# Deliberately charge full capacity for one volume and two whole-volume copies.
# This is a conditional model, not proof that billing ends after 72 hours.
storage_gb_minutes = D(3 * gib_bytes) / D(decimal_gb) * D(72 * 60)
storage = storage_gb_minutes * D("0.000003472222222")
egress_example = D(1) * D("0.05")

out = {
    "kind": "OFFLINE_SOURCE_AND_ARITHMETIC_ONLY",
    "source_blob_matches": len(results),
    "schema_assertions": 10,
    "source_verification": results,
    "conditional_model": {
        "currency": "USD", "billing_gb_assumption": "decimal_1000000000_bytes",
        "whole_fleet_vcpu": 2, "whole_fleet_memory_mib": 2048,
        "compute_minutes": 120, "storage_gib_conservative": 3,
        "storage_minutes_assumed": 4320, "egress_gb_example_only": 1,
        "compute_usd": str(compute), "storage_usd": str(storage),
        "egress_usd": str(egress_example),
        "sum_before_github_subscription_tax_and_unknowns_usd": str(
            compute + storage + egress_example
        ),
        "invoice_cap_proven": False,
        "billing_stops_at_observer_expiry_proven": False,
    },
    "polling": {
        "five_second_ticks_in_72_hours": 72 * 60 * 60 // 5,
        "four_separate_reads_hourly_for_72_hours": 4 * 72,
        "proposed_v7_reads": 256,
        "current_native_wire_call_ceiling": 1800,
        "claim_that_either_budget_fits_full_trace": False,
        "candidate_allocation_wide_wire_budget": {
            "preparation": 128, "native": 1200, "early_cleanup": 160,
            "terminal_readonly": 288, "reserved_reconciliation": 24,
            "sum": 128 + 1200 + 160 + 288 + 24,
            "proposal_only": True,
        },
    },
    "infrastructure": {"provider_requests": 0, "database_connections": 0,
                       "resources_created": 0, "resources_deleted": 0,
                       "credentials_created_or_read": 0},
    "acceptance": "S2_FEASIBILITY_NOT_PROVEN",
}
assert sum(v for k, v in out["polling"]["candidate_allocation_wide_wire_budget"].items()
           if k not in {"sum", "proposal_only"}) == 1800
(ROOT / "analysis-results.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
print(
    json.dumps(
        {k: v for k, v in out.items() if k != "source_verification"},
        ensure_ascii=False,
        indent=2,
    )
)
