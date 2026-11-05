# Gate #745 — диагностика отказа 26 октября и конкретный план завершения

Дата: 26.10.2026, МСК. Основание: [поручение](https://github.com/markmalina/gate/issues/745#issuecomment-6420611096) и прямое продолжение Марка в текущем Work.
Существующий outcome: #745 / PR747 / execution `gate-745-snapshot-20261008T1458Z`.
Статус: **DIAGNOSTICS_COMPLETED / awaiting_owner_plan_approval**. Это не завершение #745 и не выпуск.
Срез source/runtime наблюдений: 19:06–19:12 МСК; итоговая документация публикуется после этого среза.

## Результат

Ложный отказ `task_superseded` для старой незакрытой задачи воспроизведён на точном предикате исходного кода. Он существует **и в deployed-source, и в текущем PR747**. Поэтому простой выпуск нынешнего PR не устраняет этот класс сбоя.

Предикат выбирает любую другую NEEDS_REPLY-задачу того же account/project/conversation. Сортировка created_at DESC лишь выбирает одну строку; она **не сравнивает** её возраст с текущей задачей или прочитанным состоянием. Контекст перечитывается и drafts создаются заново, но старый sibling остаётся в таблице, поэтому отказ повторяется.

Это установленный дефект исходного условия и воспроизведённый механизм. **Не установлено**, что именно такая строка вызвала сегодняшний конкретный Production отказ: строки/логи личного диалога не читались, DB diagnosis не выполнялся.

Отдельно подтверждено по исходнику: single send для NEEDS_REPLY работает с набором covered tasks, а REPLIED follow-up запрещается при любом непустом covered_task_ids. Этот второй предикат тоже не определяет новизну задачи. Нельзя исправлять только queue и считать одиночный путь автоматически согласованным.

## Проверенное состояние

| Слой | Факт и версия | Предел |
| --- | --- | --- |
| Реализовано в коде | PR747 head e5c2220cd8fc437e541595a41c14c0b80397c5c4, snapshot guard, 0116, sender admission, event revisions, собственное продвижение серии | Старый sibling veto остался; новый exact head независимой приёмкой не покрыт |
| Проверено до этой диагностики | Пять check-runs exact e5c2220 завершены SUCCESS: backend/windows/quest/gate-i4/mandatory synthetic merge | CI не означает install/runtime/product acceptance |
| Проверено сейчас | 49 source-derived SQLite/AST/pure-helper диагностических утверждений PASS; 5 исходных Git blobs совпали | Нет pytest/SQLAlchemy/PostgreSQL в текущей среде; полный suite и PG races здесь не запускались |
| Слито | PR747 OPEN, merged=false; current main86dbc0f429dce089bf733fc422f20f556535895b | PR base461088d6 отличается от актуального main, хотя GitHub mergeable=true |
| Production | Scoped gate_get_status: commit afe0c6b85193, manual, send_enabled=true; RU/BY ready | Наблюдение scoped, не глобальный census; полный fingerprint из runtime не получен |
| Main vs guard | В tree main нет app/application/dialog_snapshots.py, app/infrastructure/dialog_snapshots.py и migration0116; queue blob совпадает с deployed-source93788e3 | 0116 и snapshot guard из PR в текущем main отсутствуют |
| Connected Gate M | Контракт get_reply_context не объявляет context_snapshot/context_ready/dialog_events; series2–50, interval_seconds строго60 | Проверена фактически предоставленная схема инструментов, не live отправка и не полный server/client roundtrip |
| Не завершено | Исправление sibling/follow-up, согласование task coverage, installed Ops control, fixture, cutover, клиентский каталог, live приёмка | #745 остаётся открытой |

[Exact head checks](https://api.github.com/repos/markmalina/gate/commits/e5c2220cd8fc437e541595a41c14c0b80397c5c4/check-runs?per_page=100).
[PR747](https://github.com/markmalina/gate/pull/747).
[Предыдущая квитанция](https://github.com/markmalina/gate/issues/745#issuecomment-6267718741).

## Синтетическое воспроизведение

Приложенный reproduction-results.json записывает каждое утверждение и raw/Git hashes исходников.
Никакой application runtime или Telegram client не импортировался. Из AST точного source извлечён запрос newer_task; его where/order/limit исполнены в изолированной SQLite через минимальный адаптер. Из обоих sender sources исполнено точное REPLIED/covered_task_ids-условие. Чистые snapshot/event functions исполнены без изменения.

| Состояние другой задачи | Production predicate | PR747 predicate | Значение |
| --- | --- | --- | --- |
| Нет другой задачи / только текущая | Не запрещает | Не запрещает | Контроль |
| Более старая NEEDS_REPLY в том же scope | Запрещает | Запрещает | Воспроизведён ложный отказ |
| Более новая NEEDS_REPLY | Запрещает | Запрещает | Сам предикат не различает возраст |
| NEEDS_REPLY с тем же timestamp | Запрещает | Запрещает | Время не критерий допуска |
| Старая REPLIED | Не запрещает | Не запрещает | Фильтр status работает |
| Другой account/project/conversation | Не запрещает | Не запрещает | Проверена изоляция предиката |

Ещё проверено: три повтора чтения/пересоздания drafts не меняют входы этого предиката; single REPLIED + любой covered task даёт отказ; snapshot неизменного состояния совпадает; изменение каждого поля scope/revision/message_id/text меняет digest; business_message/edit/delete с text/voice/photo/document/без текста классифицируется по событию. Одинаковый текст другого message_id не является тем же событием.

Эти проверки **не** доказывают всю последовательность get_context→save→queue→dispatch, транзакционную семантику, идемпотентность sender или Production причину. Идемпотентность/гонки оценены чтением уже существующих тестов, не объявлены вновь пройденными.

Изучены tests/test_dialog_snapshots_postgres.py, tests/test_reply_sequences.py, tests/test_reply_sequences_postgres.py. Они содержат проверки raw ingress обеих сторон, stale documents, race после reservation, replay, unknown-related boundaries, two Works и остановку хвоста. В этих просмотренных файлах нет сценария «старый pending sibling + свежий валидный snapshot → допустимая серия». Отсутствие во всём repository test suite не заявляется.

## Зависимости и что из старого устарело

1. **Старый обязательный постоянный GitHub App source key не является текущим требованием обычного deploy.** Текущие Gate AGENTS используют ephemeral GITHUB_TOKEN для Gate-owned short deploy. Не просить Марка заново заводить этот ключ.
2. **Это не устраняет отдельный #745 metadata/source admission blocker.** Ops381 head a441e184c98732db49ad5e02b17efa42cb30a5ef по-прежнему OPEN/unmerged. Opsmain уже **8e68b7535659ea7f3acb112eb3304c669f7c4201**, а не исторический b2d0587. Текущий release.json остаётся enabled=false, fixtureAcceptance=PENDING, independentSourceReview=PENDING, reviewedExecutorSourceSha=null, allocationPolicy=null.
3. Existing private Ops/Delta read binding для A/O не доказан. Это UNKNOWN, а не утверждение «секрета нет». API коннектора не превращается в Actions credential. Значения secrets/settings не читались; secret provisioning не разрешён.
4. Supporting758/760/762 по fresh GET OPEN/unmerged. Старые namespace/identity/mixed-owner проблемы и отдельные source gates нельзя считать установленными решениями.
5. S2 documented feasibility остаётся NOT_PROVEN: effective fleet limits, initial volume, backup completion, dedicated isolation/identity, cleanup и surviving observer authority не имеют свежего native подтверждения. Такие ресурсы не создавались.
6. #718 — остановка хвоста после inbound; #745 — обязательный снимок и новое сообщение **любой стороны**, одиночные ответы/документы/серии. #722 — выпуск серий, supervisor/renewal/stop/canary и operational gates. Использовать общий sender/fence и существующие lanes; не переносить весь старый AQUB backlog. AQUB frozen, его routes/grants неприменимы.

[Ops381](https://github.com/markmalina/ops/pull/381).
[Ops release текущего среза](https://github.com/markmalina/ops/blob/8e68b7535659ea7f3acb112eb3304c669f7c4201/agent-ops/actions/gate-snapshot-cutover/release.json).
[Детальный read transport packet](https://github.com/markmalina/gate/blob/e5c2220cd8fc437e541595a41c14c0b80397c5c4/docs/operations/gate745-s1/READ-TRANSPORT.md).
[Существующее ТЗ семи спринтов](https://github.com/markmalina/gate/blob/e5c2220cd8fc437e541595a41c14c0b80397c5c4/docs/specs/ISSUE-745-SPRINT-SPEC.md).

## Предлагаемый минимальный план реализации — на одобрение

### A. Исправить конкретный runtime defect в прежнем PR747

- Сначала сверить актуальный main/head и совмещённый кандидат, сохранив чужие changes.
- В queue устранить безусловный запрет «существует любая другая NEEDS_REPLY» **только в пути с обязательным валидным reviewed snapshot**. Не заменять snapshot одним created_at: задержанная проекция, raw durable ingress, одинаковые тексты, outbound и edit/delete этим не покрываются.
- Согласовать single NEEDS_REPLY / REPLIED follow-up / sequence с одним immutable reviewed task coverage. Уже охваченные readset tasks сами по себе не являются новым событием. Новые facts после baseline требуют context_changed. Не помечать исторические задачи REPLIED ради снятия запрета; не перелинковывать drafts молча.
- Сохранить separate payload/account/recipient approval, lease/fence, attempt ledger и sent/failed/unknown. Старые drafts без snapshot должны по-прежнему отказывать. Ранее отправленная попытка возвращает прежний receipt без нового provider dispatch.
- Для серии собственный подтверждённый send продвигает snapshot только через существующий durable audit progress. Входящее или постороннее исходящее останавливает хвост, без auto-resume.
- Свести ошибки к реальной причине: context_changed + sent=false + свежий scoped context/delta, при усечении явный paging. Не выдавать старый sibling за новое inbound.
- Обновить действительные MCP input/output/описания для snapshot и новых отказов; существующие hashes/formatting/document semantics сохранить. Интервал60 секунд и предел2–50 сейчас остаются действующим контрактом; произвольные10–80 не реализовывать в этом инциденте.

### B. Доказать исправление до выпуска

- Новые PostgreSQL integration cases: старый sibling/несколько старых tasks + свежий snapshot → queue и single/follow-up допускаются; новое событие до admission → provider dispatch0.
- Матрица: raw ingress до проекции, owner/other Work outbound, одинаковый текст с новым ID, voice/photo/file/edit/delete, чужой scope, устаревший или отсутствующий snapshot.
- Полный server flow после отказа → новое чтение → новые drafts → точное approval → однократная доставка mock provider. Отдельно replay sent/failed, unknown без автоповтора, две Work и конкурентные queue/send, restart, серия2 и50.
- Проверить actual PostgreSQL locking на общей send admission boundary; queue validation не заменяет dispatch guard. Telegram запрос, уже начатый до нового события, нельзя обещать отменить.
- Миграция0116 upgrade/downgrade/schema drift/backup-restore на synthetic DB, formatting/media compatibility и старые необходимые regression suites.
- Один необходимый original exact-head CI после содержательного runtime изменения; независимая exact-source проверка отдельным предусмотренным проектом способом. Собственная диагностика не independent ACCEPT.

### C. Подготовить действительный маршрут выпуска

- В существующем Ops375 установить допустимый narrowly scoped metadata transport A/O либо вернуть **конкретную** защищённую capability-проблему. Не выдумывать новый токен и не просить у Марка значения в чат.
- Завершить/установить reviewed независимый Gate-owned Ops contract и согласованные pins после source admission. Ordinary short deploy не заменяет cutover.
- Закрыть feasibility и authentication/preparation/fixture gaps по существующему ТЗ, подготовить exact supported fixture/cleanup/cost/rollback packet. Существующие источники и grants не расширять автоматически.
- До любой credential/resource/native/Production mutation показать отдельный конкретный subject/revision/target/effects/caps/expiry/rollback packet; текущий отчёт эти эффекты не разрешает.

### D. Выпустить и принять

- Fresh full Production identity/schema/source/fleet, actual autoDeploy=false, зафиксированный LKG и bound backup/restore. Все старые writers остановлены; общий lock; guarded sends=false → 0116 → exact candidate deploy → verify → sameSHA sends=true → independent verify.
- При source/identity/backup/fleet/health/unknown mismatch STOP. LKG afe0c6b85193 здесь только observed baseline, **не доказанный готовый rollback**: полный SHA/schema compatibility и backup ещё должны быть проверены. Не обещать downgrade с работающими новыми writers.
- Обновить реально подключённый Gate M каталог, проверить snapshot roundtrip. Затем отдельный точный Mark-controlled Business canary с синтетическими данными; в существующем ТЗ проект ≤15мин, ≤6 delivery dispatch, model/STT0, bounded reads. Это предложение лимитов, не live grant.
- Доказать unchanged send/replay, новое сообщение любой стороны → refusal0, рабочий повторный цикл, stale files и остановку хвоста. Финальная матрица A01–A12, deployed revision и receipts, отдельная пользовательская приёмка.

## Решение, требуемое от Марка сейчас

Утвердить **A+B: ограниченное исправление кода и MCP-контракта, synthetic/PostgreSQL проверки и подготовку результата в существующем PR747**. Вместе с этим C можно готовить как source/documentation packet без доступа к secrets/provider/data.

Этот выбор не просит повторного разрешения на уже выполненную диагностику. Это следующий конкретный этап согласно пункту7 исходного поручения. Credential setup, ресурсный стенд, Production cutover и live send требуют будущего точного проверяемого пакета; сейчас он не готов, поэтому общее разрешение на них не запрашивается.

Сегодня достигнут проверяемый результат: механизм дефекта воспроизведён, фактические версии/блокеры уточнены, дальнейшая работа ограничена конкретными изменениями. Готовность всей #745 сегодня обещать нельзя: действительный installed control, fixture/cutover и product acceptance пока отсутствуют.

## Данные и границы

Личные диалоги/имена/получатели/task/draft IDs и тексты не читались и в этот пакет не копируются. Старые4 и новые12 drafts не отправлять/не queue/не использовать для canary. Production DB reads0, Telegram/model/STT calls0, outbound0. Вызван только scoped read-only Gate status и repository/automation reads. Другие исходы, PR и автоматы не менялись.

## Воспроизведение

Пять исходных файлов нужно получить из неизменяемых revisions и сохранить рядом с извлечённым reproduce_745.py в audit-source/:

| Имя файла | Revision | Repository path | Git blob |
| --- | --- | --- | --- |
| prod-seq.py | afe0c6b8519381a0007c0dcc1c145207b5d54a3f | app/infrastructure/reply_sequences.py | 93788e3134649b26f26999c535316047f8988ebb |
| pr-seq.py | e5c2220cd8fc437e541595a41c14c0b80397c5c4 | app/infrastructure/reply_sequences.py | 79afa3453c65e74671959ecaca4a92708a63c76e |
| prod-sends.py | afe0c6b8519381a0007c0dcc1c145207b5d54a3f | app/infrastructure/reply_sends.py | 229a45ff6b6dd27db68aa659359de99a5035e200 |
| pr-sends.py | e5c2220cd8fc437e541595a41c14c0b80397c5c4 | app/infrastructure/reply_sends.py | dbae4f01a12fdaefcda461f0def237ae217f8323 |
| pr-app-snapshot.py | e5c2220cd8fc437e541595a41c14c0b80397c5c4 | app/application/dialog_snapshots.py | 23d2a5f614a04794d678b8fc4007d932c770a2d0 |

Источник каждого: https://github.com/markmalina/gate/blob/<Revision>/<Repository-path>.
Получать через repository client или git show с доступом к этому private repository; токены в пример не вставлять.
Запуск: python reproduce_745.py > reproduction-results.json. Git blob каждого файла должен совпасть с таблицей.

Код ниже сохранён внутри Markdown как часть диагностического отчёта; приложение Gate он не изменяет.

```python
"""Stdlib-only source-derived diagnostic; no application/provider/production I/O.

Run with the five immutable source files named in README/evidence. It executes
the exact queue-query AST through a small SQLite query adapter, the exact single
send rejection condition, and the unmodified pure snapshot helper functions.
This is deliberately NOT a PostgreSQL/application/concurrency acceptance test.
"""
import ast
import hashlib
import json
from pathlib import Path
import runpy
import sqlite3
from types import SimpleNamespace as NS

ROOT = Path(__file__).parent / "audit-source"
results = []


class Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return self.name + " = ?", value

    def __ne__(self, value):
        return self.name + " != ?", value

    def desc(self):
        return self.name + " DESC"


class Query:
    def __init__(self, column):
        self.column = column.name

    def where(self, *conditions):
        self.conditions = conditions
        return self

    def order_by(self, *columns):
        self.order = columns
        return self

    def limit(self, count):
        self.count = count
        return self

    def execute(self, rows):
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE TABLE tasks (id TEXT, conversation_id TEXT, account_id TEXT, project_id TEXT, status TEXT, created_at INTEGER)")
            db.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?)", rows)
            query = "SELECT " + self.column + " FROM tasks WHERE "
            query += " AND ".join(c[0] for c in self.conditions)
            query += " ORDER BY " + ", ".join(self.order) + " LIMIT ?"
            return db.execute(query, [c[1] for c in self.conditions] + [self.count]).fetchone()


task_model = NS(**{k: Column(k) for k in ("id", "conversation_id", "account_id", "project_id", "status", "created_at")})
globals_ = {"select": Query, "ReplyTask": task_model, "ReplyTaskStatus": NS(NEEDS_REPLY=NS(value="needs_reply")), "first_conversation": NS(id="dialog"), "first_draft": NS(account_id="account", project_id="project"), "first_task": NS(id="current")}
scenarios = [
    ("no_sibling", [], False),
    ("current_task_only", [("current", "dialog", "account", "project", "needs_reply", 20)], False),
    ("older_pending_same_dialog", [("old", "dialog", "account", "project", "needs_reply", 10)], True),
    ("newer_pending_same_dialog", [("new", "dialog", "account", "project", "needs_reply", 30)], True),
    ("same_timestamp_pending", [("same_time", "dialog", "account", "project", "needs_reply", 20)], True),
    ("older_replied", [("old", "dialog", "account", "project", "replied", 10)], False),
    ("other_account", [("old", "dialog", "other", "project", "needs_reply", 10)], False),
    ("other_project", [("old", "dialog", "account", "other", "needs_reply", 10)], False),
    ("other_dialog", [("old", "other", "account", "project", "needs_reply", 10)], False),
]
for version in ("prod", "pr"):
    source = (ROOT / (version + "-seq.py")).read_text()
    assignment = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "newer_task" for t in n.targets))
    query_ast = assignment.value.value.args[0]
    expression = ast.Expression(body=query_ast)
    compiled = compile(ast.fix_missing_locations(expression), "source-query", "eval")
    for name, rows, expected in scenarios:
        actual = eval(compiled, globals_).execute(rows) is not None
        assert actual == expected, (version, name, actual)
        results.append({"check": version + ":" + name, "pass": True, "task_superseded": actual})
    old = scenarios[2][1]
    # Rereading context or replacing drafts does not mutate this predicate's inputs.
    for retry in range(3):
        assert eval(compiled, globals_).execute(old) is not None
    results.append({"check": version + ":reread_and_new_drafts_cannot_clear_sibling_predicate", "pass": True})
    sender_tree = ast.parse((ROOT / (version + "-sends.py")).read_text())
    condition = next(n.test for n in ast.walk(sender_tree) if isinstance(n, ast.If) and "current.task_status == ReplyTaskStatus.REPLIED.value and covered_task_ids" == ast.unparse(n.test))
    condition_code = compile(ast.Expression(body=condition), "single-send-condition", "eval")
    for status, covered, expected in [("replied", ("old",), True), ("needs_reply", ("old", "current"), False), ("replied", (), False)]:
        actual = bool(eval(condition_code, {"current": NS(task_status=status), "covered_task_ids": covered, "ReplyTaskStatus": NS(REPLIED=NS(value="replied"))}))
        assert actual == expected
        results.append({"check": version + ":single_send:" + status + ":" + str(len(covered)), "pass": True, "rejects": actual})

helpers = runpy.run_path(str(ROOT / "pr-app-snapshot.py"))
digest = helpers["snapshot_digest"]
matches = helpers["snapshot_matches"]
state = {"workspace": "synthetic", "account": "account", "project": "project", "chat": "dialog", "revision": 1, "message_id": "one", "text": "same"}
original = digest(state)
assert matches(original, digest(dict(state)))
results.append({"check": "unchanged_snapshot", "pass": True})
for field in state:
    assert not matches(original, digest({**state, field: str(state[field]) + "changed"}))
    results.append({"check": "snapshot_change:" + field, "pass": True})
for kind in ("business_message", "edited_business_message", "deleted_business_messages"):
    for content in ({"text": "same"}, {"voice": {}}, {"photo": []}, {"document": {}}, {}):
        assert helpers["telegram_dialog_key"]({kind: {"business_connection_id": "synthetic", "chat": {"id": 123}, **content}}) == ("synthetic", "123")
        results.append({"check": "event:" + kind + ":" + next(iter(content), "empty"), "pass": True})

print(json.dumps({"scope": "source-derived predicate and pure helper diagnostics only", "pass_count": len(results), "production_db_reads": 0, "provider_calls": 0, "outbound_messages": 0, "sources": {p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "git_blob_sha1": hashlib.sha1(b"blob " + str(len(p.read_bytes())).encode() + b"\0" + p.read_bytes()).hexdigest()} for p in sorted(ROOT.glob("*.py")) if p.name in ("prod-seq.py", "pr-seq.py", "prod-sends.py", "pr-sends.py", "pr-app-snapshot.py")}, "checks": results}, ensure_ascii=False, indent=2))
```
