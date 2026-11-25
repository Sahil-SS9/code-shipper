"""CLI output: verdict first, then Next, then detail (Phase 5c).

The shape is fixed by the spec because it is the part a script and a person both read:

    <verdict pill>  <#n title>  · head · reviewed N files · Ns

    Next: <who acts, on what>

    <detail>

    <dim summary>

Exit codes are the contract for anything wrapping this: 0 clean, 1 findings, 2 error, 3 incomplete.
They are returned as data (`EXIT_CODES`) and asserted by tests, not left to each call site.

`--json` carries the same facts. Colour is off when stdout is not a TTY or `NO_COLOR` is set — a
non-interactive run that emitted escape codes would break every pipe it enters.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, TextIO

from .projection import Projection

# The exit-code contract. Referenced from --help and the README so the three cannot drift.
EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2
EXIT_INCOMPLETE = 3

EXIT_CODES: dict[str, int] = {
    "clean": EXIT_CLEAN,
    "findings": EXIT_FINDINGS,
    "error": EXIT_ERROR,
    "incomplete": EXIT_INCOMPLETE,
}

EXIT_HELP = "0 = clean · 1 = findings · 2 = error · 3 = incomplete"

# ANSI, used only when colour is enabled.
COLOURS = {
    "red": "\033[31m", "green": "\033[32m", "amber": "\033[33m",
    "blue": "\033[34m", "dim": "\033[2m", "bold": "\033[1m",
    "inverse_red": "\033[41;30m", "inverse_amber": "\033[43;30m",
    "inverse_green": "\033[42;30m", "inverse_grey": "\033[47;30m",
    "reset": "\033[0m",
}

PILL_COLOURS = {"fail": "inverse_red", "warn": "inverse_amber",
                "pass": "inverse_green", "muted": "inverse_grey",
                "flight": "blue"}


def colour_enabled(stream: TextIO | None = None, *, environ: dict[str, str] | None = None) -> bool:
    """Whether to emit ANSI. No TTY or NO_COLOR means plain text."""
    env = os.environ if environ is None else environ
    if env.get("NO_COLOR") is not None and env.get("NO_COLOR") != "":
        return False
    target = stream if stream is not None else sys.stdout
    try:
        return bool(target.isatty())
    except (AttributeError, ValueError):
        return False


def exit_code(projection: Projection) -> int:
    """The exit code for a projection state.

    Incomplete is its own code, not an error: a caller can retry an incomplete review but must not treat
    it as clean, and collapsing the two would give a CI wrapper no way to distinguish them.
    """
    if projection.state == "error":
        return EXIT_ERROR
    if projection.state == "incomplete":
        return EXIT_INCOMPLETE
    if projection.findings and any(
        f.severity.lower() in ("important", "blocking") for f in projection.findings
    ):
        return EXIT_FINDINGS
    if projection.state in ("findings", "paused"):
        return EXIT_FINDINGS
    return EXIT_CLEAN


class _Painter:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, colour: str) -> str:
        if not self.enabled:
            return text
        return f"{COLOURS.get(colour, '')}{text}{COLOURS['reset']}"


def render_review(projection: Projection, *, colour: bool = False) -> list[str]:
    """A review result: pill, Next, detail, summary."""
    paint = _Painter(colour)
    pill_text = f" {projection.chip or 'OK'} "
    pill = paint(pill_text, PILL_COLOURS.get(projection.level, "inverse_grey"))
    budget = projection.budget
    # Built in steps rather than nested inside the f-string below: a nested f-string in a replacement
    # field with a reused quote is PEP 701 (3.12+) only, and this project supports 3.11.
    head_text = projection.head[:8] or "—"
    detail = (f"· head {head_text} · {len(projection.findings)} finding(s) · "
              f"{budget.clock_text()}")
    lines = [
        f"{pill} {paint('#' + str(projection.pr) + ' ' + projection.title, 'bold')}  "
        f"{paint(detail, 'dim')}",
        "",
    ]

    headline, sub, action, _label = projection.next_bar()
    lines.append(f"{paint('Next:', 'bold')} {headline}")
    if sub:
        lines.append(f"      {paint(sub, 'dim')}")
    if action in projection.actions() and projection.state != "empty":
        lines.append(f"      {paint('run: code-shipper ' + _verb(action) + ' ' + str(projection.pr), 'blue')}")
    lines.append("")

    if projection.refusal and projection.state != "error":
        lines.append(paint(f"  blocker: {projection.refusal}", "amber"))
        lines.append("")

    for finding in projection.findings:
        important = finding.severity.lower() in ("important", "blocking")
        tag = paint("IMPORTANT", "red") if important else paint("NIT", "dim")
        lines.append(f"  {tag}  {finding.title}")
        lines.append(f"             {paint(f'{finding.path}:{finding.line}', 'dim')}")
        if finding.why:
            lines.append(f"             {finding.why}")
        if finding.suggested_fix:
            lines.append(f"             {paint('- ' + finding.suggested_fix[0].strip(), 'red')}")
            lines.append(f"             {paint('+ ' + finding.suggested_fix[1].strip(), 'green')}"
                         f" {paint('(suggested, not applied)', 'dim')}")
        if finding.also_noted:
            lines.append(f"             {paint('also noted: ' + ', '.join(finding.also_noted), 'dim')}")
        lines.append("")

    if not projection.findings:
        lines.append(f"  {paint('No findings at this head.', 'green')}")
        lines.append("")

    summary = [f"Review {_state_word(projection)}"]
    if projection.approvals_needed_count:
        summary.append(f"approvals {projection.approvals_have} of "
                       f"{projection.approvals_needed_count}")
    if projection.signals:
        finished = sum(1 for s in projection.signals if s.ok)
        summary.append(f"{finished} of {len(projection.signals)} checks finished")
    lines.append(paint(" · ".join(summary), "dim"))
    lines.append(paint(f"exit {exit_code(projection)} ({_exit_word(exit_code(projection))}) · "
                       f"{EXIT_HELP}", "dim"))
    return lines


def render_status(projections: list[Projection], *, colour: bool = False,
                  repo: str = "", check_every_minutes: int = 2) -> list[str]:
    """Everything watched, one row per pull request, and how often it is checked."""
    paint = _Painter(colour)
    lines = []
    lines.append(f"{paint(repo or 'watching', 'bold')}  "
                 f"{paint(f'· watching {len(projections)} · checked every {check_every_minutes} min', 'dim')}")
    lines.append("")
    if not projections:
        lines.append("  Nothing is being watched.")
    for projection in projections:
        dot = {"fail": "red", "warn": "amber", "pass": "green",
               "flight": "blue", "muted": "dim"}.get(projection.level, "dim")
        age = projection.rows[0].age_seconds if projection.rows else 0
        age_text = f"{age // 60}m" if age < 3600 else f"{age // 3600}h"
        # Fixed columns, and the reason column is clipped rather than allowed to run into the age.
        title = projection.title[:24]
        verdict = projection.chip
        reason = projection.next_bar()[0]
        lines.append(
            f"  {paint('●', dot)} {paint('#' + str(projection.pr), 'bold'):<6} "
            f"{title:<24}  {paint(f'{verdict:<22}', dot)}  "
            f"{reason[:36]:<36} {paint(age_text, 'dim')}"
        )
    lines.append("")
    lines.append(paint("Open one: code-shipper status #25   Board: code-shipper ui   "
                       "Script: --json", "dim"))
    return lines


def render_json(projection: Projection) -> str:
    """The same facts as data. No colour, ever."""
    payload: dict[str, Any] = projection.as_dict()
    payload["exit_code"] = exit_code(projection)
    headline, sub, action, _label = projection.next_bar()
    payload["next"] = {"headline": headline, "sub": sub, "action": action}
    payload["lanes"] = [{"step": n, "state": s, "detail": d} for n, s, d in projection.lanes()]
    return json.dumps(payload, indent=2, sort_keys=True)


def _verb(action: str) -> str:
    return {"fix": "fix", "review": "review", "open-pr": "status", "stop": "stop",
            "refresh": "status", "explain-policy": "config effective"}.get(action, "status")


def _state_word(projection: Projection) -> str:
    return {"findings": "found issues", "incomplete": "did not finish",
            "approvals-satisfied": "complete", "approvals-blocked": "complete",
            "observe": "complete (observe only)", "paused": "paused",
            "stopped": "stopped", "error": "failed", "reviewing": "running",
            "empty": "complete"}.get(projection.state, "complete")


def _exit_word(code: int) -> str:
    return {EXIT_CLEAN: "clean", EXIT_FINDINGS: "findings", EXIT_ERROR: "error",
            EXIT_INCOMPLETE: "incomplete"}.get(code, "unknown")
