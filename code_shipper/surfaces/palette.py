"""The command palette: one list of actions, shared by Desktop and TUI (Phase 5c).

The palette replaces the wall of buttons in the old design. Its whole content is a grouped list of
actions with their keys, and the protection against drift is that the list is derived from the
projection's action vocabulary rather than written out twice.

There is no merge entry and no approve entry. That is not a filter applied at render time — `PALETTE`
cannot contain one, because it is built from `ALLOWED_ACTIONS` and asserted against `FORBIDDEN_ACTIONS`
in the tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from .projection import ALLOWED_ACTIONS, Projection

GROUPS = ("This pull request", "Everywhere")


@dataclass(frozen=True)
class PaletteItem:
    action: str
    label: str
    key: str
    group: str
    detail: str = ""


# The full vocabulary, with the key and the wording each surface shows.
ITEMS: tuple[PaletteItem, ...] = (
    PaletteItem("review", "Review this pull request again", "r", "This pull request",
                "runs a fresh review at the current head"),
    PaletteItem("fix", "Try one bounded fix round", "f", "This pull request",
                "one round, inside the budget, then it stops"),
    PaletteItem("open-pr", "Open on GitHub", "o", "This pull request"),
    PaletteItem("explain-policy", "Explain the approvals rule", "p", "This pull request"),
    PaletteItem("stop", "Stop watching", "s", "This pull request",
                "settles the current round; nothing is left half-pushed"),
    PaletteItem("refresh", "Refresh", "R", "Everywhere"),
    PaletteItem("withdraw-lesson", "Withdraw a stored lesson", "w", "Everywhere"),
)


def items_for(projection: Projection) -> list[PaletteItem]:
    """The palette for this state: only actions live in it, grouped as the spec says.

    Live-only rather than greyed-out: a palette listing actions that will be refused teaches a user that
    the palette is unreliable.
    """
    live = set(projection.actions())
    chosen = [item for item in ITEMS if item.action in live]
    out: list[PaletteItem] = []
    for group in GROUPS:
        out.extend(item for item in chosen if item.group == group)
    return out


def group_items(projection: Projection) -> list[tuple[str, list[PaletteItem]]]:
    chosen = items_for(projection)
    return [(group, [i for i in chosen if i.group == group]) for group in GROUPS
            if any(i.group == group for i in chosen)]


def assert_safe() -> None:
    """Guard used by the tests and available at import: no palette entry may end a pull request."""
    for item in ITEMS:
        if item.action not in ALLOWED_ACTIONS:
            raise AssertionError(f"palette item {item.action!r} is not an allowed action")
        lowered = f"{item.action} {item.label}".lower()
        for word in ("merge", "approve", "lgtm"):
            if word in lowered:
                raise AssertionError(f"palette item {item.action!r} says {word!r}")
