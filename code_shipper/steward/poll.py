"""Polling triggers: review and approvals driven by a clock (D12, Phase 5).

No webhooks in v1 — there is no public endpoint, and inventing one to receive events would put a
listener on the internet to save a poll. So the trigger is a loop:

    every interval: has the head moved? → if yes: incremental review, then approvals re-evaluation

Three properties keep a loop safe:

**The head is compared to what we last reviewed, not to the base.** A push that changes nothing we
reviewed is still a new head, so the review re-runs and the cache decides for itself whether to call a
model. The loop does not try to be clever about which pushes matter.

**Stop and label are checked every round, before any work.** A stop that is only honoured at the end of
a round is not a stop. Both signals are read fresh from the host each round rather than remembered, so
a label removed a second ago takes effect now.

**Our own wall clock.** The loop stops itself after a deadline it set, independent of any event, because
a trigger that depends on an event arriving is a trigger that can wait forever.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Round:
    """What one poll did, and why."""

    number: int = 0
    head: str = ""
    previous_head: str = ""
    changed: bool = False
    reviewed: bool = False
    approvals_updated: bool = False
    stopped: bool = False
    reason: str = ""
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "head": self.head,
            "previous_head": self.previous_head,
            "changed": self.changed,
            "reviewed": self.reviewed,
            "approvals_updated": self.approvals_updated,
            "stopped": self.stopped,
            "reason": self.reason,
            "seconds": round(self.seconds, 3),
        }


@dataclass
class PollResult:
    rounds: list[Round] = field(default_factory=list)
    stopped_reason: str = ""
    last_head: str = ""

    @property
    def reviewed_count(self) -> int:
        return sum(1 for round_ in self.rounds if round_.reviewed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rounds": [round_.as_dict() for round_ in self.rounds],
            "stopped_reason": self.stopped_reason,
            "last_head": self.last_head,
            "reviewed_count": self.reviewed_count,
        }


def poll(
    *,
    current_head: Callable[[], str],
    is_stopped: Callable[[], bool],
    on_new_head: Callable[[str, str], None],
    on_round: Callable[[Round], None] | None = None,
    interval: float = 30.0,
    minutes: float = 0.0,
    max_rounds: int = 0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> PollResult:
    """Poll for a new head and call ``on_new_head(old, new)`` when it moves.

    ``is_stopped`` is consulted at the top of every round, before the head is read and before any work:
    a stop that only takes effect between rounds is honoured late, and one that only takes effect at the
    end is not honoured at all.
    """
    if interval <= 0:
        raise ValueError("interval must be positive")
    started = clock()
    deadline = started + (minutes * 60.0) if minutes else None

    result = PollResult()
    last_seen = ""
    number = 0

    while True:
        number += 1
        round_started = clock()
        current = Round(number=number)

        # Stop before anything else this round.
        if is_stopped():
            current.stopped = True
            current.reason = "stopped"
            result.rounds.append(current)
            result.stopped_reason = "stopped"
            break

        if deadline is not None and clock() >= deadline:
            result.stopped_reason = "deadline"
            break

        if max_rounds and number > max_rounds:
            result.stopped_reason = "max_rounds"
            break

        try:
            head = current_head()
        except Exception as exc:  # noqa: BLE001 - a host hiccup must not kill the loop
            current.reason = f"could not read the head: {exc}"
            current.seconds = clock() - round_started
            result.rounds.append(current)
            # Keep polling: a transient read failure is not a decision to stop.
        else:
            current.head = head
            current.previous_head = last_seen
            current.changed = bool(last_seen) and head != last_seen
            if not last_seen:
                # The first poll establishes the baseline without reviewing: there is nothing to
                # compare against yet, and reviewing here would post a review the caller did not ask
                # for on a PR nobody pushed to.
                current.reason = "baseline"
                last_seen = head
            elif current.changed:
                on_new_head(last_seen, head)
                current.reviewed = True
                current.reason = "new head reviewed"
                last_seen = head
            else:
                current.reason = "head unchanged"

            current.seconds = clock() - round_started
            result.rounds.append(current)

        if on_round is not None:
            on_round(current)

        if deadline is not None and clock() >= deadline:
            result.stopped_reason = "deadline"
            break
        if max_rounds and number >= max_rounds:
            result.stopped_reason = "max_rounds"
            break
        sleep(interval)

    result.last_head = last_seen
    return result
