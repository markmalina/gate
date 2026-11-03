"""Disposable-only process/DB witness. No provider credentials or external effects.

The writer exercises real Watch tables with fixed synthetic bytes and uncommitted
transactions. The verifier is read-only and recomputes an independent manifest.
"""

from __future__ import annotations

import asyncio
import base64
import ctypes
import hashlib
import json
import os
import re
import select
import signal
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

MAX_MEMORY = 512 * 1024 * 1024
MAX_CPU = 1.0
FIXTURE_WORKSPACE = "gate781-synthetic"
ROLES = {"writer", "verify", "pg"}
PROD = {"972b6ab7-988a-421c-abfa-425451ad714f", "a148b138-44b3-4349-9d4b-292880503055"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value).encode()).hexdigest()


def identity(env=None):
    env = os.environ if env is None else env
    keys = {
        "nonce": "GATE781_NONCE",
        "sourceSha": "GATE781_SOURCE_SHA",
        "projectId": "RAILWAY_PROJECT_ID",
        "environmentId": "RAILWAY_ENVIRONMENT_ID",
        "serviceId": "RAILWAY_SERVICE_ID",
        "deploymentId": "RAILWAY_DEPLOYMENT_ID",
        "instanceId": "RAILWAY_REPLICA_ID",
    }
    result = {k: env.get(v, "") for k, v in keys.items()}
    if env.get("GATE781_DISPOSABLE") != "synthetic-data-only":
        raise ValueError("E_781_DISPOSABLE")
    for key, value in result.items():
        if key == "sourceSha":
            if not re.fullmatch("[0-9a-f]{40}", value):
                raise ValueError("E_781_SOURCE")
        elif not re.fullmatch("[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", value) or value in PROD:
            raise ValueError("E_781_IDENTITY")
    if env.get("RAILWAY_TOKEN") or env.get("OPENAI_API_KEY") or env.get("TELEGRAM_BOT_TOKEN"):
        raise ValueError("E_781_CREDENTIAL_BOUNDARY")
    return result


def parse_caps(cpu, memory):
    values = cpu.strip().split()
    if len(values) != 2 or not all(re.fullmatch("[1-9][0-9]*", x) for x in values):
        raise ValueError("E_781_CPU_UNBOUNDED")
    quota, period = map(int, values)
    if not re.fullmatch("[1-9][0-9]*", memory.strip()):
        raise ValueError("E_781_MEMORY_UNBOUNDED")
    cap = int(memory.strip())
    if quota / period > MAX_CPU or cap > MAX_MEMORY:
        raise ValueError("E_781_RESOURCE_CAP")
    return {"cpu": quota / period, "memoryBytes": cap}


def caps():
    # cgroup v2 mounted by container runtime; no externally selectable path.
    if not Path("/sys/fs/cgroup/cgroup.controllers").is_file():
        raise ValueError("E_781_CGROUP_VERSION")
    return parse_caps(
        Path("/sys/fs/cgroup/cpu.max").read_text(), Path("/sys/fs/cgroup/memory.max").read_text()
    )


def emit(prefix, value):
    print(prefix + base64.b64encode(canonical(value).encode()).decode(), flush=True)


def witness(who, boot, phase, processes, reaped, resources):
    return {
        "schema": "gate781.process-witness/v1",
        **who,
        "bootId": boot,
        "phase": phase,
        "observedAt": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "processes": processes,
        "childrenReaped": reaped,
        **resources,
    }


def database_url():
    raw = os.environ.get("DATABASE_URL", "")
    parsed = urlparse(raw)
    expected = os.environ.get("GATE781_PG_HOST", "")
    if (
        parsed.scheme not in {"postgres", "postgresql", "postgresql+asyncpg"}
        or parsed.hostname != expected
        or not expected.endswith(".railway.internal")
        or parsed.port != 5432
        or parsed.path != "/gate781"
        or parsed.username != "gate781"
        or not parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("E_781_DATABASE_BINDING")
    return raw.replace("postgresql+asyncpg:", "postgresql:")


def expected_rows():
    # Explicit synthetic identifiers, not copied owner data. Hash the actual bytes.
    blob = b"GATE781 SYNTHETIC AUDIO ORIGINAL\x00\x01"
    sha = hashlib.sha256(blob).hexdigest()
    notes = [
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "gate781/" + str(n))),
            "sha256": sha,
            "data": blob.hex(),
            "transcription_state": state,
            "delivery_state": delivery,
            "provider_request_count": count,
            "revision": "synthetic-v1",
            "next_part": n,
        }
        for n, state, delivery, count in [
            (1, "processing", "pending", 1),
            (2, "transcribed", "sending", 1),
            (3, "unknown", "unknown", 1),
        ]
    ]
    journal = [
        {
            "note_id": notes[1]["id"],
            "part": 0,
            "writer": "gate781-synthetic",
            "body_sha256": hashlib.sha256(b"synthetic-text").hexdigest(),
            "issue_number": 781,
            "comment_id": 781001,
        }
    ]
    return {
        "notes": sorted(notes, key=lambda x: x["id"]),
        "cursor": "gate781-fixed-cursor",
        "journal": journal,
    }


