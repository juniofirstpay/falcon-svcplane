# falcon-svcplane

East-west trust boundary for Falcon services: in-process mTLS termination + CN-based allow-list gating.

The service process terminates mTLS itself, one listener serves both the U-plane (end-user, JWT, no client cert) and the east-west S/K planes (client cert → allow-list → `noun:verb` scope), and the two planes share a port.

- **No mesh sidecar.** The service process is the mTLS endpoint. No `X-Forwarded-Client-Cert` header trust.
- **One listener, `CERT_OPTIONAL`.** A presented-but-unverifiable client cert fails the handshake; absence is fine at TLS, svcplane fail-closes east-west routes.
- **Fail-closed by default.** Empty allow-list → every east-west route 403s until deploy config wires the CN → scope map.
- **Framework-agnostic core.** `Verifier` takes an ASGI `scope` dict, not a Falcon `Request`. Falcon integration lives in `falcon_svcplane.hooks`.

---

## Install

Pipfile:

```
falcon-svcplane = { git = "https://github.com/juniofirstpay/falcon-svcplane.git" }
```

Dependencies (all pulled transitively): `falcon >= 4.0`, `uvicorn >= 0.30`, `cryptography >= 42`.

---

## Integration recipe (5 parts, ~20 lines of glue)

### 1. Settings — two sections

```yaml
tls:
  cert_file: ""       # empty → uvicorn plaintext, existing dev/tests unchanged
  key_file: ""
  client_ca_file: ""

svcplane:
  allow_list: []      # [{cn, kind, source, scopes}]
```

Deploy sets the paths + populates the allow-list per environment. Empty defaults are safe: the service boots plaintext, and any east-west route emits `MissingClientCertError` (401) until certs + allow-list arrive.

### 2. Uvicorn wiring — CLI entry point

```python
from falcon_svcplane import (
    PeerCertH11Protocol,
    PeerCertHttpToolsProtocol,
    build_uvicorn_ssl_kwargs,
)

ssl_kwargs = build_uvicorn_ssl_kwargs(
    settings.tls.cert_file,
    settings.tls.key_file,
    settings.tls.client_ca_file,
)
http_protocol = (
    (PeerCertHttpToolsProtocol or PeerCertH11Protocol) if ssl_kwargs else "auto"
)

uvicorn.Config(
    app=http_app,
    host="0.0.0.0",
    port=settings.http.port,
    http=http_protocol,
    **ssl_kwargs,
)
```

`build_uvicorn_ssl_kwargs("", "", "")` returns `{}` → uvicorn's default protocol runs, no peer-cert injection, no behavioral change vs. plaintext. `PeerCertHttpToolsProtocol` is `None` unless you install `uvicorn[standard]` (which pulls `httptools`); the `or` fallback picks the h11 variant otherwise.

### 3. Verifier construction + error handlers — boot-time, one place

```python
from falcon_svcplane import Verifier, build_allow_list
from falcon_svcplane.hooks import register_error_handlers

verifier = Verifier(build_allow_list(list(settings.svcplane.allow_list)))
register_error_handlers(http_app)
```

Consumers whose 9xxx error-code band is already taken pass their own codes:

```python
from falcon_svcplane import SvcPlaneErrorCodes
verifier = Verifier(
    build_allow_list(list(settings.svcplane.allow_list)),
    codes=SvcPlaneErrorCodes(missing_cert=5000, unknown_cn=5001, missing_scope=5002),
)
```

Export `verifier` from wherever your services live (e.g. `app/services/__init__.py`) so route files can import it.

### 4. Route decorators — one line per responder

```python
import falcon
from falcon_svcplane.hooks import require_service_scope
from app.services import verifier

class InternalRevocationsRoute:
    @falcon.before(require_service_scope(verifier, "revocations.sessions:read"))
    async def on_get_sessions(self, req, resp): ...
```

Scope name choice is per-route policy — it lives in the route file, next to the handler it protects.

For rail/TSP callbacks (CALLBACK principals — no scope check, cert-bound identity is the whole authorization):

```python
from falcon_svcplane.hooks import require_callback

class RailCallbackRoute:
    @falcon.before(require_callback(verifier))
    async def on_post(self, req, resp): ...
```

### 5. Reading the verified principal (optional)

Handlers that need to know *who* called them:

```python
from falcon_svcplane.hooks import principal_from_request

async def on_post(self, req, resp):
    principal = principal_from_request(req)   # Principal | None
    logger.info("east-west call", cn=principal.cn, source=principal.source)
```

---

## The uniform error wire shape

