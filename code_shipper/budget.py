"""Run budgets: rounds, pushes, wall clock and spend.

A budget exists to be enforced by us rather than assumed from the harness. Hermes will happily retry a
refused model call for minutes, ignoring its own run budget (measured in Phase 0), so the only
authoritative clock is this one.

Two rules shape the design:

* **A cap that cannot be enforced is refused.** If the adapter cannot report or reserve cost, a
  configured `daily_usd` is rejected at construction rather than being silently treated as unlimited
  or as zero.
* **Unknown cost is unknown, not zero.** A run whose cost nobody reported increments
  `unknown_cost_runs`; it does not tell the next reader that nothing was spent.

`start_round()` is the one that matters adversarially: a loop must not be able to extend its own
allowance by calling it again.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable


class BudgetExceeded(Exception):
    """A budget limit was reached. ``limit`` names which one."""

    def __init__(self, limit: str, detail: str = "") -> None:
        super().__init__(f"{limit}: {detail}" if detail else limit)
        self.limit = limit
        self.detail = detail


@dataclass
class RunBudget:
    """Per-run and per-day limits. Constructed once per run; not resettable from inside."""

    fix_rounds: int = 3
    pushes_per_day: int = 10
    wall_clock_seconds: int = 45 * 60
    daily_usd: float | None = None
    enforceable: bool = True
    clock: Callable[[], float] = time.monotonic
    _started_at: float | None = field(default=None, repr=False)
    rounds_used: int = 0
    pushes_used: int = 0
    spend_used: float = 0.0
    unknown_cost_runs: int = 0

    def __post_init__(self) -> None:
        for name in ("fix_rounds", "pushes_per_day", "wall_clock_seconds"):
            value = getattr(self, name)
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.daily_usd is not None:
            if not isinstance(self.daily_usd, (int, float)) or self.daily_usd < 0:
                raise ValueError("daily_usd must be a number >= 0, or omitted")
            if not self.enforceable:
                raise ValueError(
                    "daily_usd is set but the adapter cannot enforce or reserve it; refusing the "
                    "run rather than pretending the cap applies"
                )
        # The clock starts when the budget is created, because the budget's lifetime is the run.
        if self._started_at is None:
            self._started_at = self.clock()

    # -- rounds ------------------------------------------------------------

    def start_round(self) -> None:
        """Begin a fix round, or refuse if the allowance is spent."""
        if self.rounds_used >= self.fix_rounds:
            raise BudgetExceeded("fix_rounds", f"{self.rounds_used} of {self.fix_rounds} used")
        self.check_clock()
        if self._started_at is None:
            self._started_at = self.clock()
        self.rounds_used += 1

    # -- pushes ------------------------------------------------------------

    def record_push(self) -> None:
        if self.pushes_used >= self.pushes_per_day:
            raise BudgetExceeded("pushes_per_day", f"{self.pushes_used} of {self.pushes_per_day}")
        self.pushes_used += 1

    # -- clock -------------------------------------------------------------

    def check_clock(self) -> None:
        """Refuse if the wall clock has run out.

        Ours, not the harness's: a refused model call makes Hermes retry for minutes regardless of
        its own run budget, so the caller must consult this before each step rather than trusting
        the agent to stop.

        The clock starts at construction. A budget's lifetime is the run it was created for, so
        ``wall_clock_seconds`` counts from creation rather than from the first check — otherwise a
        caller could postpone every check and never be refused.
        """
        if self._started_at is None:
            self._started_at = self.clock()
            return
        elapsed = self.clock() - self._started_at
        if elapsed >= self.wall_clock_seconds:
            raise BudgetExceeded(
                "wall_clock", f"{int(elapsed)}s of {self.wall_clock_seconds}s"
            )

    @property
    def elapsed_seconds(self) -> float:
        if self._started_at is None:
            return 0.0
        return self.clock() - self._started_at

    # -- spend -------------------------------------------------------------

    def record_cost(self, cost_usd: float | None) -> None:
        """Record a model cost. ``None`` means unknown, which is recorded as unknown."""
        if cost_usd is None:
            self.unknown_cost_runs += 1
            return
        if not isinstance(cost_usd, (int, float)) or cost_usd < 0:
            raise ValueError("cost_usd must be a number >= 0, or None for unknown")
        if self.daily_usd is not None and (self.spend_used + cost_usd) > self.daily_usd:
            raise BudgetExceeded(
                "daily_usd", f"{self.spend_used + cost_usd:.4f} of {self.daily_usd:.2f}"
            )
        self.spend_used += float(cost_usd)

    # -- reporting ---------------------------------------------------------

    @property
    def rounds_remaining(self) -> int:
        return max(0, self.fix_rounds - self.rounds_used)

    @property
    def pushes_remaining(self) -> int:
        return max(0, self.pushes_per_day - self.pushes_used)

    def as_dict(self) -> dict:
        return {
            "rounds_used": self.rounds_used,
            "fix_rounds": self.fix_rounds,
            "pushes_used": self.pushes_used,
            "pushes_per_day": self.pushes_per_day,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "wall_clock_seconds": self.wall_clock_seconds,
            "spend_used": round(self.spend_used, 6),
            "daily_usd": self.daily_usd,
            "unknown_cost_runs": self.unknown_cost_runs,
        }