def cluster_marker(nonce, service_id):
    return "gate781-" + hashlib.sha256((nonce + ":" + service_id).encode()).hexdigest()[:32]


async def connect(url=None):
    import asyncpg

    connection = await asyncpg.connect(url or database_url(), timeout=10, command_timeout=15)
    if url is None:
        expected = cluster_marker(os.environ["GATE781_NONCE"], os.environ["GATE781_PG_SERVICE_ID"])
        if await connection.fetchval("SHOW cluster_name") != expected:
            await connection.close()
            raise ValueError("E_781_DATABASE_IDENTITY")
    return connection


async def read_data(connection):
    # Explicit raw-column projection independent of seed ORM serializers.
    notes = await connection.fetch("""SELECT id,sha256,encode(data,'hex') AS data,
        transcription_state,delivery_state,provider_request_count,revision,next_part
        FROM watch_voice_notes ORDER BY id""")
    cursor = await connection.fetchval(
        "SELECT cursor FROM watch_voice_cursors "
        "WHERE workspace_key=$1 AND account_id='synthetic-account'",
        FIXTURE_WORKSPACE,
    )
    journal = await connection.fetch(
        "SELECT note_id,part,writer,body_sha256,issue_number,comment_id "
        "FROM watch_voice_text_receipts ORDER BY note_id,part"
    )
    return {
        "notes": [dict(x) for x in notes],
        "cursor": cursor,
        "journal": [dict(x) for x in journal],
    }


async def seed(url=None):
    """One creator using PostgreSQL advisory lock; second replica does not rewrite."""
    c = await connect(url)
    try:
        await c.execute("SELECT pg_advisory_lock(7810119)")
        present = await c.fetchval("SELECT to_regclass('public.watch_voice_notes') IS NOT NULL")
        if present:
            if await read_data(c) != expected_rows():
                raise ValueError("E_781_EXISTING_DATA")
            return False
        # Exact Watch model definitions at the pinned Gate source, not a lookalike schema.
        from app.models.watch_voice import WatchVoiceCursor, WatchVoiceNote, WatchVoiceTextReceipt
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(
            (url or database_url()).replace("postgresql:", "postgresql+asyncpg:")
        )
        tables = [
            WatchVoiceCursor.__table__,
            WatchVoiceNote.__table__,
            WatchVoiceTextReceipt.__table__,
        ]
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync: WatchVoiceNote.metadata.create_all(sync, tables=tables)
            )
        await engine.dispose()
        async with c.transaction():
            rows = expected_rows()
            await c.execute(
                "INSERT INTO watch_voice_cursors(workspace_key,account_id,cursor) "
                "VALUES($1,'synthetic-account',$2)",
                FIXTURE_WORKSPACE,
                rows["cursor"],
            )
            for n in rows["notes"]:
                await c.execute(
                    """INSERT INTO watch_voice_notes(id,workspace_key,account_id,file_id,
                    revision,source_path,filename,media_type,size_bytes,sha256,data,
                    dropbox_modified_at,received_at,transcription_state,delivery_state,
                    transcript_origin,next_part,provider_request_count)
                    VALUES($1,$2,'synthetic-account',$1,$3,'/synthetic','synthetic.wav',
                    'audio/wav',$4,$5,$6,now(),now(),$7,$8,'gate',$9,$10)""",
                    n["id"],
                    FIXTURE_WORKSPACE,
                    n["revision"],
                    len(bytes.fromhex(n["data"])),
                    n["sha256"],
                    bytes.fromhex(n["data"]),
                    n["transcription_state"],
                    n["delivery_state"],
                    n["next_part"],
                    n["provider_request_count"],
                )
            j = rows["journal"][0]
            await c.execute(
                """INSERT INTO watch_voice_text_receipts(note_id,part,writer,body_sha256,
                issue_number,issue_url,received_at,comment_id)
                VALUES($1,$2,$3,$4,$5,'https://example.invalid/synthetic',now(),$6)""",
                j["note_id"],
                j["part"],
                j["writer"],
                j["body_sha256"],
                j["issue_number"],
                j["comment_id"],
            )
        return True
    finally:
        await c.close()


async def proof(who, phase, url=None):
    c = await connect(url)
    try:
        async with c.transaction(readonly=True, isolation="repeatable_read"):
            observed = await read_data(c)
            if observed != expected_rows():
                raise ValueError("E_781_RESTORE_CONTENT")
            system_id = await c.fetchval("SELECT system_identifier::text FROM pg_control_system()")
        return {
            "schema": "gate781.data-proof/v1",
            "phase": phase,
            "nonce": who["nonce"],
            "sourceSha": who["sourceSha"],
            "deploymentId": who["deploymentId"],
            "postgresServiceId": os.environ["GATE781_PG_SERVICE_ID"],
            "databaseSystemId": system_id,
            "dataDigest": digest(observed),
            "uncommittedAbsent": True,
            "claimsQuarantined": True,
            "mutations": 0,
        }
    finally:
        await c.close()


