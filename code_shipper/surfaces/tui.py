"""TUI: the same structure as Desktop, in text (Phase 5c).

`render(projection, width, height)` returns lines and nothing else — no clock, no terminal query, no
state. That makes it screenshot-able, diffable and testable, and stops the TUI accumulating logic the
CLI does not have.

Layout: inbox left, detail right, a NEXT bar with a coloured left edge, the lanes line, folded and open
findings with diff lines, and a key bar whose keys are only the ones live in this state. Under 70
columns it collapses to one column — detail with `← list` back — rather than clipping its own keys.

Everything textual comes from the projection. This module decides layout only.
"""

from __future__ import annotations

from .mantra import NEVER_MERGES
from .projection import Projection

# Below this width a two-column board would clip its own key bar.
MIN_BOARD_WIDTH = 70
DEFAULT_WIDTH = 124
DEFAULT_HEIGHT = 34

# Keys, from the spec. There is no merge key and no approve key; a test asserts the absence.
KEYMAP: dict[str, str] = {
    "stop": "s stop",
    "cancel": "x cancel",
    "fix": "f fix",
    "review": "r review",
    "open-pr": "o GitHub",
    "explain-policy": "p rules",
    "refresh": "R refresh",
}

# Navigation keys that are always available (moving, folding, the palette, help, quit).
STATIC_KEYS = (("j/k", "move"), ("↵", "open"), ("c", "copy"), ("t", "track"),
               (":", "commands"), ("?", "help"), ("q", "quit"))

# ◐ means "work is happening right now", and nothing else. A waiting decision is not running: a spinner
# over a step that is waiting for a person reads as activity and invites waiting for it to finish.
LANE_ICON = {"done": "✓", "failed": "✕", "waiting": "▸", "running": "◐",
             "notstarted": "○", "notneeded": "–"}
BULLET = {"fail": "●", "warn": "●", "pass": "●", "muted": "●", "flight": "●"}


def _clip(text: str, width: int) -> str:
    """Clip to width at a word boundary, marking the cut.

    Breaking mid-word produced "PR adds an undocumented w" in a real review, which reads as a bug in the
    data rather than as a shortened line. A word that does not fit is dropped whole and the ellipsis says
    so; when not even one word fits, the hard cut is the only option and the ellipsis still marks it.
    """
    if width <= 1:
        return ""
    if len(text) <= width:
        return text
    head = text[: width - 1]
    if " " in head.strip():
        cut = head.rstrip()
        last_space = cut.rfind(" ")
        if last_space > 0:
            cut = cut[:last_space].rstrip()
        if cut:
            return cut + "…"
    return head + "…"


def _pad(text: str, width: int) -> str:
    return _clip(text, width).ljust(width)


def _age(seconds: int) -> str:
    if seconds <= 0:
        return ""
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _wrap(text: str, width: int) -> list[str]:
    """Wrap on word boundaries, keeping the caller's indent on continuation lines."""
    if width <= 1:
        return [""]
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _wrapped(prefix: str, text: str, width: int) -> list[str]:
    """`prefix` + wrapped text, with continuation lines aligned under the text."""
    indent = " " * len(prefix)
    pieces = _wrap(text, max(8, width - len(prefix)))
    out = [prefix + pieces[0]]
    out.extend(indent + piece for piece in pieces[1:])
    return out


def _next_bar(projection: Projection, width: int, *, wrap: bool = True) -> list[str]:
    """The NEXT bar: a coloured left edge and the copy from the projection.

    Wrapped rather than clipped in the wide layout: the sub-line is the sentence that tells a reader what
    to do, and cutting it with an ellipsis removes exactly the part that says so. The narrow layout clips,
    because there a wrapped sentence is worse than a short one.
    """
    headline, sub, _action, _label = projection.next_bar()
    inner = max(10, width - 2)
    if not wrap:
        return [f"▌{_pad('NEXT  ' + headline, inner)}",
                f"▌{_pad('      ' + sub, inner)}"]
    out = [f"▌{line}" for line in _wrapped("NEXT  ", headline, inner)]
    out += [f"▌{line}" for line in _wrapped("      ", sub, inner)]
    return out


