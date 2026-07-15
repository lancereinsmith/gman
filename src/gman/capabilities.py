"""Token classification and capability tracking for graceful degradation.

Classic tokens (``ghp_``/``gho_``) announce their powers in the
``X-OAuth-Scopes`` response header. Fine-grained tokens (``github_pat_``)
have no introspection — permissions are learned by observing 403s.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TokenKind = Literal["classic", "fine-grained", "unknown"]

READ_FAMILIES: tuple[str, ...] = (
    "contents.read",
    "actions.read",
    "pages.read",
    "admin.read",
    "pulls.read",
    "dependabot.read",
    "secret_scanning.read",
)
WRITE_FAMILIES: tuple[str, ...] = (
    "contents.write",
    "actions.write",
    "admin.write",
    "delete",
)
ALL_FAMILIES: tuple[str, ...] = READ_FAMILIES + WRITE_FAMILIES

FAMILY_HINTS: dict[str, str] = {
    "metadata.read": "included with every token",
    "contents.read": "needs Contents: read (fine-grained) or repo scope (classic)",
    "actions.read": "needs Actions: read (fine-grained) or repo scope (classic)",
    "pages.read": "needs Pages: read (fine-grained) or repo scope (classic)",
    "admin.read": (
        "needs Administration: read (fine-grained) or repo scope (classic); "
        "traffic also needs push access"
    ),
    "pulls.read": "needs Pull requests: read (fine-grained) or repo scope (classic)",
    "dependabot.read": (
        "needs Dependabot alerts: read (fine-grained) or security_events/repo scope (classic)"
    ),
    "secret_scanning.read": (
        "needs Secret scanning alerts: read (fine-grained) or security_events/repo scope (classic)"
    ),
    "contents.write": "needs Contents: write (fine-grained) or repo scope (classic)",
    "actions.write": "needs Actions: write (fine-grained) or repo scope (classic)",
    "admin.write": "needs Administration: write (fine-grained) or repo scope (classic)",
    "delete": "needs Administration: write (fine-grained) or delete_repo scope (classic)",
}


def classify_token(token: str | None) -> TokenKind:
    """Classify a token by its well-known prefix."""
    if not token:
        return "unknown"
    if token.startswith(("ghp_", "gho_")):
        return "classic"
    if token.startswith("github_pat_"):
        return "fine-grained"
    return "unknown"


@dataclass
class TokenInfo:
    """What we know about the current token."""

    kind: TokenKind = "unknown"
    scopes: set[str] | None = None  # classic tokens only; None = not introspectable

    def apply_scopes_header(self, header: str | None) -> None:
        """Record scopes from an ``X-OAuth-Scopes`` header (classic tokens only)."""
        if header is None:
            return
        self.scopes = {s.strip() for s in header.split(",") if s.strip()}
        self.kind = "classic"


class CapabilityCache:
    """Tracks which permission families the current token has.

    Resolution order: an observed result (from a real 2xx/403) always wins;
    otherwise classic scopes answer; otherwise ``None`` (unknown — try the
    call and let the response teach us).
    """

    def __init__(self, token_info: TokenInfo) -> None:
        self.token_info = token_info
        self._observed: dict[str, bool] = {}

    def resolve(self, family: str) -> bool | None:
        if family in self._observed:
            return self._observed[family]
        scopes = self.token_info.scopes
        if scopes is None:
            return None
        if family == "delete":
            return "delete_repo" in scopes
        return "repo" in scopes or "public_repo" in scopes

    def mark(self, family: str, allowed: bool) -> None:
        self._observed[family] = allowed

    def hint(self, family: str) -> str:
        return FAMILY_HINTS.get(family, "")
