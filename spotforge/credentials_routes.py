"""Credential endpoints deliberately parse bodies without echoing validation input."""
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from spotforge.credentials import CredentialError, get_credentials


def create_router(store=None, *, credentials=None):
    # The store is accepted for consistent integration, but never receives keys.
    router = APIRouter()
    vault = credentials if credentials is not None else get_credentials()

    def answer(action):
        try:
            return JSONResponse(action(), headers={"Cache-Control": "no-store"})
        except CredentialError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get("/providers/fal")
    def status():
        return answer(vault.status)

    @router.put("/providers/fal")
    async def configure(request: Request):
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 8192:
                raise HTTPException(413, "Schlüssel-Anfrage ist zu groß.")
        try:
            body = json.loads(content)
            if not isinstance(body, dict) or set(body) != {"key", "persistence"}:
                raise ValueError()
            if not isinstance(body["key"], str) or body["persistence"] not in {"session", "keychain"}:
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise HTTPException(422, "Erwartet werden key und persistence (keychain oder session).") from None
        return answer(lambda: vault.set(body["key"], body["persistence"]))

    @router.delete("/providers/fal")
    def remove():
        return answer(vault.delete)

    return router
