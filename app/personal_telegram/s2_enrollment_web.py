"""One-owner, one-time MTProto enrollment on the isolated S2 stand."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from time import monotonic
from typing import Any, Literal
from urllib.parse import parse_qs

from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from telethon import TelegramClient  # type: ignore[import-untyped]
from telethon.errors import (  # type: ignore[import-untyped]
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession  # type: ignore[import-untyped]
from telethon.tl.types.auth import (  # type: ignore[import-untyped]
    SentCode,
    SentCodeTypeApp,
    SentCodeTypeEmailCode,
    SentCodeTypeSms,
)

from app.personal_telegram.cloud_session import SCHEMA
from app.personal_telegram.cloud_worker import TelegramApplication

COOKIE = "__Host-gate-s2"
LOG = logging.getLogger("uvicorn.error")
PROJECT_ID = "4d14a47e-0f3c-47cf-9eb9-893fae7e2044"
VAULT = Path("/gate-vault")
ENVELOPE = "ru-session.bin"
SESSION_REF = "session_gate_s2_ru"
HEADERS = {
    "Cache-Control": "no-store, private",
    "Content-Security-Policy": (
        "default-src 'none'; img-src 'self'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'; style-src 'unsafe-inline'"
    ),
    # Document form POSTs need their same-origin Origin header for CSRF checks.
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Robots-Tag": "noindex, nofollow",
}
STYLE = (
    "html{color-scheme:light dark}body{font:17px system-ui;max-width:36rem;"
    "margin:3rem auto;padding:1.25rem}input,button{font:inherit;padding:.5rem;"
    "margin:.5rem 0}img{width: min(90%,320px);display:block;background:white}"
)
ACCESS = (
    '<form method="post" action="/unlock"><label>Ключ доступа к стенду'
    '<br><input name="access" type="password" required autocomplete="off" maxlength="128">'
    "</label><br><button>Открыть</button></form>"
)
START = (
    '<form method="post" action="/start-code"><button>Войти по коду</button></form>'
    '<form method="post" action="/start"><button>Показать QR</button></form>'
)
CODE = (
    '<form method="post" action="/code"><label>Код входа'
    '<br><input name="code" type="password" inputmode="numeric" '
    'required autocomplete="one-time-code" maxlength="12"></label>'
    "<br><button>Подтвердить</button></form>"
)
PASSWORD = (
    '<form method="post" action="/confirm"><label>Пароль двухэтапной проверки Telegram'
    '<br><input name="password" type="password" required autocomplete="off" maxlength="256">'
    "</label><br><button>Подтвердить</button></form>"
)


@dataclass(frozen=True, slots=True, repr=False)
class Config:
    application: TelegramApplication
    phone: str
    access: str
    cipher: Fernet
    origin: str
    vault: Path

    @classmethod
    def read(cls) -> Config | None:
        origin = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
        phone = os.getenv("GATE_S2_OWNER_PHONE", "")
        access = os.getenv("GATE_S2_ACCESS_KEY", "")
        if (
            os.getenv("RAILWAY_PROJECT_ID") != PROJECT_ID
            or os.getenv("RAILWAY_ENVIRONMENT_NAME") != "staging"
            or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") != str(VAULT)
            or not re.fullmatch(r"[a-z0-9-]+\.up\.railway\.app", origin)
            or not re.fullmatch(r"\+7[0-9]{10}", phone)
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", access)
        ):
            return None
        try:
            mounts = Path("/proc/self/mountinfo").read_text().splitlines()
            if not any(line.split(" ")[4] == str(VAULT) for line in mounts):
                return None
            app = TelegramApplication(
                int(os.environ["GATE_PERSONAL_TELEGRAM_API_ID"]),
                os.environ["GATE_PERSONAL_TELEGRAM_API_HASH"],
            )
            app.validate()
            cipher = Fernet(os.environ["GATE_S2_SESSION_KEY"].encode("ascii"))
        except (KeyError, ValueError, RuntimeError, OSError):
            return None
        return cls(app, phone[1:], access, cipher, f"https://{origin}", VAULT)


def page(message: str, form: str = "", *, status: int = 200, refresh: bool = False) -> HTMLResponse:
    refresh_tag = '<meta http-equiv="refresh" content="2;url=/">' if refresh else ""
    return HTMLResponse(
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"{refresh_tag}<title>Gate · Telegram RU</title><style>{STYLE}</style>"
        f"<main><h1>Gate · Telegram RU</h1><p>{message}</p>{form}</main></html>",
        status_code=status,
        headers=HEADERS,
    )


def verify_owner(user: Any, expected_phone: str) -> int:
    if (
        user is None
        or getattr(user, "bot", True)
        or not getattr(user, "is_self", False)
        or type(getattr(user, "id", None)) is not int
        or user.id <= 0
        or not isinstance(getattr(user, "phone", None), str)
        or not secrets.compare_digest(user.phone, expected_phone)
    ):
        raise ValueError("owner_mismatch")
    return int(user.id)


def save_envelope(config: Config, *, owner_id: int, session: str) -> None:
    if not session or len(session) > 4096:
        raise ValueError("session_unavailable")
    payload = {
        "schema": SCHEMA,
        "api_id": config.application.api_id,
        "owner_user_id": owner_id,
        "session_ref": SESSION_REF,
        "session": session,
    }
    temporary = config.vault / f".enrollment-{secrets.token_hex(8)}"
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(config.cipher.encrypt(json.dumps(payload).encode()))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, config.vault / ENVELOPE)
        fd = os.open(config.vault, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


async def verify_saved_session(config: Config, run_id: str) -> None:
    """Read the persisted envelope once per explicitly selected verification run."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", run_id):
        LOG.warning("s2_ru_saved_session_probe_invalid_run")
        return

    try:
        with (config.vault / ENVELOPE).open("rb") as handle:
            raw = handle.read(16_385)
        if not 0 < len(raw) <= 16_384:
            raise ValueError("invalid_envelope")
        payload = json.loads(config.cipher.decrypt(raw))
        owner_id = payload["owner_user_id"]
        session = payload["session"]
        if (
            payload["schema"] != SCHEMA
            or payload["api_id"] != config.application.api_id
            or payload["session_ref"] != SESSION_REF
            or type(owner_id) is not int
            or owner_id <= 0
            or not isinstance(session, str)
            or not 0 < len(session) <= 4096
        ):
            raise ValueError("invalid_envelope")
        with (config.vault / f".verify-{run_id}").open("xb") as claim:
            os.fsync(claim.fileno())
        descriptor = os.open(config.vault, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except FileExistsError:
        return
    except (OSError, InvalidToken, KeyError, TypeError, ValueError):
        LOG.warning("s2_ru_saved_session_probe_unavailable")
        return

    client = None
    verified = False
    try:
        client = TelegramClient(
            StringSession(session),
            config.application.api_id,
            config.application.api_hash,
            receive_updates=False,
            catch_up=False,
            auto_reconnect=False,
            request_retries=0,
            connection_retries=0,
            flood_sleep_threshold=0,
            timeout=5,
            device_model="Gate RU S2",
            app_version="S2 saved session check",
        )
        del session, payload, raw
        async with asyncio.timeout(20):
            await client.connect()
            verified = verify_owner(await client.get_me(), config.phone) == owner_id
    except Exception:
        LOG.warning("s2_ru_saved_session_probe_failed")
        return
    finally:
        if client is not None:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=7)
            except Exception:
                LOG.warning("s2_ru_saved_session_probe_disconnect_failed")
                verified = False

    if verified:
        LOG.info("s2_ru_saved_session_verified_and_disconnected")
    else:
        LOG.warning("s2_ru_saved_session_probe_owner_mismatch")


