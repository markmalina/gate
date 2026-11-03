"""Real capped local containers; synthetic Railway-shaped IDs, zero Railway calls."""

import base64
import json
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, timeout=30).strip()


def synthetic(number):
    return "00000000-0000-0000-0000-" + str(number).zfill(12)


def records(name):
    result = []
    for line in docker("logs", name).splitlines():
        if line.startswith("gate781:"):
            result.append(json.loads(base64.b64decode(line[8:])))
    return result


def main():
    assert os.environ["GITHUB_ACTIONS"] == "true"
    network = "gate781-offline"
    containers = ["gate781-pg", "gate781-writer1", "gate781-writer2", "gate781-wrong-db"]
    common = {
        "GATE781_DISPOSABLE": "synthetic-data-only",
        "GATE781_NONCE": synthetic(781),
        "GATE781_SOURCE_SHA": os.environ["GITHUB_SHA"],
        "GATE781_EXPIRES_AT": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "RAILWAY_PROJECT_ID": synthetic(1),
        "RAILWAY_ENVIRONMENT_ID": synthetic(2),
    }

    def start(name, role, service, replica, extra):
        env = {
            **common,
            "RAILWAY_SERVICE_ID": synthetic(service),
            "RAILWAY_DEPLOYMENT_ID": synthetic(service + 10),
            "RAILWAY_REPLICA_ID": synthetic(replica),
            **extra,
        }
        args = ["run", "-d", "--name", name, "--network", network,
                "--cpus", "1", "--memory", "512m"]
        if role == "pg":
            args += ["--network-alias", "gate781-pg.railway.internal"]
        for key, value in env.items():
            args += ["-e", key + "=" + value]
        docker(*args, "gate781-" + role + ":ci")

    try:
        docker("network", "create", network)
        start("gate781-pg", "pg", 4, 40,
              {"POSTGRES_USER": "gate781", "POSTGRES_PASSWORD": "synthetic-ci-only",
               "POSTGRES_DB": "gate781"})
        for _ in range(40):
            ready = subprocess.run(
                ["docker", "exec", "gate781-pg", "pg_isready", "-U", "gate781"],
                capture_output=True, timeout=5,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise AssertionError("local PG did not start")
        database = {
            "DATABASE_URL": "postgresql://gate781:synthetic-ci-only@"
                            "gate781-pg.railway.internal:5432/gate781",
            "GATE781_PG_HOST": "gate781-pg.railway.internal",
            "GATE781_PG_SERVICE_ID": synthetic(4),
        }
        # A wrong target is rejected BEFORE the first table creation/seed.
        start("gate781-wrong-db", "writer", 3, 39,
              {**database, "GATE781_PG_SERVICE_ID": synthetic(999)})
        assert docker("wait", "gate781-wrong-db") == "1"
        assert docker("exec", "gate781-pg", "psql", "-U", "gate781", "-Atc",
                      "SELECT to_regclass('public.watch_voice_notes') IS NULL") == "t"
        for n in (1, 2):
            start("gate781-writer" + str(n), "writer", 3, 30 + n, database)
        for _ in range(60):
            if all(records(name) for name in containers[1:3]):
                break
            time.sleep(0.5)
        else:
            raise AssertionError("writer readiness missing")
        evidence = []
        for name in containers[1:3]:
            docker("stop", "--time", "15", name)
            assert docker("inspect", "--format", "{{.State.ExitCode}}", name) == "0"
            proof = records(name)
            assert [p["phase"] for p in proof] == ["START", "TREE_REAPED"]
            assert proof[-1]["childrenReaped"] == 2
            assert all(p["memoryBytes"] <= 536870912 and p["cpu"] <= 1 for p in proof)
            evidence += proof
        docker("stop", "--time", "15", "gate781-pg")
        assert docker("inspect", "--format", "{{.State.ExitCode}}", "gate781-pg") == "0"
        Path("gate781-containers.json").write_text(json.dumps({
            "mode": "LOCAL_DOCKER_SYNTHETIC", "railwayCalls": 0,
            "wrongDatabaseBeforeSeed": "REFUSED", "writers": evidence,
            "postgresResources": records("gate781-pg"),
        }, indent=2) + "\n")
    finally:
        for name in containers:
            subprocess.run(["docker", "rm", "-fv", name], capture_output=True, timeout=30)
        subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=30)


if __name__ == "__main__":
    main()
