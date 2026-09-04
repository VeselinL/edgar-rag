"""OIDC session routes."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from src.auth.oidc import AuthenticationError
from src.backend.dependencies import RequestServices
from src.conversations.service import ConversationSettings


def create_router(
    conversation_settings: ConversationSettings,
    services: RequestServices,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/auth/session")
    async def auth_session(request: Request) -> dict[str, Any]:
        if conversation_settings.mode != "oidc":
            return {"mode": "none", "authenticated": True}
        auth = getattr(request.app.state, "auth", None)
        if auth is None:
            raise HTTPException(status_code=503, detail="Authentication is not available.")
        try:
            await asyncio.to_thread(
                auth.authenticate,
                request.cookies.get(auth.settings.cookie_name),
            )
        except AuthenticationError:
            return {"mode": "oidc", "authenticated": False}
        return {"mode": "oidc", "authenticated": True}

    @router.get("/api/auth/login")
    async def auth_login(request: Request, return_to: str = "/") -> RedirectResponse:
        auth = getattr(request.app.state, "auth", None)
        if conversation_settings.mode != "oidc" or auth is None:
            raise HTTPException(status_code=404, detail="Authentication is not enabled.")
        try:
            location = await asyncio.to_thread(
                auth.begin_login, return_to=return_to
            )
        except (AuthenticationError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return RedirectResponse(location, status_code=302)

    @router.get("/api/auth/callback")
    async def auth_callback(
        request: Request,
        state: str | None = None,
        code: str | None = None,
        error: str | None = None,
    ) -> RedirectResponse:
        auth = getattr(request.app.state, "auth", None)
        if conversation_settings.mode != "oidc" or auth is None:
            raise HTTPException(status_code=404, detail="Authentication is not enabled.")
        if error or not state or not code:
            raise HTTPException(status_code=401, detail="Sign-in was not completed.")
        try:
            authenticated = await asyncio.to_thread(
                auth.complete_login, state=state, code=code
            )
        except AuthenticationError as failure:
            raise HTTPException(status_code=401, detail=str(failure)) from failure
        response = RedirectResponse(authenticated.return_to, status_code=303)
        maximum_age = auth.settings.session_ttl_seconds
        response.set_cookie(
            auth.settings.cookie_name,
            authenticated.token,
            max_age=maximum_age,
            httponly=True,
            secure=auth.settings.cookie_secure,
            samesite=auth.settings.cookie_same_site,
            path="/",
        )
        response.set_cookie(
            auth.settings.csrf_cookie_name,
            authenticated.csrf_token,
            max_age=maximum_age,
            httponly=False,
            secure=auth.settings.cookie_secure,
            samesite=auth.settings.cookie_same_site,
            path="/",
        )
        return response

    @router.post("/api/auth/logout", status_code=204)
    async def auth_logout(request: Request) -> Response:
        auth = getattr(request.app.state, "auth", None)
        if conversation_settings.mode != "oidc" or auth is None:
            raise HTTPException(status_code=404, detail="Authentication is not enabled.")
        await services.conversation_service_for(request, require_csrf=True)
        await asyncio.to_thread(
            auth.logout, request.cookies.get(auth.settings.cookie_name)
        )
        response = Response(status_code=204)
        response.delete_cookie(auth.settings.cookie_name, path="/")
        response.delete_cookie(auth.settings.csrf_cookie_name, path="/")
        return response

    return router