class Enrollment:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.lock = asyncio.Lock()
        self.cookie = ""
        self.deadline = 0.0
        self.unlock_after = 0.0
        self.next_code_after = 0.0
        self.attempts = 0
        self.state = "ready"
        self.image: bytes | None = None
        self.code_length = 0
        self.code_hint = ""
        self.code: asyncio.Queue[str] = asyncio.Queue(1)
        self.password: asyncio.Queue[str] = asyncio.Queue(1)
        self.task: asyncio.Task[None] | None = None

    def authorized(self, cookie: str) -> bool:
        return bool(
            self.cookie
            and monotonic() < self.deadline
            and secrets.compare_digest(self.cookie, cookie)
        )

    @property
    def enrolled(self) -> bool:
        return (self.config.vault / ENVELOPE).is_file()

    async def sign_in_by_code(self, client: Any) -> None:
        phone = f"+{self.config.phone}"
        sent = await client.send_code_request(phone)
        if not isinstance(sent, SentCode) or not 4 <= getattr(sent.type, "length", 0) <= 12:
            raise ValueError("unsupported_code_delivery")
        if isinstance(sent.type, SentCodeTypeApp):
            self.code_hint = "Telegram отправил код в приложение. Открой российский аккаунт."
        elif isinstance(sent.type, SentCodeTypeSms):
            self.code_hint = "Telegram отправил код по SMS на российский номер."
        elif isinstance(sent.type, SentCodeTypeEmailCode):
            self.code_hint = "Telegram отправил код на почту, привязанную к аккаунту."
        else:
            raise ValueError("unsupported_code_delivery")
        self.code_length = sent.type.length
        for attempt in range(3):
            self.state = "code_retry" if attempt else "code"
            value = await self.code.get()
            try:
                await client.sign_in(phone=phone, code=value, phone_code_hash=sent.phone_code_hash)
                return
            except PhoneCodeInvalidError:
                await asyncio.sleep(1)
            finally:
                del value
        raise ValueError("code_attempts")

    async def sign_in_by_qr(self, client: Any) -> None:
        token = await client.qr_login()
        waiter = asyncio.create_task(token.wait())
        try:
            await asyncio.sleep(0)
            image = io.BytesIO()
            import_module("qrcode").make(token.url).save(image, format="PNG")
            self.image = image.getvalue()
            self.state = "qr"
            await waiter
        finally:
            if not waiter.done():
                waiter.cancel()
                with suppress(asyncio.CancelledError):
                    await waiter
            self.image = None

    async def run(self, method: Literal["qr", "code"] = "qr") -> None:
        client = TelegramClient(
            StringSession(),
            self.config.application.api_id,
            self.config.application.api_hash,
            receive_updates=True,
            catch_up=False,
            auto_reconnect=False,
            request_retries=0,
            connection_retries=1,
            flood_sleep_threshold=0,
            timeout=5,
            device_model="Gate RU S2",
            app_version="S2 enrollment",
        )
        stored = False
        acted = False
        try:
            async with asyncio.timeout(180):
                await client.connect()
                try:
                    if method == "code":
                        await self.sign_in_by_code(client)
                    else:
                        await self.sign_in_by_qr(client)
                    acted = True
                except SessionPasswordNeededError:
                    acted = True
                    for _ in range(3):
                        self.state = "password"
                        value = await self.password.get()
                        try:
                            await client.sign_in(password=value)
                        except PasswordHashInvalidError:
                            await asyncio.sleep(1)
                            continue
                        finally:
                            del value
                        break
                    else:
                        raise ValueError("password_attempts")
                owner = verify_owner(await client.get_me(), self.config.phone)
                save_envelope(self.config, owner_id=owner, session=client.session.save())
                stored = True
                self.state = "bound"
                LOG.info("s2_ru_mtproto_owner_verified")
        except (TimeoutError, PhoneCodeExpiredError):
            self.state = "expired"
        except FloodWaitError as exc:
            self.next_code_after = max(self.next_code_after, monotonic() + exc.seconds)
            self.state = "code_throttled"
        except ValueError as exc:
            self.state = {
                "owner_mismatch": "mismatch",
                "unsupported_code_delivery": "unsupported_code_delivery",
                "code_attempts": "code_failed",
            }.get(str(exc), "failed")
        except Exception:
            self.state = "failed"
        finally:
            if acted and not stored:
                with suppress(Exception):
                    await asyncio.wait_for(client.log_out(), timeout=7)
            with suppress(Exception):
                await asyncio.wait_for(client.disconnect(), timeout=7)
            self.image = None
            self.code_length = 0
            self.code_hint = ""
            if not self.code.empty():
                self.code.get_nowait()
            if not self.password.empty():
                self.password.get_nowait()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = Config.read()
    app.state.enrollment = Enrollment(config) if config else None
    probe = None
    run_id = os.getenv("GATE_S2_VERIFY_SAVED_SESSION", "")
    if config is not None and run_id:
        probe = asyncio.create_task(verify_saved_session(config, run_id))
    try:
        yield
    finally:
        current = app.state.enrollment
        if current and current.task and not current.task.done():
            current.task.cancel()
            with suppress(asyncio.CancelledError):
                await current.task
        if probe is not None and not probe.done():
            probe.cancel()
            with suppress(asyncio.CancelledError):
                await probe


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def enrollment(request: Request) -> Enrollment | None:
    current: Enrollment | None = request.app.state.enrollment
    return current


