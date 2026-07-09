"""Falcon integration: hook factories + error-handler registration.

The hooks are **closure factories** (Shape A) — the caller binds a specific
:class:`~falcon_svcplane.svcplane.Verifier` at decoration time. This avoids
module-level state, keeps two verifiers in the same process independent, and
makes each route file's identity gate visible in its imports.

Typical wiring::

    # boot (app/http.py or equivalent)
    from falcon_svcplane import Verifier, build_allow_list
    from falcon_svcplane.hooks import register_error_handlers

    verifier = Verifier(build_allow_list(settings.svcplane.allow_list))
    register_error_handlers(http_app)

    # per route
    from falcon_svcplane.hooks import require_service_scope
    from app.services import verifier   # the singleton constructed at boot

    class InternalRevocationsRoute:
        @falcon.before(require_service_scope(verifier, "revocations.sessions:read"))
        async def on_get_sessions(self, req, resp): ...

The verifier stashes the authenticated :class:`~falcon_svcplane.svcplane.Principal`
on ``req.context.svcplane_principal`` — handlers that need to know *who*
called them can read it with :func:`principal_from_request`.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import falcon
import falcon.asgi

from .errors import SvcPlaneError
from .svcplane import Principal, Verifier


_PRINCIPAL_CTX_ATTR = "svcplane_principal"


HookFn = Callable[
    [falcon.asgi.Request, falcon.asgi.Response, object, dict],
    Awaitable[None],
]


def require_service_scope(verifier: Verifier, scope: str) -> HookFn:
    """Return a Falcon ``before`` hook that gates a SERVICE route on ``scope``.

    Fail-closed: no client cert →
    :class:`~falcon_svcplane.errors.MissingClientCertError` (401), unknown CN
    → :class:`~falcon_svcplane.errors.UnknownCNError` (403), missing scope →
    :class:`~falcon_svcplane.errors.MissingScopeError` (403).
    """

    async def hook(
        req: falcon.asgi.Request,
        resp: falcon.asgi.Response,
        resource: object,
        params: dict,
    ) -> None:
        principal = verifier.authenticate(req.scope)
        verifier.require_scope(principal, scope)
        setattr(req.context, _PRINCIPAL_CTX_ATTR, principal)

    return hook


def require_callback(verifier: Verifier) -> HookFn:
    """Return a Falcon ``before`` hook that gates a CALLBACK route.

    CALLBACK principals carry no scopes — the mere fact that the caller
    presented a known cert-bound identity is the whole authorization. Body is
    treated as data, not a command.
    """

    async def hook(
        req: falcon.asgi.Request,
        resp: falcon.asgi.Response,
        resource: object,
        params: dict,
    ) -> None:
        principal = verifier.authenticate(req.scope)
        setattr(req.context, _PRINCIPAL_CTX_ATTR, principal)

    return hook


def principal_from_request(req: falcon.asgi.Request) -> Principal | None:
    """Return the verified east-west principal on ``req``, or ``None``."""
    return getattr(req.context, _PRINCIPAL_CTX_ATTR, None)


async def render_svcplane_error(
    req: falcon.asgi.Request,
    resp: falcon.asgi.Response,
    ex: SvcPlaneError,
    params: dict,
) -> None:
    """Falcon error handler: render a :class:`SvcPlaneError` on the response.

    Emits ``application/json`` with the wire format hardened in
    :mod:`falcon_svcplane.errors` — uniform across every consuming repo.
    """
    resp.status = ex.http_status
    resp.media = ex.json()


def register_error_handlers(app: falcon.asgi.App) -> None:
    """Plumb :class:`SvcPlaneError` → the uniform response envelope.

    One line, called once at boot. All three east-west error subclasses share
    :class:`SvcPlaneError` as their base, so a single handler covers them.
    """
    app.add_error_handler(SvcPlaneError, render_svcplane_error)


__all__ = (
    "principal_from_request",
    "register_error_handlers",
    "render_svcplane_error",
    "require_callback",
    "require_service_scope",
)
