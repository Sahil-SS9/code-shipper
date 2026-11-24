"""P2-B's review-posting path, kept as a thin alias over the role-aware broker.

`write.Broker` predates the role allowlist in `roles.py`. Two implementations of the same rule is how
one of them silently stops being enforced, so this module now re-exports the role-aware broker and
keeps only the names the P2-B tests use.

New code should import from `.roles`.
"""

from __future__ import annotations

from .roles import (  # noqa: F401
    FORBIDDEN_OPS,
    REVIEW_EVENTS_BY_ROLE,
    ROLE_OPS,
    Broker,
    BrokerError,
    BrokerRefused,
    CountingTransport,
    Receipt,
    Transport,
    UrllibTransport,
    quote_path,
    reconcile,
)

# The P2-B name for the reviewer-only event set.
REVIEWER_EVENTS = REVIEW_EVENTS_BY_ROLE["reviewer"]

__all__ = [
    "Broker",
    "BrokerError",
    "BrokerRefused",
    "CountingTransport",
    "FORBIDDEN_OPS",
    "REVIEWER_EVENTS",
    "REVIEW_EVENTS_BY_ROLE",
    "ROLE_OPS",
    "Receipt",
    "Transport",
    "UrllibTransport",
    "quote_path",
    "reconcile",
]