def active(request: Request) -> Enrollment | None:
    current = enrollment(request)
    if current and current.authorized(request.cookies.get(COOKIE, "")):
        return current
    return None


async def form_value(request: Request, current: Enrollment, name: str) -> str:
    if (
        request.headers.get("origin") != current.config.origin
        or request.headers.get("content-type") != "application/x-www-form-urlencoded"
    ):
        return ""
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 4096:
            return ""
    try:
        value = parse_qs(body.decode("utf-8"), max_num_fields=1).get(name, [""])[0]
        return value if len(value) <= 256 else ""
    except (UnicodeError, ValueError):
        return ""


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def index(request: Request) -> Response:
    current = enrollment(request)
    if current is None:
        return page("Стенд запущен. Ожидает защищённые настройки Railway.")
    if active(request) is None:
        return page("Доступ владельца. Для входа по коду достаточно одного телефона.", ACCESS)
    if current.enrolled:
        return page("Российский аккаунт подтверждён Telegram. Сессия сохранена зашифрованной.")
    if current.state == "qr":
        return page(
            "В российском Telegram открой «Настройки → Устройства → Подключить устройство».",
            '<img src="/qr" alt="QR подключения"><p><a href="/">Проверить результат</a></p>',
            refresh=True,
        )
    if current.state == "password":
        return page("Telegram запросил пароль двухэтапной проверки.", PASSWORD)
    if current.state in {"code", "code_retry"}:
        message = (
            "Код не подошёл. Сверь его с сообщением Telegram."
            if (current.state == "code_retry")
            else current.code_hint
        )
        return page(message, CODE)
    if current.task and not current.task.done():
        return page("Telegram обрабатывает запрос.", refresh=True)
    return page(
        {
            "ready": "Подключение российского аккаунта.",
            "expired": "Время подтверждения истекло. Запусти вход заново.",
            "mismatch": "Telegram подтвердил другой номер. Проверь активный аккаунт на телефоне.",
            "failed": "Telegram не подтвердил вход. Можно повторить.",
            "unsupported_code_delivery": "Telegram предложил другой способ входа. Выбери QR.",
            "code_throttled": "Telegram ограничил запросы кода. Подожди перед повтором.",
            "code_failed": "Три введённых кода не подошли. Запроси новый код.",
        }.get(current.state, "Подключение недоступно."),
        START,
    )


