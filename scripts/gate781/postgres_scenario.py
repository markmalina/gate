"""Real, LOCAL CI PostgreSQL rehearsal; never accepts a Railway address."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import runtime


def main():
    phase = sys.argv[1]
    if os.environ.get("GITHUB_ACTIONS") != "true" or phase not in {"seed-stop", "verify"}:
        raise ValueError("CI_ONLY")
    # Fixed localhost and explicit synthetic databases/credentials.
    db = "gate781" if phase == "seed-stop" else "gate781_restore"
    url = "postgresql://postgres:postgres@localhost:5432/" + db
    if phase == "seed-stop":
        assert asyncio.run(runtime.seed(url))
        assert not asyncio.run(runtime.seed(url))  # no re-seeding
        children = runtime.supervise(
            lambda fd, i: asyncio.run(runtime.hold_transaction(fd, i, url)))
        # Both child transactions have modified actual rows but NOT committed.
        assert runtime.reap(children) == 2
        for pid in children:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            raise AssertionError("child survived waitpid barrier")
    who = {"nonce": "00000000-0000-0000-0000-000000000781",
           "sourceSha": os.environ["GITHUB_SHA"],
           "deploymentId": "00000000-0000-0000-0000-000000000782"}
    os.environ["GATE781_PG_SERVICE_ID"] = "00000000-0000-0000-0000-000000000783"
    proof = asyncio.run(runtime.proof(who, "BASELINE" if phase == "seed-stop" else "RESTORED", url))
    output = {"mode": "LOCAL_POSTGRES_SYNTHETIC", "railwayCalls": 0,
              "restoreKind": "PG_DUMP_LOGICAL_NOT_RAILWAY_VOLUME", "proof": proof}
    Path("gate781-" + phase + ".json").write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