def _lanes_line(projection: Projection, width: int) -> list[str]:
    """Two labelled groups, as the Desktop has: who does what is the point of the line."""
    steps = projection.lanes()
    blocking = ""
    for name, state, _sub in steps:
        if state not in ("done", "notneeded"):
            blocking = name
            break
    # Step names, shortest first. The full labels are the clearest, but a lanes line that overflows is
    # clipped by the caller and loses the *last* step — the one naming the reader's own job — which is
    # the worst possible thing to lose. So the line degrades deliberately rather than being cut.
    def build(step_names: tuple[str, str, str, str], groups: tuple[str, str] | None) -> str:
        labels = []
        for name, (_step, state, _sub), shown in zip(steps, steps, step_names):
            icon = LANE_ICON.get(state, "○")
            text = shown.upper() if name == blocking else shown
            labels.append(f"{icon} {text}")
        if groups:
            return (f" {groups[0]} {labels[0]} {labels[1]} │ {groups[1]} "
                    f"{labels[2]} {labels[3]}")
        return f" {labels[0]} {labels[1]} │ {labels[2]} {labels[3]}"

    full = ("Review", "Fix", "Approvals", "You merge")
    short = ("Review", "Fix", "Appr", "Merge")
    for names, groups in ((full, ("CODE-SHIPPER", "YOU AND GITHUB")),
                          (full, None),
                          (short, ("CS", "YOU"))):
        candidate = build(names, groups)
        if len(candidate) <= width:
            return [_pad(candidate, width)]
    # Nothing fits: the steps still matter more than the group labels.
    return [_pad(build(short, None), width)]


def _inbox(projection: Projection, width: int) -> list[str]:
    lines: list[str] = []
    for name, rows in projection.inbox_groups():
        lines.append(_clip(f" {name.upper()}  {len(rows)}", width))
        for row in rows:
            marker = "▸" if (row.repo == projection.repo and row.pr == projection.pr) else " "
            age = _age(row.age_seconds)
            head_width = max(8, width - 9)
            lines.append(_pad(f"{marker}{BULLET.get(row.level, '●')} "
                              f"{_clip(row.title, head_width)}", width - len(age) - 1)
                         + f" {age}")
            lines.append(_clip(f"   {row.reason}", width))
    if not lines:
        lines = [" nothing in the inbox"]
    return lines


def _finding_lines(projection: Projection, width: int, *, wrap: bool = True) -> list[str]:
    if not projection.findings:
        return [f" {projection.findings_placeholder()}"]
    out: list[str] = []
    open_index = 0
    for index, finding in enumerate(projection.findings):
        if finding.severity.lower() in ("important", "blocking"):
            open_index = index
            break
    for index, finding in enumerate(projection.findings):
        important = finding.severity.lower() in ("important", "blocking")
        tag = "IMPORTANT" if important else "NIT"
        arrow = "▾" if index == open_index else "▸"
        out.append(_clip(f" {arrow} {tag}  {finding.title}", width))
        out.append(_clip(f"        {finding.path}:{finding.line}", width))
        if index == open_index:
            if finding.why:
                if wrap:
                    out += _wrapped("    ", finding.why, width)
                else:
                    out.append(_clip(f"    {finding.why}", width))
            for _kind, number, text in (finding.code or ()):
                out.append(_clip(f"   {number:>3} │ {text}", width))
            if finding.suggested_fix:
                out.append(_clip("       suggested fix · not applied · you decide", width))
                out.append(_clip(f"   {finding.line:>3} −  {finding.suggested_fix[0].strip()}", width))
                out.append(_clip(f"   {finding.line:>3} +  {finding.suggested_fix[1].strip()}", width))
        if finding.also_noted:
            out.append(_clip(f"        also noted: {', '.join(finding.also_noted)}", width))
    return out


