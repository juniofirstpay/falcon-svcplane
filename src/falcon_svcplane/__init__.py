"""falcon-svcplane — east-west trust boundary for Falcon services.

Public API: everything a consumer needs is re-exported at the top level; the
submodule tree is an implementation detail (except :mod:`falcon_svcplane.hooks`
which consumers use directly for the Falcon integration).
"""

from __future__ import annotations

from .errors import (
    MissingClientCertError,
    MissingScopeError,
    SvcPlaneError,
    SvcPlaneErrorCodes,
    UnknownCNError,
)
from .mtls import (
    PeerCertH11Protocol,
    PeerCertHttpToolsProtocol,
    build_uvicorn_ssl_kwargs,
)
from .svcplane import (
    KIND_CALLBACK,
    KIND_SERVICE,
    AllowList,
    Principal,
    Verifier,
    build_allow_list,
    peer_cn,
)

__version__ = "0.1.0"

__all__ = (
    "AllowList",
    "KIND_CALLBACK",
    "KIND_SERVICE",
    "MissingClientCertError",
    "MissingScopeError",
    "PeerCertH11Protocol",
    "PeerCertHttpToolsProtocol",
    "Principal",
    "SvcPlaneError",
    "SvcPlaneErrorCodes",
    "UnknownCNError",
    "Verifier",
    "__version__",
    "build_allow_list",
    "build_uvicorn_ssl_kwargs",
    "peer_cn",
)
