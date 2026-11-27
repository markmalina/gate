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
from typing import Any
from urllib.parse import parse_qs

from cryptography.fernet import Fernet
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from telethon import TelegramClient  # type: ignore[import-untyped]
from telethon.errors import (  # type: ignore[import-untyped]
    PasswordHashInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession  # type: ignore[import-untyped]

from app.personal_telegram.cloud_session import SCHEMA
from app.personal_telegram.cloud_worker import TelegramApplication

COOKIE = "__Host-gate-s2"
LOG = logging.getLogger("uvicorn.error")
PROJECT_ID = "4d14a47e-0f3c-47cf-9eb9-893fae7e2044"
VAULT = Path("/gate-vault")
ENVELOPE = "ru-session.bin"
HEADERS = {
    "Cache-Control": "no-store, private",
    "Content-Security-Policy": (
        "default-src 'none'; img-src 'self'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'; style-src 'unsafe-inline'"
    ),
    "Referrer-Policy": "no-referrer",
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
START = '<form method="post" action="/start"><button>Показать QR</button></form>'
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
        "session_ref": "session_gate_s2_ru",
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


class Enrollment:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.lock = asyncio.Lock()
        self.cookie = ""
        self.deadline = 0.0
        self.unlock_after = 0.0
        self.attempts = 0
        self.state = "ready"
        self.image: bytes | None = None
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

    async def run(self) -> None:
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
                token = await client.qr_login()
                waiter = asyncio.create_task(token.wait())
                try:
                    await asyncio.sleep(0)
                    image = io.BytesIO()
                    import_module("qrcode").make(token.url).save(image, format="PNG")
                    self.image = image.getvalue()
                    self.state = "qr"
                    await waiter
                    acted = True
                except SessionPasswordNeededError:
                    acted = True
                    self.image = None
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
                finally:
                    if not waiter.done():
                        waiter.cancel()
                        with suppress(asyncio.CancelledError):
                            await waiter
                    self.image = None
                owner = verify_owner(await client.get_me(), self.config.phone)
                save_envelope(self.config, owner_id=owner, session=client.session.save())
                stored = True
                self.state = "bound"
                LOG.info("s2_ru_mtproto_owner_verified")
        except TimeoutError:
            self.state = "expired"
        except ValueError as exc:
            self.state = "mismatch" if str(exc) == "owner_mismatch" else "failed"
        except Exception:
            self.state = "failed"
        finally:
            if acted and not stored:
                with suppress(Exception):
                    await asyncio.wait_for(client.log_out(), timeout=7)
            with suppress(Exception):
                await asyncio.wait_for(client.disconnect(), timeout=7)
            self.image = None
            if not self.password.empty():
                self.password.get_nowait()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = Config.read()
    app.state.enrollment = Enrollment(config) if config else None
    try:
        yield
    finally:
        current = app.state.enrollment
        if current and current.task and not current.task.done():
            current.task.cancel()
            with suppress(asyncio.CancelledError):
                await current.task


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
        return page("Доступ владельца", ACCESS)
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
    if current.task and not current.task.done():
        return page("Telegram обрабатывает запрос.", refresh=True)
    return page(
        {
            "ready": "Подключение российского аккаунта.",
            "expired": "Время QR истекло. Можно создать новый QR.",
            "mismatch": "Telegram подтвердил другой номер. Проверь активный аккаунт на телефоне.",
            "failed": "Telegram не подтвердил вход. Можно повторить один раз.",
        }.get(current.state, "Подключение недоступно."),
        START,
    )


@app.post("/unlock")
async def unlock(request: Request) -> Response:
    current = enrollment(request)
    if current is None:
        return page("Настройки отсутствуют.", status=503)
    value = await form_value(request, current, "access")
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
async def start(request: Request) -> Response:
    current = active(request)
    if current is None or request.headers.get("origin") != current.config.origin:
        return page("Открой стенд и введи ключ доступа.", status=403)
    async with current.lock:
        if current.enrolled:
            return RedirectResponse("/", status_code=303, headers=HEADERS)
        if current.task and not current.task.done():
            return RedirectResponse("/", status_code=303, headers=HEADERS)
        if current.attempts >= 4 or current.deadline - monotonic() < 180:
            return page("Окно проверки закрыто.", status=409)
        current.attempts += 1
        current.state = "starting"
        current.task = asyncio.create_task(current.run())
    return RedirectResponse("/", status_code=303, headers=HEADERS)


@app.get("/qr")
async def qr(request: Request) -> Response:
    current = active(request)
    if current is None or current.image is None or current.state != "qr":
        return Response(status_code=404, headers=HEADERS)
    return Response(current.image, media_type="image/png", headers=HEADERS)


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
