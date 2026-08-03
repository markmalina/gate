"""Private Dropbox storage used by the Alfa transcription product."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from app.application.alpha_transcription_product import AlphaTranscriptionError
from app.core.config import Settings

_DROPBOX_API = "https://api.dropboxapi.com/2"
_DROPBOX_CONTENT_API = "https://content.dropboxapi.com/2"
_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class DropboxEntry:
    path: str
    name: str
    size_bytes: int


class DropboxTranscriptionStorage:
    def __init__(self, *, settings: Settings, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http

    @property
    def ready(self) -> bool:
        return bool(
            self._settings.dropbox_call_app_key
            and self._settings.dropbox_call_app_secret.get_secret_value()
            and self._settings.dropbox_call_refresh_token.get_secret_value()
            and self._settings.dropbox_call_account_id
        )

    async def stat(self, path: str) -> DropboxEntry:
        token = await self._access_token()
        response = await self._post_json(
            f"{_DROPBOX_API}/files/get_metadata",
            token=token,
            payload={"path": path, "include_deleted": False},
        )
        if response.get(".tag") != "file":
            raise AlphaTranscriptionError("alpha_transcription_source_not_file")
        return _entry(response)

    async def download_to_file(self, path: str, destination: Path) -> int:
        token = await self._access_token()
        try:
            async with self._http.stream(
                "POST",
                f"{_DROPBOX_CONTENT_API}/files/download",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Dropbox-API-Arg": json.dumps(
                        {"path": path}, separators=(",", ":")
                    ),
                },
                timeout=300.0,
            ) as response:
                if response.is_error:
                    raise AlphaTranscriptionError(_storage_error(response, "download"))
                total = 0
                with destination.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        output.write(chunk)
        except httpx.HTTPError:
            raise AlphaTranscriptionError(
                "alpha_transcription_storage_download_unknown"
            ) from None
        if total <= 0:
            raise AlphaTranscriptionError("alpha_transcription_source_empty")
        return total

    async def download_bytes(self, path: str, *, maximum: int = 10_000_000) -> bytes:
        token = await self._access_token()
        try:
            response = await self._http.post(
                f"{_DROPBOX_CONTENT_API}/files/download",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Dropbox-API-Arg": json.dumps(
                        {"path": path}, separators=(",", ":")
                    ),
                },
                timeout=120.0,
            )
        except httpx.HTTPError:
            raise AlphaTranscriptionError(
                "alpha_transcription_storage_download_unknown"
            ) from None
        if response.is_error:
            raise AlphaTranscriptionError(_storage_error(response, "download"))
        if not response.content or len(response.content) > maximum:
            raise AlphaTranscriptionError("alpha_transcription_artifact_size_invalid")
        return response.content

    async def upload_bytes(self, path: str, data: bytes) -> DropboxEntry:
        if not data:
            raise AlphaTranscriptionError("alpha_transcription_artifact_empty")
        token = await self._access_token()
        await self._ensure_parent(path, token=token)
        try:
            response = await self._http.post(
                f"{_DROPBOX_CONTENT_API}/files/upload",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/octet-stream",
                    "Dropbox-API-Arg": json.dumps(
                        {
                            "path": path,
                            "mode": "overwrite",
                            "autorename": False,
                            "mute": True,
                            "strict_conflict": False,
                        },
                        separators=(",", ":"),
                    ),
                },
                content=data,
                timeout=120.0,
            )
        except httpx.HTTPError:
            raise AlphaTranscriptionError(
                "alpha_transcription_storage_upload_unknown"
            ) from None
        if response.is_error:
            raise AlphaTranscriptionError(_storage_error(response, "upload"))
        return _entry(response.json())

    async def upload_file(self, path: str, source: Path) -> DropboxEntry:
        size = source.stat().st_size
        if size <= 0:
            raise AlphaTranscriptionError("alpha_transcription_source_empty")
        token = await self._access_token()
        await self._ensure_parent(path, token=token)
        if size <= _UPLOAD_CHUNK_BYTES:
            return await self.upload_bytes(path, source.read_bytes())

        with source.open("rb") as stream:
            first = stream.read(_UPLOAD_CHUNK_BYTES)
            session_id = await self._start_upload_session(token=token, data=first)
            offset = len(first)
            while size - offset > _UPLOAD_CHUNK_BYTES:
                chunk = stream.read(_UPLOAD_CHUNK_BYTES)
                await self._append_upload_session(
                    token=token,
                    session_id=session_id,
                    offset=offset,
                    data=chunk,
                )
                offset += len(chunk)
            tail = stream.read()
            return await self._finish_upload_session(
                token=token,
                session_id=session_id,
                offset=offset,
                path=path,
                data=tail,
            )

    async def _access_token(self) -> str:
        if not self.ready:
            raise AlphaTranscriptionError(
                "alpha_transcription_storage_not_configured"
            )
        try:
            response = await self._http.post(
                "https://api.dropboxapi.com/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": (
                        self._settings.dropbox_call_refresh_token.get_secret_value()
                    ),
                },
                auth=(
                    self._settings.dropbox_call_app_key,
                    self._settings.dropbox_call_app_secret.get_secret_value(),
                ),
                timeout=30.0,
            )
        except httpx.HTTPError:
            raise AlphaTranscriptionError(
                "alpha_transcription_storage_oauth_unknown"
            ) from None
        if response.is_error:
            raise AlphaTranscriptionError(
                "alpha_transcription_storage_oauth_rejected"
            )
        try:
            payload: Any = response.json()
        except ValueError:
            payload = None
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise AlphaTranscriptionError("alpha_transcription_storage_oauth_invalid")
        return token

    async def _ensure_parent(self, path: str, *, token: str) -> None:
        parent = PurePosixPath(path).parent
        current = PurePosixPath("/")
        for part in parent.parts[1:]:
            current /= part
            try:
                await self._post_json(
                    f"{_DROPBOX_API}/files/create_folder_v2",
                    token=token,
                    payload={"path": current.as_posix(), "autorename": False},
                )
            except AlphaTranscriptionError as exc:
                if exc.code != "alpha_transcription_storage_create_folder_conflict":
                    raise

    async def _post_json(
        self,
        url: str,
        *,
        token: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            response = await self._http.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=60.0,
            )
        except httpx.HTTPError:
            raise AlphaTranscriptionError(
                "alpha_transcription_storage_transport_unknown"
            ) from None
        if response.is_error:
            operation = "create_folder" if "create_folder" in url else "request"
            raise AlphaTranscriptionError(_storage_error(response, operation))
        try:
            body: Any = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise AlphaTranscriptionError(
                "alpha_transcription_storage_response_invalid"
            )
        return body

    async def _start_upload_session(self, *, token: str, data: bytes) -> str:
        response = await self._content_call(
            "/files/upload_session/start",
            token=token,
            argument={"close": False},
            data=data,
        )
        session_id = response.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise AlphaTranscriptionError(
                "alpha_transcription_storage_session_invalid"
            )
        return session_id

    async def _append_upload_session(
        self,
        *,
        token: str,
        session_id: str,
        offset: int,
        data: bytes,
    ) -> None:
        await self._content_call(
            "/files/upload_session/append_v2",
            token=token,
            argument={
                "cursor": {"session_id": session_id, "offset": offset},
                "close": False,
            },
            data=data,
        )

    async def _finish_upload_session(
        self,
        *,
        token: str,
        session_id: str,
        offset: int,
        path: str,
        data: bytes,
    ) -> DropboxEntry:
        payload = await self._content_call(
            "/files/upload_session/finish",
            token=token,
            argument={
                "cursor": {"session_id": session_id, "offset": offset},
                "commit": {
                    "path": path,
                    "mode": "overwrite",
                    "autorename": False,
                    "mute": True,
                    "strict_conflict": False,
                },
            },
            data=data,
            timeout=300.0,
        )
        return _entry(payload)

    async def _content_call(
        self,
        endpoint: str,
        *,
        token: str,
        argument: dict[str, Any],
        data: bytes,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        try:
            response = await self._http.post(
                f"{_DROPBOX_CONTENT_API}{endpoint}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/octet-stream",
                    "Dropbox-API-Arg": json.dumps(
                        argument, separators=(",", ":")
                    ),
                },
                content=data,
                timeout=timeout,
            )
        except httpx.HTTPError:
            raise AlphaTranscriptionError(
                "alpha_transcription_storage_upload_unknown"
            ) from None
        if response.is_error:
            raise AlphaTranscriptionError(_storage_error(response, "upload"))
        try:
            payload: Any = response.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise AlphaTranscriptionError(
                "alpha_transcription_storage_response_invalid"
            )
        return payload


def _entry(payload: Any) -> DropboxEntry:
    if not isinstance(payload, dict):
        raise AlphaTranscriptionError("alpha_transcription_storage_response_invalid")
    path = payload.get("path_display")
    name = payload.get("name")
    size = payload.get("size")
    if not isinstance(path, str) or not isinstance(name, str) or not isinstance(size, int):
        raise AlphaTranscriptionError("alpha_transcription_storage_response_invalid")
    return DropboxEntry(path=path, name=name, size_bytes=size)


def _storage_error(response: httpx.Response, operation: str) -> str:
    if operation == "create_folder" and response.status_code == 409:
        return "alpha_transcription_storage_create_folder_conflict"
    if response.status_code == 409:
        return f"alpha_transcription_storage_{operation}_conflict"
    if response.status_code in {401, 403}:
        return "alpha_transcription_storage_authorization_rejected"
    if response.status_code >= 500:
        return f"alpha_transcription_storage_{operation}_unknown"
    return f"alpha_transcription_storage_{operation}_rejected"
