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
    # C2 wrap-up reserve: a floor held back from the main allowance so an exhausted run can still post
    # INCOMPLETE and write its ledger entry. Without it, the run that most needs to report is the one
    # that cannot: the budget refuses at exactly the moment an honest partial result matters most.
    # None means "derive": a quarter of the wall clock, at most 60s. A fixed default would make any
    # run shorter than the default invalid, which is the reserve breaking the thing it protects.
    wrap_up_seconds: int | None = None
    wrap_up_usd: float | None = None
    clock: Callable[[], float] = time.monotonic
    _started_at: float | None = field(default=None, repr=False)
    rounds_used: int = 0
    pushes_used: int = 0
    spend_used: float = 0.0
    unknown_cost_runs: int = 0
    _wrap_up_started: bool = field(default=False, repr=False)
    _wrap_up_used: bool = field(default=False, repr=False)
    _wrap_up_deadline: float | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        for name in ("fix_rounds", "pushes_per_day", "wall_clock_seconds"):
            value = getattr(self, name)
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        explicit_reserve = self.wrap_up_seconds is not None
        if self.wrap_up_seconds is None:
            # Scale with the clock: a quarter, capped at a minute. A short run still gets a reserve
            # proportional to its length rather than none at all.
            # Scale, but never equal the clock. Below two seconds there is no whole second to hold
            # back, so the reserve is 0 and `reserve_available` reports False — an honest "this run
            # is too short to wrap up" rather than a configuration error or a useless reserve.
            self.wrap_up_seconds = min(60, self.wall_clock_seconds // 4)
        if self.wrap_up_seconds < 0:
            raise ValueError("wrap_up_seconds must be >= 0")
        if self.wrap_up_seconds >= self.wall_clock_seconds:
            # A reserve that eats the whole run is not a reserve, it is a misconfiguration. Only an
            # explicitly configured reserve is refused for this; a derived one cannot reach it.
            raise ValueError(
                f"wrap_up_seconds ({self.wrap_up_seconds}) must be less than wall_clock_seconds "
                f"({self.wall_clock_seconds})"
            )
        assert self.wrap_up_seconds is not None or not explicit_reserve
        if self.daily_usd is not None:
            if not isinstance(self.daily_usd, (int, float)) or self.daily_usd < 0:
                raise ValueError("daily_usd must be a number >= 0, or omitted")
            if not self.enforceable:
                raise ValueError(
                    "daily_usd is set but the adapter cannot enforce or reserve it; refusing the "
                    "run rather than pretending the cap applies"
                )
        if self.wrap_up_usd is None and self.daily_usd is not None:
            # About 5% of the cap, with a small fixed minimum so a tiny cap still leaves room to post.
            # The minimum is itself capped at half the allowance, because on a very small cap a fixed
            # floor would exceed the whole budget and be refused — the reserve must scale down, not
            # refuse to exist.
            self.wrap_up_usd = max(
                self.daily_usd * 0.05, min(0.01, self.daily_usd * 0.5)
            )
        if self.wrap_up_usd is not None:
            if not isinstance(self.wrap_up_usd, (int, float)) or self.wrap_up_usd < 0:
                raise ValueError("wrap_up_usd must be a number >= 0, or omitted")
            if self.daily_usd is not None and self.wrap_up_usd >= self.daily_usd:
                raise ValueError(
                    f"wrap_up_usd ({self.wrap_up_usd}) must be less than daily_usd "
                    f"({self.daily_usd})"
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
        # Main-allowance work stops short of the ceiling by the reserve, so the wrap-up can still run.
        if not self._wrap_up_started and elapsed >= self.working_limit_seconds:
            raise BudgetExceeded(
                "wall_clock",
                f"{int(elapsed)}s of {self.working_limit_seconds}s of working time "
                f"(the last {self.wrap_up_seconds}s is reserved for wrapping up)",
            )
        if elapsed >= self.wall_clock_seconds:
            raise BudgetExceeded(
                "wall_clock", f"{int(elapsed)}s of {self.wall_clock_seconds}s"
            )

    @property
    def elapsed_seconds(self) -> float:
        if self._started_at is None:
            return 0.0
        return self.clock() - self._started_at

    # -- wrap-up reserve (C2) ----------------------------------------------

    @property
    def working_limit_seconds(self) -> int:
        """Where ordinary work must stop: the ceiling minus the reserved wrap-up window."""
        return max(1, self.wall_clock_seconds - self.wrap_up_seconds)

    @property
    def reserve_available(self) -> bool:
        """Whether there is a reserved floor and it is unspent.

        A run too short to hold back a whole second has no reserve, and says so, rather than offering
        a wrap-up window of zero that would refuse the moment it opened.
        """
        return self.wrap_up_seconds > 0 and not self._wrap_up_used

    def begin_wrap_up(self) -> None:
        """Spend the reserved floor, once, to report honestly after the main allowance is gone.

        One-shot on purpose. A reserve a loop can enter repeatedly is not a reserve, it is a second
        budget with a friendlier name — and the whole point is that wrap-up work is bounded.
        """
        if self._wrap_up_used:
            raise BudgetExceeded(
                "wrap_up", "the wrap-up reserve has already been spent"
            )
        self._wrap_up_started = True
        self._wrap_up_used = True
        self._wrap_up_deadline = self.clock() + max(1, self.wrap_up_seconds)

    def wrap_up_remaining_seconds(self) -> float:
        """Seconds left in the wrap-up window, or 0 when it never started or has expired."""
        if not self._wrap_up_started or self._wrap_up_deadline is None:
            return 0.0
        return max(0.0, self._wrap_up_deadline - self.clock())

    def check_wrap_up(self) -> None:
        """Refuse if the wrap-up window has run out. Only meaningful after begin_wrap_up()."""
        if not self._wrap_up_started:
            raise BudgetExceeded("wrap_up", "the wrap-up reserve has not been entered")
        if self.wrap_up_remaining_seconds() <= 0:
            raise BudgetExceeded("wrap_up", "the wrap-up window has expired")

    def record_wrap_up_cost(self, cost_usd: float | None) -> None:
        """Cost spent during wrap-up is exempt from the main daily cap, up to the reserve.

        Otherwise an exhausted run is refused when it tries to post the very message explaining that
        it is exhausted.
        """
        if cost_usd is None:
            self.unknown_cost_runs += 1
            return
        if not isinstance(cost_usd, (int, float)) or cost_usd < 0:
            raise ValueError("cost_usd must be a number >= 0, or None for unknown")
        cap = self.wrap_up_usd
        if cap is not None and (self.spend_used + cost_usd) > (self.daily_usd or 0) + cap:
            raise BudgetExceeded(
                "wrap_up_usd", f"{self.spend_used + cost_usd:.4f} exceeds the cap plus reserve"
            )
        self.spend_used += float(cost_usd)

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
            "working_limit_seconds": self.working_limit_seconds,
            "wrap_up_seconds": self.wrap_up_seconds,
            "wrap_up_usd": self.wrap_up_usd,
            "wrap_up_used": self._wrap_up_used,
        }
