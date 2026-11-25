"""The TUI board: a pure renderer of a Projection (Phase 5b).

`render(projection, width, height)` returns a list of lines and nothing else. No clock, no terminal
query, no state of its own — which is what makes it screenshot-able, diffable and testable, and what
stops the TUI accumulating logic the CLI does not have.

Layout, per the wireframes: a persistent context line on top, attention-sorted inbox left, detail
right, and a keymap footer that shows only the keys that work in the current state. At narrow widths
the two regions collapse to a list, and below the floor it degrades to a plain text summary rather than
clipping the actions.
"""

from __future__ import annotations

from .projection import Projection

# The floor below which a board would clip its own actions. Text is better than a broken box.
MIN_BOARD_WIDTH = 72

# Key bindings, per the design notes: `s` stops watching, `x` cancels the run, Escape closes.
KEYMAP: dict[str, str] = {
    "stop": "s stop watching",
    "cancel": "x cancel run",
    "fix": "f fix",
    "review": "r review",
    "open-pr": "o open PR",
    "explain-policy": "p policy",
    "refresh": "R refresh",
    "withdraw-lesson": "w withdraw lesson",
}


def _clip(text: str, width: int) -> str:
    """Clip to width, marking the cut so a reader knows text is missing."""
    if width <= 1:
        return ""
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


def _pad(text: str, width: int) -> str:
    return _clip(text, width).ljust(width)


def _box(title: str, body: list[str], width: int, height: int) -> list[str]:
    """A bordered panel. Every returned line is exactly `width` display columns."""
    inner = max(1, width - 2)
    lines = ["┌" + _pad(title, inner) + "┐"]
    for row in body[: max(0, height - 2)]:
        lines.append("│" + _pad(row, inner) + "│")
    while len(lines) < height - 1:
        lines.append("│" + " " * inner + "│")
    lines.append("└" + "─" * inner + "┘")
    return lines


def _inbox_lines(projection: Projection, width: int) -> list[str]:
    if not projection.rows:
        return [f"  {projection.chip}", "  nothing in the inbox"]
    lines = []
    for row in projection.rows:
        marker = "▸" if row.repo == projection.repo and row.pr == projection.pr else " "
        lines.append(f" {marker} {row.chip:<13} #{row.pr:<4} {_clip(row.title, max(8, width - 26))}")
        # The reason, in words, on its own line: a chip alone never says why.
        lines.append(f"     {_clip(row.reason, max(8, width - 8))}")
    return lines


def _signal_lines(projection: Projection) -> list[str]:
    """The four independent signals, never compressed into one badge."""
    out = []
    for signal in projection.signals:
        mark = "✓" if signal.ok else "·"
        out.append(f" {mark} {signal.name:<9} {signal.value}")
    return out


def _finding_lines(projection: Projection, width: int) -> list[str]:
    if not projection.findings:
        return [" no findings"]
    out = []
    for finding in projection.findings:
        out.append(f" {finding.severity[:9]:<10} {_clip(finding.path, width - 30)}:{finding.line}")
        out.append(f"   {_clip(finding.title, max(8, width - 6))}")
    return out


def render(projection: Projection, *, width: int = 113, height: int = 30) -> list[str]:
    """The board as lines. Pure: same input, same output, always."""
    if width < MIN_BOARD_WIDTH:
        return render_compact(projection, width=width)

    # Context line: connection, profile, runtime owner, sync age.
    lines = [_pad(projection.context_line(), width)]

    header = f" {projection.chip}  {projection.repo}#{projection.pr}  {projection.title}"
    lines.append(_pad(header, width))
    if not projection.head_pinned and projection.head:
        # A verdict computed at a different head must say so rather than look current.
        lines.append(_pad(
            f" STALE: verdict is for {projection.reviewed_head[:8] or 'none'}, "
            f"head is {projection.head[:8]}", width))

    left_width = max(34, width // 3)
    right_width = width - left_width
    detail_height = height - 6

    inbox = _inbox_lines(projection, left_width - 2)
    detail: list[str] = []
    detail += _signal_lines(projection)
    detail.append("")
    if projection.verdict:
        detail.append(f" verdict  {projection.verdict}"
                      + ("" if projection.head_pinned else " (stale)"))
    if projection.refusal:
        detail.append(f" blocker  {_clip(projection.refusal, right_width - 4)}")
    detail.append("")
    detail += _finding_lines(projection, right_width - 2)
    detail.append("")
    detail.append(f" {projection.budget.rounds_text()} · {projection.budget.pushes_text()} · "
                  f"{projection.budget.clock_text()}")
    detail.append(f" {projection.budget.spend_text()}")
    if projection.log:
        detail.append("")
        detail += [f" {_clip(line, right_width - 4)}" for line in projection.log[-4:]]

    left = _box(" inbox — needs you first ", inbox, left_width, detail_height)
    right = _box(" detail ", detail, right_width, detail_height)
    for index in range(max(len(left), len(right))):
        lines.append((left[index] if index < len(left) else " " * left_width)
                     + (right[index] if index < len(right) else " " * right_width))

    # Keymap footer: only the keys that work right now.
    live = projection.actions()
    keys = " ".join(KEYMAP[action] for action in live if action in KEYMAP)
    keys += " esc close"
    lines.append(_pad(keys, width))
    lines.append(_pad(f" {projection.authority_note}", width))
    return lines


def render_compact(projection: Projection, *, width: int = 70) -> list[str]:
    """Below the board floor: plain text with the actions still readable.

    Deliberately not a squeezed board. Clipping a board hides actions, and hiding an action is worse
    than losing the box drawing.
    """
    lines = [
        _pad(projection.context_line(), width),
        _pad(f" {projection.chip}  {projection.repo}#{projection.pr} {projection.title}", width),
    ]
    if projection.verdict:
        lines.append(_pad(f" verdict: {projection.verdict}", width))
    if projection.refusal:
        lines.append(_pad(f" blocker: {projection.refusal}", width))
    for signal in projection.signals:
        lines.append(_pad(f"  {'✓' if signal.ok else '·'} {signal.name}: {signal.value}", width))
    for finding in projection.findings:
        lines.append(_pad(
            f"  {finding.severity} {finding.path}:{finding.line} {finding.title}", width))
    lines.append(_pad("", width))
    live = " ".join(KEYMAP[action] for action in projection.actions() if action in KEYMAP)
    lines.append(_pad(f" keys: {live} esc close", width))
    return lines
