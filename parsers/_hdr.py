"""Small header helpers shared by the parsers.

Kept dependency-free (stdlib only) so every parser can pull threading / crypto
metadata out of a plain ``{name: value}`` header dict the same way.
"""

from __future__ import annotations


def hget(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive header lookup."""

    low = name.lower()
    for key, val in headers.items():
        if key.lower() == low:
            return val
    return None


def refs(value: str | None) -> list[str]:
    """Split a ``References`` / ``In-Reply-To`` value into message-id tokens."""

    if not value:
        return []
    return [tok for tok in str(value).replace(",", " ").split() if tok.startswith("<")]


def importance(headers: dict[str, str]) -> str | None:
    imp = (hget(headers, "Importance") or "").strip().lower()
    if imp in ("low", "normal", "high"):
        return imp
    prio = (hget(headers, "X-Priority") or "").strip()[:1]
    if prio in ("1", "2"):
        return "high"
    if prio in ("4", "5"):
        return "low"
    return None


def crypto_flags(headers: dict[str, str]) -> tuple[bool, bool]:
    """Best-effort (not verified) S/MIME or PGP envelope detection from headers."""

    ctype = (hget(headers, "Content-Type") or "").lower()
    signed = (
        "multipart/signed" in ctype
        or "application/pkcs7-signature" in ctype
        or "application/x-pkcs7-signature" in ctype
        or "smime-type=signed-data" in ctype
    )
    encrypted = (
        "multipart/encrypted" in ctype
        or "application/pgp-encrypted" in ctype
        or "smime-type=enveloped-data" in ctype
        or ("application/pkcs7-mime" in ctype and "signed-data" not in ctype)
    )
    return signed, encrypted


def enrich_from_headers(msg) -> None:
    """Fill threading / importance / crypto fields on *msg* from ``msg.headers``.

    Only sets a field that is still at its default, so a parser that already
    knows better (e.g. the ``.eml`` parser walking MIME parts) wins.
    """

    h = msg.headers or {}
    if msg.message_id is None:
        mid = (hget(h, "Message-ID") or "").strip()
        msg.message_id = mid or None
    if msg.in_reply_to is None:
        msg.in_reply_to = (refs(hget(h, "In-Reply-To")) or [None])[0]
    if not msg.references:
        msg.references = refs(hget(h, "References"))
    if msg.importance is None:
        msg.importance = importance(h)
    if not msg.is_signed and not msg.is_encrypted:
        msg.is_signed, msg.is_encrypted = crypto_flags(h)
