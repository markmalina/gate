# MTProto RU — ревизия ТЗ, исходников и плана 12.10.2026

Канон: [Gate #696](https://github.com/AlphaCenter/gate/issues/696). Автор этой ревизии: α по поручению Марка. Область: глубокая проверка и актуализация плана. **Не formal Delta ACCEPT, не реализация и не разрешение live effects.** Результат публикуется в существующем [PR #759](https://github.com/AlphaCenter/gate/pull/759), без нового PR.

## 1. Вывод и изменение относительно оценки 11.10

Ревизия v2 полезна: обоснованно сохраняет Telethon/Gate/PostgreSQL, standalone command, единственного владельца основной сессии, общую координацию Business/MTProto и разделение первых send/read результатов. Переход на другой transport, второй MCP gateway, новый broker или full account mirror сейчас не обоснован.

После оценки 11.10 23:28 появился код [PR #761](https://github.com/AlphaCenter/gate/pull/761). Он закрывает часть прежнего пробела с воспроизводимыми session/SQL proofs: есть реальные исходники, 25 provider-free и 4 PostgreSQL contention tests, exact-head CI. Он не закрывает V0/V1 целиком. Staged worker не подключён, active schema/entrypoint/setup/dispatcher/new Work tools отсутствуют.

Главный организационный недостаток v2 — крупный V0 и общая формулировка «Ops contract». Она смешивает подготовку source, реальные Work проверки, protected custody и runtime release. V3 делит это на пакеты, фиксирует владельца каждой зависимости и позволяет безопасным source направлениям продвигаться при отдельном live blocker.

Главные новые source находки: нет локального deadline у renewal после startup; поздний receipt после смены владельца нельзя записать обычным finish; self/user alias не канонизирован; durable grants/client binding и all-writer guard отсутствуют; corrupt queued payload может блокировать выбор следующей операции. Все это относится к **ещё не активированному кандидату**, а не утверждает найденный live production exploit.

## 2. Доказательства и границы

| ID | Прочитанный предмет | Exact subject | Что подтверждено |
| --- | --- | --- | --- |
| E01 | Gate main/корневые инструкции/parent/bootstrap | `461088d68d047e4cd08c7a4f581300d591af241e` | Repository identity AlphaCenter/gate; direct parent Center; Gate local domain; independent Ops requirement |
| E02 | PR759 metadata и оба документа целиком | `50792e3db48ee26084f6eeb839a5661b34ac16c4` | Draft/open/unmerged; v2 23 families/28 scenarios, OSS14 comparison |
| E03 | PR761 metadata, все added source/docs/scripts/tests и workflow delta | `3955eb0539c1b30bd0816a958ffc7127f768fcb3` | 14 files, source foundation, no active runtime wiring |
| E04 | Exact-head workflow/job metadata и backend logs | [run38175506026](https://github.com/AlphaCenter/gate/actions/runs/38175506026) | Literal checkout3955; backend/windows SUCCESS; Ruff/Mypy314; 2943 passed,1 skipped,12 subtests |
| E05 | Current scoped Gate status | production `afe0c6b85193`, observed12.10 около08:13 Europe/Minsk | personal configured/authenticated=null, runtime_connected=false, send/create_group=false, local_session_not_bound; current catalogue no personal tools |
| E06 | Current #696 comments/receipts | [terminal6113926523](https://github.com/AlphaCenter/gate/issues/696#issuecomment-6113926523) | Bounded source package done, compute lease released; next work remains dependency/source |
| E07 | Business fence/inbox/Mayak, OAuth/Principal/metadata/file bridge, Docker/constraints | Gate461088d | Draft-required fence; blanket task close; separate Mayak journal; no client identity in Principal; existing task-bound byte bridge; Docker has Telethon |
| E08 | Current #747 metadata и snapshot/migration patches | `9723382ba37fd3b3d15430d7e1ba436217d0d72b` | Open/unmerged snapshot source; keys are Business connection/chat; schema0116 from watch0115 |
| E09 | Current #744 metadata/patches и main migrations | `08c499b943b432ab1ceca78c2d6df6bba57174c1` | Stale base; media0115 and watch0115 different IDs but same predecessor0114 |
| E10 | Installed Gate deploy workflow и exact Ops executor | Ops`7bd47fe1c646f30c87207dc513cd1e5990d23a78` | Simple current-main fixed-web-service deployment; no custody/worker/migration orchestration |
| E11 | Ops main tree, Gate745 README/profile/release | `b2ca3677ec5f664d9ac0eca2359b4cbcceed605e` | Separate Gate745 fixed-profile source exists; release disabled/PENDING, no MTProto authority |
| E12 | Official Telegram/Telethon/OpenAI docs, opened12.10 | URLs in §8 | Main auth-key concurrency constraint, RPC deadline vs connection timeout, file host contract, content context constraints |
| E13 | One local exact-source worker probe | PR761 worker body; synthetic ports | Hung post-startup renewal + stop stays pending/connected; forced cancel disconnects/releases synthetic client |
| E14 | Private automation lookup | Safe projection only | One MTProto morning report is report-only; past one-shot/no next-run does not prove active implementation |

Общий Gate status включает соседние блоки. В этой ревизии сохраняется только E05; raw status, OAuth subject/account/person/media identifiers, unrelated counts/payloads и automation IDs в Git не помещаются. В RU-only live scope нельзя перенести BY проверку из соседнего проекта.

Не выполнены: свой полный pytest/PG suite, настоящий Telegram connect/authorization/DC migration/session restart, secrets inspection, live Work new-tools/files, provider inventory/cost, real DB schema/backup, deployments. CI прочитан отдельно и не приписывается локальному запуску. Изолированный probe не заменяет integration test.

## 3. Что в #761 уже сделано качественно

- Encrypted authenticated session envelope binds workspace/account/binding/owner/generation; async CAS serializes saves, failure/cancellation poisons session instead of retrying stale revision.
- Actual pinned Telethon auth callback/save/DC-field/close/clone logic exercised without network. Transient clone does not overwrite main encrypted record.
- Standalone prepare/replay/conflict and stable random_id persist before provider I/O; separately keyed equal text is independent command.
- Ownership latch survives lease expiry; replacement generation cannot be acquired until trusted disconnect release. Concurrent PostgreSQL owner/prepare/CAS/claim checks test real DB locks.
- Typed user/chat/channel numeric identity; scoped UNKNOWN quarantine lets unrelated peers proceed.
- Default-off worker never constructs client; failed disconnect latches ownership. No source auto-wiring.
- Capability/method/writer inventories explicitly label planned/candidate states. 104 records,66 constructors и42 call sites не называются supported functions.

Подготовленную основу сохранить. Ревизия не рекомендует переписывать её с нуля и не объявляет весь PR непригодным из-за будущих activation пробелов.

## 4. Реестр находок

Severity здесь означает условие до указанного выпуска, не сегодняшнюю production уязвимость. «Source» — чтение конкретного кода; «reproduced» — локальная проверка; «inference» — следствие, которое следующий implementation пакет должен подтвердить targeted test.

| ID / этап | Находка и доказательство | Значение / требуемое закрытие |
| --- | --- | --- |
| F01 · V1 | **Reproduced.** cloud_worker.run_until: acquire_owner вне startup deadline; обычный heartbeat renew_owner159–164 без wait_for. Новый probe достиг READY, завис renewal и установил stop; task не завершился, fake client остался connected | Add pool/SQL/operation deadlines, bounded stop/signals/cancellation; health expires, disconnect or ownership-latched blocker. Test DB hang после READY, а не лишь exception before ready |
| F02 · V0-B/V2 | **Source, acknowledged scaffold.** CommandDelegation DTO comes from caller; Principal has no issuer/client identity. execute checks delegation_id/scope but not stored client_namespace. claim explicitly not dispatch admission | Durable grant registry, trusted OAuth client identity/namespace, expiry/revocation epoch and exact payload/peer/context at final dispatch. status/cancel/result refs тоже auth-bound. Not fixable by confirmed=true |
| F03 · V1 | **Source gap.** DB binding owns RU label+owner subject and session generation; worker expected_provider_user_id supplied constructor; no bound service/deployment/boot/source recovery evidence | Reviewed setup pins actual provider owner/application; termination receipt ties old owner token to exact process/source/session generation. Old auth-check/contact-sync/enrollment paths must obey ownership or be denied |
| F04 · V2 | **Source.** PeerIdentity allows self and user; keys self:42/user:42 differ though Saved Messages can identify same physical owner peer | Normalize presentation aliases server-side; one physical self peer admission/quarantine. Direct DTO provider IDs must not substitute server-issued resolver proof. No live bypass was exercised |
| F05 · V1/V2 | **Source.** finish requires current unexpired owner token and matching old attempt lease. After release/replacement, late original ACK cannot settle through this function; current late-ACK test uses same live owner | Separate authenticated idempotent receipt recording/reconciliation with immutable attempt binding. Contradictory/replayed receipts denied; no extra dispatch/right revival. Old unknown quarantine resolvable by proof |
| F06 · V1/V2 | **Source inference.** claim picks first queued row then unseals inside transaction; corrupt payload/wrong key raises and leaves queued head unchanged | Bounded poison disposition before I/O; preserve forensic metadata and versioned ciphertext; next eligible peer progresses. Confirm on PostgreSQL; do not label corrupt command as already sent |
| F07 · V2/V5 | **Source/design gap.** attempt uniqueness one-per-operation; claim writes unknown before I/O; cancel labels cancelled_after_dispatch even when only reserved; no dispatch marker | Define reservation vs dispatch/effect truth and immutable attempt lifecycle. Pilot: no automatic mutation retry, explicit retry_after. Future safe/no-effect retries need compatible multi-attempt policy; not blanket retry UNKNOWN |
| F08 · V1/V7 | **Source.** Session has key_version; encrypted command payload rows have no key_version; restore/rotation path absent | Pin payload key generation; retain old keys until all outstanding operations/receipts recoverable; test rotation/restore with UNKNOWN/expired grants. No default secret deletion |
| F09 · V0-E/V2 | **Source.** AST inventory not exhaustive; modern fence requires ReplyDraft; Mayak uses separate AgentSendAttempt; Business outbound closes all pending tasks | Reachability+deny/guard for every RU sink, joined active/unknown journals, account typed peer and causal task coverage. Common actual dispatch boundary, not just wrappers whose guards can be bypassed |
| F10 · V2 | **Source integration gap.** #747 versions keyed connection/chat, snapshot incorporates routed scope/history; no personal typed-peer hook; receive_updates=False cannot know all unseen MT events | Explicit adapter to canonical peer and observed frontier; reply vs exact standalone intent; ACK/echo idempotent projection and revision bump once. Preserve #745 guarantees/limits; do not assume source acceptance transfers |
| F11 · V4/integration | **Source.** #744 media revision0115_reply_media_packages and main0115_watch_voice_post_payment both descend0114; #7470116 descends watch0115 | Different revision IDs, not literal duplicate. Blind union produces two heads. Reconcile fresh integration source/migration owner before release; no rewrite deployed revision IDs; MT schema numbering not preallocated blindly |
| F12 · V0-B/D | **Source+host docs.** Current MCP metadata advertises existing scopes only; new personal scopes absent. Upload bridge needs ReplyTask and base64; no personal roundtrip | New scopes + refreshed catalogue/new-session discovery; stable host request-key experiment; actual private file transport. Official optional helpers are reference, not evidence current Gate M host supports them |
| F13 · V0-C/V1 | **Source, concrete blocker.** Ops has more than one Gate path, but simple deploy only web; separate745 profile fixed #745/schema0116 and disabled/PENDING | Prepare narrow Gate-owned MTProto operation packet; reuse reviewed primitives, not credentials/grants/authority. Accepted Ops executor owner/result needed. Minimal inspect first, not wait for all ten generic operations |
| F14 · V3 | **Specification gap.** v2 says permitted AI context but does not define proof/scope/withdrawal; search/list may return snippets outside selected content scope | Define context/participants/purpose/consent/retention, separate metadata discovery; conditional live selected content, no full mirror or blanket permission. Platform terms handled without declaring every read universally legal/illegal |
| F15 · runtime/product | **Evidence limitation.** Worker restart test uses FakeClient; session test uses MemoryRepository;4PG tests use isolated metadata | Need combined actual Session→PG reload→new process and bounded true owner probe, plus all-writer PG tests and current Work UX. Broader2943 count is regression evidence, not certification of missing code |
| F16 · execution | **Receipt.** source lease released; one-shot report-only task | Record no active execution proof. Next owner claims a bounded source package; external blocker must not be described as continuously working code |

Дополнительные корректировки: Docker main уже устанавливает Telethon, поэтому прежнее описание optional-not-installed устарело. Сохраняется tested1.44; переход1.45 не нужен ради самого перехода. New connection_retries=1 отличается от прежнего0, но request_retries=0; change must have explicit bounded-connect decision, no unsupported duplicate-effect claim.

## 5. Новая карта зависимостей

| Пакет | Вход | Конкретный выход | Владелец и доказательство |
| --- | --- | --- | --- |
| V0-B | #761 scaffold + actual OAuth/metadata | Durable grants/client binding + opaque jobs/refs + synthetic new tools | Gate; source/PG tests/actual Work auth refresh before live |
| V0-C | E10/E11 + proposed activation doc | Small reviewed inspect/inventory/termination operation; separate source plan for migration/worker/import | α Gate packet → accepted α Ops; immutable contract/receipt, no inferred handoff |
| V0-D | Current byte bridge + official file params | One synthetic upload/private download roundtrip | Gate; actual host+hash; file-only blocker separated |
| V0-E | E07/#761 candidate inventory | Complete reachable RU writer/session-user table + common identity/admission fixtures | Gate; source reachability and real PG races |
| V1-S | #761 + F01–F08 | Worker CLI, additive active schema/grants/keys, bounded lifecycle and receipt reconcile | Gate source package; activation still default-off |
| V1-A / M0 | Accepted Ops + V1-S + exact owner scope | Bound RU, actual owner, restart/status/recovery in Work | Gate runtime + Ops receipts; no history/send |
| V2-S | V0-B/E + minimal peer resolver + #747 compatibility | First text dispatcher with Business/echo/snapshot/replay semantics | Gate; joint PG tests before send |
| V2-A / M1 | M0 + V2-S + exact selected text/target | One owner-approved standalone text from Work with receipt/replay | Gate; cap1 test, real Work; no read-history prerequisite |
| V3-S/A / M2 | Common scope/worker/resolver + selected context | Bounded search/history/thread/cursors/partial evidence from Work | Gate; allowed context and real range/result |
| V4 / M3 | V0-D + mutation/read core | Rich/reply/chunks + documents then native media | Gate; actual human download/file semantics, media dependency reconciled |
| V5/V6 | Proven mutation core/individual rights | Manage selected messages/schedule/groups/contacts/organization | Gate; per-effect acceptance and no bulk/paid scope |
| V7 / Full | All required families | C01–C23 with T01–T38 evidence and owner product acceptance | Gate α; independent review distinct from source author |

Любая blocked protected dependency имеет owner, входной packet и проверяемый выход. Next safe source пакет не ждёт login, чужой истории, provisioning или file UX. Ресурсы/стоимость/сроки не измерены; plan packets не обозначаются неделями.

## 6. Приёмка и доказательство прогресса

- **Foundation source:** полезные модули/test evidence есть в PR761; unmerged. Не заменять этим M0.
- **M0:** actual RU owner + persisted credentials + one primary worker + live restart/recovery + honest Work status/source/schema/freshness.
- **M1:** точный выбранный текст, первый самостоятельный send without inbound/ReplyDraft, one provider message creation receipt; replay/timeout/status return no second effect; API receipt не означает прочтение человеком.
- **M2:** выбранный допустимый RU чат/окно; observed pages, boundaries, dates/refs, limits/continuation/partial_reason. Не полный экспорт.
- **M3:** выбранный файл реально доступен из текущего Work; document checksum и native media semantics различаются.
- **Full:** no mandatory planned family marked supported without source+tests+runtime+Work; T01–T28 сохранены, T29–T38 добавлены для найденных gaps.

После каждых source changes fresh exact head and relevant checks; старый CI/Delta к новой ревизии не переносится. Formal independent attempt имеет отдельный Delta issue по Center policy; автор этой плановой ревизии не выдаёт себе ACCEPT. Merge/deploy/closed issue — отдельные действия с доказательствами.

## 7. Пакет для передачи Ops

**Статус: ПОДГОТОВЛЕНО К ПЕРЕДАЧЕ.** Новой Ops issue и person-directed delivery в этом planning исполнении не создавалось; accepted executor не подтверждён. Исходное [activation proposal](https://github.com/AlphaCenter/gate/blob/3955eb0539c1b30bd0816a958ffc7127f768fcb3/docs/specs/MTPROTO_RU_RUNTIME_ACTIVATION_CONTRACT.md) сохраняется как input. Следующее направление не требует повторного общего архитектурного решения Марка.

- Автор/отправитель: α Gate; адресат: α Ops, local infrastructure owner. Канон задачи/контекста: #696, этот audit и v3 §11.
- Задача первой передачи: подготовить reviewed fixed-target **read-only inventory + RU custody shape** operation; separate acknowledgement of capability/limits. Provider execution только по конкретному разрешённому safe contract, не данным этой ревизии.
- Read output: present/absent/incomplete/decryptable/invalid и session/source/key-generation/owner-match statuses without values; verified owner/source/config/fleet/auto-deploy/worker availability/cost metadata. Credential/app/session values, DB dumps/content/OTP не возвращаются.
- Межпроцессная остановка: termination proof связывает binding+session generation+old lease token с actual deployment/boot/source; whole auth-key users inventory включает CLIs/contact-sync/probes/setup/runtime. Expired lease/replica1/empty process map сами не являются stop proof.
- Source proposal второй передачи: reviewed additive migration + backup/restore compatibility, dedicated disabled worker deploy, import/verify и bounded rollback/drain receipts. Один migration owner; no automatic double preDeploy.
- Restrictions: only RU; BY excluded; AQUB frozen; no credential-copy/provider dashboard fallback; no production mutation, new resource/price or connection from a planning packet; no arbitrary shell/SQL/targets. Current Gate745 profile remains its own fixed scope.
- Materials: Gate461088d, runtime761/3955eb0, Opsb2ca367, simple deploy7bd47fe, exact proposed schemas/run paths and v3 findings.
- Acceptance: source-bound operation/target contract independently reviewed; actor/workflow/issuer/source/expiry/hard caps; clear receipt schema, failure/UNKNOWN handling; then actual authorized operation yields fresh proof. Proposal≠delivery≠acceptance≠execution.
- Next step owner: α Gate prepares/resolves formal project-to-project handoff using existing matching Ops outcome if found, otherwise smallest bounded issue with explicit owner acceptance. Технические шаги, доступные исполнителю, Марку не перекладывать.

## 8. Официальные источники и интерпретация

Прочитаны12.10:

- [Telegram errors §406](https://core.telegram.org/api/errors): two main TCP sessions using same authorization key can conflict even from one IP; media sessions and explicit tmp_sessions exception differ. Gate pilot retains one main owner.
- [Telethon client docs](https://docs.telethon.dev/en/stable/modules/client.html): constructor timeout относится к соединению; RPC/SQL/application waits require own deadlines. Current stable docs1.45 do not independently certify async hooks of pinned1.44.
- [OpenAI plugin reference, File APIs](https://developers.openai.com/plugins/reference): optional host helpers/fileParams define how a host can authorize selected files. They do not prove current connected Gate M implements that transport. Synthetic actual-host roundtrip remains required.
- [Telegram API Terms](https://core.telegram.org/api/terms) and [Content Licensing](https://telegram.org/tos/content-licensing): AI content handling has platform restrictions and context-specific consent exception. V3 specifies the required bounded evidence instead of inferring entitlement from account ownership or public access.

Инженерные решения/оценки в §4–§7 — выводы этого ревью на основании указанных sources. Platform contract reading — не новая всеобщая legal attestation и не утверждение нарушения existing Gate flows.

## 9. Воспроизведение F01 без provider/БД

Исполнен exact body `cloud_worker.py` из3955eb0 с заменой импортируемых портов. Это узкий deterministic worker test; никакой настоящий Telegram/SQL и full application composition не выполняются.

Steps: remove only app imports via AST → compile original module body → inject synthetic session/store/client → complete startup → hang second READY renewal → set stop → inspect task/connection → force cancel to cleanup. Deadline probe used100ms after stop solely to show event is not observed; source code has no renewal deadline, so это не измерение production recovery time.

Observed safe result:

```json
{"client_still_connected":true,"db_renewal_blocked":true,"exact_source_body":true,"forced_cancel_cleanup_disconnected":true,"forced_cancel_cleanup_released":true,"provider_calls":0,"stop_requested":true,"synthetic_db_and_client":true,"worker_completed":false}
```

Minimal reproducible harness from repository root (Python3.12; current module contains no eager Telethon import):

```python
import ast, asyncio, sys, types
from datetime import UTC, datetime, timedelta
from pathlib import Path

path = Path("app/personal_telegram/cloud_worker.py")
tree = ast.parse(path.read_text())
tree.body = [n for n in tree.body if not (
    isinstance(n, ast.ImportFrom) and (n.module or "").startswith("app."))]
module = types.ModuleType("isolated_exact_worker")
sys.modules[module.__name__] = module

class CommandRuntimeError(RuntimeError):
    pass

async def open_session(**kwargs):
    return object()

module.__dict__.update(
    CommandRuntimeError=CommandRuntimeError,
    utc_now=lambda: datetime.now(UTC), SqlCommandStore=object,
    LeaseToken=object, SessionCipher=object,
    open_encrypted_session=open_session)
exec(compile(tree, str(path), "exec"), module.__dict__)

class Store:
    def __init__(self):
        self.scope = types.SimpleNamespace(validate=lambda: None)
        self.ready_calls = 0
        self.hung = asyncio.Event()
        self.released = False
    async def acquire_owner(self, **kwargs):
        return object()
    async def renew_owner(self, token, **kwargs):
        if kwargs["health"] == "ready":
            self.ready_calls += 1
            if self.ready_calls > 1:
                self.hung.set()
                await asyncio.Event().wait()
    async def release_disconnected_owner(self, token):
        self.released = True

class Client:
    connected = False
    async def connect(self): self.connected = True
    async def is_user_authorized(self): return True
    async def owner_id(self): return 42
    async def disconnect(self): self.connected = False
    def is_connected(self): return self.connected

async def check():
    store, client, stop = Store(), Client(), asyncio.Event()
    worker = module.RuCloudWorker(
        store=store, cipher=object(), session_generation=1,
        expected_provider_user_id=42, client_factory=lambda _: client,
        enabled=True, lease_ttl=timedelta(seconds=5),
        heartbeat_seconds=0.01, startup_timeout=0.1,
        disconnect_timeout=0.1)
    task = asyncio.create_task(worker.run_until(stop))
    await asyncio.wait_for(store.hung.wait(), 1)
    stop.set()
    done, _ = await asyncio.wait({task}, timeout=0.1)
    assert not done and client.connected
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert not client.connected and store.released

asyncio.run(check())
```

## 10. Итог этого planning execution

ТЗ актуализируется до v3; old source/CI/provenance сохраняются, planned capabilities не объявляются supported. Публикация/обратное чтение exact docs commit фиксируются в #696 отдельно после сохранения. Статус implementation не меняется этой ревизией.

Next implementation: **V0-B Work/grant/job boundary + V0-C narrow Ops handoff; V1-S fixes can proceed independently**. При blocked external operation выбрать следующий независимый source шаг, пока остаётся полезная разрешённая работа. Фон и continuous execution без механизма не обещать.