Every consuming repo emits the same envelope for these three conditions. `title` / `http_status` / `description` are hardened by the package; only the numeric `code` is consumer-overridable.

| Condition | HTTP | title | description | extras |
|---|---|---|---|---|
| No client cert (TLS off, or `CERT_OPTIONAL` + absent) | 401 | `MissingClientCertError` | `east-west plane requires a client certificate (mTLS)` | — |
| Trusted CA, CN not in allow-list | 403 | `UnknownCNError` | `certificate CN is not in the allow-list` | `{cn}` |
| Authenticated, missing `noun:verb` grant | 403 | `MissingScopeError` | `certificate identity is not granted <scope>` | `{scope}` |

Default numeric codes are `9000` / `9001` / `9002`. Override per-consumer via `SvcPlaneErrorCodes`.

Example wire:

```json
{
  "code": 9001,
  "title": "UnknownCNError",
  "description": "certificate CN is not in the allow-list",
  "extras": {"cn": "stranger.svc"}
}
```

---

## Testing your integration

The package ships a smoke pattern that any consumer repo can adapt:

1. **openssl** generates a CA + server cert + at least three client certs: one whose CN is in your allow-list with the required scope, one whose CN is in the allow-list with the *wrong* scope, one whose CN is not in the allow-list at all, plus one client cert signed by a *different* CA to exercise the TLS handshake rejection path.
2. Boot with `tls.cert_file` / `tls.key_file` / `tls.client_ca_file` set and the allow-list populated.
3. `curl -v --cacert ca.crt --cert client.crt --key client.key https://…/internal/…` — cover each of the six code paths:

| # | Scenario | Expected |
|---|---|---|
| 1 | U-plane route, no client cert | 200 (one listener serves both planes) |
| 2 | East-west route, no client cert | 401 `MissingClientCertError` |
| 3 | Client cert signed by untrusted CA | TLS handshake fails, no HTTP |
| 4 | Trusted CA, unknown CN | 403 `UnknownCNError` |
| 5 | Known CN, wrong scope | 403 `MissingScopeError` |
| 6 | Known CN, right scope | passes through to the handler |

---

## Public API

Everything a consumer needs is re-exported at the top level of `falcon_svcplane`; the submodule tree is an implementation detail. The Falcon integration lives at `falcon_svcplane.hooks` and is imported explicitly (`from falcon_svcplane.hooks import ...`) because it's the wire-up point, not the primitives.

```python
from falcon_svcplane import (
    # settings + primitives
    build_allow_list, AllowList, Principal, Verifier,
    KIND_SERVICE, KIND_CALLBACK,
    peer_cn,

    # errors
    SvcPlaneError, SvcPlaneErrorCodes,
    MissingClientCertError, UnknownCNError, MissingScopeError,

    # uvicorn wiring
    build_uvicorn_ssl_kwargs,
    PeerCertH11Protocol, PeerCertHttpToolsProtocol,
)

from falcon_svcplane.hooks import (
    require_service_scope, require_callback,
    register_error_handlers,
    principal_from_request,
    render_svcplane_error,
)
```

---

## Design notes

### Why the Verifier is framework-agnostic

`Verifier.authenticate(scope: dict)` and `Verifier.require_scope(principal, scope: str)` take primitive types, not Falcon `Request` / `Response`. This lets the same core drive Starlette, FastAPI, or an aiohttp adapter with a ~15-line hook module per framework. The `falcon_svcplane.hooks` module is one such adapter; it's the only place where `import falcon` appears in the runtime path.

### Why hooks are closure factories, not module-level state

`require_service_scope(verifier, scope)` returns a fresh hook function per call, closing over the specific `Verifier` instance. This means:

- Two apps in the same process can each have their own `Verifier`.
- Tests instantiate a fresh `Verifier` per test — no global reset dance.
- The route file's identity gate is visible in its imports: `from app.services import verifier` right next to `from falcon_svcplane.hooks import require_service_scope`.

The cost is one extra import per route file — negligible next to the win of no hidden globals.

### Deferred, with triggers

- **Posture B** (mesh sidecar terminating mTLS, `TRUST_PROXY_IDENTITY` header adapter). Not implemented — introduce when a mesh appears in the topology.
- **Server-cert hot-reload.** Certs load at boot; rotation requires a pod restart. Acceptable for infrequently-rotated mTLS certs.

---

## Development

```
python3.13 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

51 tests cover the mtls kwargs builder, peer-cert capture + injection, the `__setattr__` hook on the uvicorn protocol subclass, allow-list construction, all fail-closed paths in the Verifier, the hardened error wire shape, code-override behavior, and the Shape A hook factory (including two-verifiers-in-one-process independence).