@app.post("/unlock")
async def unlock(request: Request) -> Response:
    current = enrollment(request)
    if current is None:
        return page("Настройки отсутствуют.", status=503)
    if request.headers.get("origin") != current.config.origin:
        return page("Обнови страницу Gate в браузере и повтори вход.", ACCESS, status=403)
    value = (await form_value(request, current, "access")).strip()
    if not value:
        return page("Ключ не принят.", ACCESS, status=403)
    async with current.lock:
        if monotonic() < current.unlock_after:
            return page("Повтори через несколько секунд.", status=429)
        current.unlock_after = monotonic() + 3
        if not secrets.compare_digest(current.config.access.encode(), value.encode()):
            return page("Ключ не принят.", ACCESS, status=403)
        response = RedirectResponse("/", status_code=303, headers=HEADERS)
        current.cookie = secrets.token_urlsafe(32)
        current.deadline = monotonic() + 600
        response.set_cookie(
            COOKIE, current.cookie, max_age=600, secure=True, httponly=True, samesite="strict"
        )
        return response


@app.post("/start")
@app.post("/start-code")
async def start(request: Request) -> Response:
    current = active(request)
    if current is None or request.headers.get("origin") != current.config.origin:
        return page("Открой стенд и введи ключ доступа.", status=403)
    method: Literal["qr", "code"] = "code" if request.url.path == "/start-code" else "qr"
    async with current.lock:
        if current.enrolled:
            return RedirectResponse("/", status_code=303, headers=HEADERS)
        if current.task and not current.task.done():
            return RedirectResponse("/", status_code=303, headers=HEADERS)
        if current.attempts >= 4 or current.deadline - monotonic() < 180:
            return page("Окно проверки закрыто.", status=409)
        if method == "code":
            now = monotonic()
            if now < current.next_code_after:
                return page("Подожди перед следующим запросом кода.", status=429)
            current.next_code_after = now + 60
        current.attempts += 1
        current.state = "starting"
        current.task = asyncio.create_task(current.run(method))
    return RedirectResponse("/", status_code=303, headers=HEADERS)


@app.get("/qr")
async def qr(request: Request) -> Response:
    current = active(request)
    if current is None or current.image is None or current.state != "qr":
        return Response(status_code=404, headers=HEADERS)
    return Response(current.image, media_type="image/png", headers=HEADERS)


@app.post("/code")
async def confirm_code(request: Request) -> Response:
    current = active(request)
    if current is None:
        return page("Сеанс истёк.", status=403)
    value = await form_value(request, current, "code")
    async with current.lock:
        if current.state not in {"code", "code_retry"} or current.code.full():
            return page("Подтверждение пока недоступно.", status=409)
        if not re.fullmatch(rf"[0-9]{{{current.code_length}}}", value):
            return page("Проверь число цифр в коде.", CODE, status=400)
        current.state = "verifying"
        current.code.put_nowait(value)
    return RedirectResponse("/", status_code=303, headers=HEADERS)


@app.post("/confirm")
async def confirm(request: Request) -> Response:
    current = active(request)
    if current is None:
        return page("Сеанс истёк.", status=403)
    value = await form_value(request, current, "password")
    async with current.lock:
        if not value or current.state != "password" or current.password.full():
            return page("Подтверждение пока недоступно.", status=409)
        current.state = "verifying"
        current.password.put_nowait(value)
    return RedirectResponse("/", status_code=303, headers=HEADERS)