def _key_bar(projection: Projection, width: int) -> list[str]:
    """Two rows: the always-available keys, then only the actions live in this state.

    The action row is derived from `projection.actions()`, so a stopped pull request does not advertise a
    stop key. A key bar that lists dead keys teaches a user to distrust every key on it.
    """
    budget = projection.budget
    live = "  ".join(KEYMAP[a] for a in projection.actions() if a in KEYMAP)
    nav = "  ".join(f"{key} {label}" for key, label in STATIC_KEYS)
    tail = f"round {budget.fixed_rounds}/{budget.max_rounds} · {budget.clock_text()}"
    # D16a: the sentence is on every surface in the same words. It sits on the footer rather than only in
    # the NEXT text because a user can read the board without reading the next action.
    return [_pad(f" {nav}", width),
            _pad(f" {live}   {tail}", width),
            _pad(f" {projection.authority_note}", width),
            _pad(f" {NEVER_MERGES}", width)]


def render(projection: Projection, *, width: int = DEFAULT_WIDTH,
           height: int = DEFAULT_HEIGHT) -> list[str]:
    """The board as lines. Pure: same input, same output, always."""
    if width < MIN_BOARD_WIDTH:
        return render_narrow(projection, width=width)

    left_width = max(34, min(42, width // 3))
    right_width = width - left_width - 1

    lines = [_pad(f" code-shipper · {projection.repo} · {projection.who or projection.profile}", width)]
    lines.append(_pad("─" * left_width + "┬" + "─" * right_width, width))

    left = _inbox(projection, left_width - 1)
    right: list[str] = []
    right.append(_clip(f" #{projection.pr}  {projection.title}   {projection.chip}", right_width))
    right.append(_clip(f" {projection.branch} → {projection.base_ref} · head "
                       f"{projection.head[:8] or '—'}", right_width))
    if projection.head and not projection.head_pinned:
        right.append(_clip(f" STALE: verdict is for {projection.reviewed_head[:8] or 'none'}, "
                           f"head is {projection.head[:8]}", right_width))
    right.append("")
    right += _next_bar(projection, right_width)
    right.append("")
    right += _lanes_line(projection, right_width)
    right.append("")
    right += _finding_lines(projection, right_width - 1, wrap=True)

    body_height = max(4, height - 6)
    for index in range(body_height):
        left_cell = left[index] if index < len(left) else ""
        right_cell = right[index] if index < len(right) else ""
        lines.append(_pad(left_cell, left_width) + "│" + _pad(right_cell, right_width))
    lines.append(_pad("─" * left_width + "┴" + "─" * right_width, width))
    lines += _key_bar(projection, width)
    return lines


def render_narrow(projection: Projection, *, width: int = 68) -> list[str]:
    """One column: the selected pull request's detail, with ← back to the list."""
    lines = [_pad(f" #{projection.pr} {projection.title}   {projection.chip}", width)]
    lines.append(_pad(f" {projection.branch} → {projection.base_ref} · "
                      f"{projection.head[:8] or '—'}", width))
    if projection.head and not projection.head_pinned:
        lines.append(_pad(f" STALE: computed at {projection.reviewed_head[:8] or 'none'}", width))
    # Every line is padded to exactly `width`, including the ones built by the shared helpers: a
    # screenshot or a diff of a board with ragged right edges is unusable.
    lines += [_pad(line, width) for line in _next_bar(projection, width, wrap=False)]
    lines += [_pad(line, width) for line in _lanes_line(projection, width)]
    lines.append(_pad("─" * width, width))
    lines += [_pad(line, width) for line in _finding_lines(projection, width, wrap=False)]
    lines.append(_pad("─" * width, width))
    live = "  ".join(KEYMAP[a] for a in projection.actions() if a in KEYMAP)
    lines.append(_pad(f" ← list  j/k move  {live}  : commands  ? help", width))
    lines.append(_pad(f" {projection.authority_note}", width))
    return lines