async def hold_transaction(ready_fd, delay, url=None):
    c = await connect(url)
    transaction = c.transaction()
    await transaction.start()
    # An uncommitted overwrite of a real original, cursor and durable claim.
    await c.execute("SET LOCAL lock_timeout='1s'")
    note = expected_rows()["notes"][delay % 3]["id"]
    await c.execute(
        "UPDATE watch_voice_notes SET transcription_state='received',"
        "provider_request_count=0,data=$1 WHERE id=$2",
        b"UNCOMMITTED MUST DISAPPEAR",
        note,
    )
    os.write(ready_fd, b"R")
    os.close(ready_fd)
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    while not stopping:
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.2 if delay else 0.05)
    # Deliberately no commit: connection close rolls back the incomplete write.
    await c.close()


def supervise(child_function, count=2, timeout=5):
    """Real waitpid barrier. Neither heartbeat loss nor SIGTERM ACK completes it."""
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:  # CHILD_SUBREAPER
        raise ValueError("E_781_SUBREAPER")
    children, readers = [], []
    for index in range(count):
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            os.setsid()
            try:
                child_function(write_fd, index)
                os._exit(0)
            except BaseException:
                os._exit(71)
        os.close(write_fd)
        children.append(pid)
        readers.append(read_fd)
    for fd in readers:
        available, _, _ = select.select([fd], [], [], 15)
        if not available or os.read(fd, 1) != b"R":
            for pid in children:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for pid in children:
                os.waitpid(pid, 0)
            for reader in readers:
                try:
                    os.close(reader)
                except OSError:
                    pass
            raise ValueError("E_781_CHILD_NOT_READY")
        os.close(fd)
    return children


def reap(children, timeout=5):
    for pid in children:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + timeout
    reaped = 0
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if pid:
            reaped += 1
            continue
        if time.monotonic() >= deadline:
            for group in children:
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if time.monotonic() >= deadline + 5:
            raise ValueError("E_781_PROCESS_TIMEOUT")
        time.sleep(0.01)
    return reaped


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ROLES:
        raise ValueError("E_781_ROLE")
    role = sys.argv[1]
    who, resource, boot = identity(), caps(), str(uuid.uuid4())
    expires = datetime.fromisoformat(
        os.environ["GATE781_EXPIRES_AT"].replace("Z", "+00:00")
    ).timestamp()
    remaining = expires - time.time()
    if remaining <= 0 or remaining > 120 * 60:
        raise ValueError("E_781_LIFETIME")
    if role == "pg":
        emit("gate781:", witness(who, boot, "START", 1, 0, resource))
        # Actual PG runs in the SAME capped cgroup. The TTL remains active even
        # when the remote controller disappears; NEVER restart policy is required.
        pg = subprocess.Popen(
            [
                "/usr/local/bin/docker-entrypoint.sh",
                "postgres",
                "-c",
                "max_connections=12",
                "-c",
                "autovacuum=off",
                "-c",
                "max_worker_processes=0",
                "-c",
                "cluster_name=" + cluster_marker(who["nonce"], who["serviceId"]),
            ],
            start_new_session=True,
        )
        signal.signal(signal.SIGTERM, lambda *_: os.killpg(pg.pid, signal.SIGTERM))
        try:
            code = pg.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            os.killpg(pg.pid, signal.SIGTERM)
            try:
                code = pg.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(pg.pid, signal.SIGKILL)
                code = pg.wait(timeout=5)
        if code != 0:
            raise ValueError("E_781_PG_EXIT")
        return
    if role == "verify":
        emit("gate781:", witness(who, boot, "START", 1, 0, resource))
        emit("gate781-data:", asyncio.run(proof(who, "RESTORED")))
        return
    created = asyncio.run(seed())
    if created:
        emit("gate781-data:", asyncio.run(proof(who, "BASELINE")))

    # Distinct rows avoid child lock contention; choose only one writer replica
    # to hold transactions, other replica runs independent non-DB child trees.
    def child(fd, index):
        if created:
            asyncio.run(hold_transaction(fd, index))
        else:
            signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
            os.write(fd, b"R")
            os.close(fd)
            while True:
                time.sleep(0.05)

    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    children = supervise(child)
    emit("gate781:", witness(who, boot, "START", len(children) + 1, 0, resource))
    while not stopping and time.time() < expires:
        time.sleep(0.05)
    count = reap(children)
    if count != len(children):
        raise ValueError("E_781_PROCESS_COUNT")
    emit("gate781:", witness(who, boot, "TREE_REAPED", len(children) + 1, count, resource))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        code = str(error)
        print(code if re.fullmatch("E_781_[A-Z_]+", code) else "E_781_RUNTIME", file=sys.stderr)
        sys.exit(1)
