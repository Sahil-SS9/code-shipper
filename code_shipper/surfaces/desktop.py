"""The Desktop page: HTML rendered from the same Projection (Phase 5b).

Self-contained by construction: inline CSS, no JavaScript, no external assets. That is not only a
convenience for screenshots — a page that needs no script to state its content is a page whose content
can be checked by reading it.

The one deliberate asymmetry with the TUI: the design notes argue that *absence* alone is ambiguous,
so the Desktop shows a **disabled** "Merge — not available" control with its reason beside it, where the
TUI simply has no merge key. Both teach the boundary; neither offers the action.
"""

from __future__ import annotations

from .projection import Projection

# The wireframe palette, copied exactly so the built surface matches the approved mock.
CSS = """
:root{
  --bg:#0a0d0f; --panel:#13181b; --panel2:#1b2226; --line:#252d31; --line2:#2c353a;
  --text:#d4dad5; --muted:#8b968f; --dim:#5d6a63;
  --amber:#e0a458; --green:#7fbf7a; --red:#d96a5f; --blue:#7ba7bc;
  --mono:ui-monospace,"JetBrains Mono","Cascadia Mono",Menlo,Consolas,"Liberation Mono",monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--mono);font-size:14px;line-height:1.45}
.page{display:grid;grid-template-columns:270px 1fr 290px;min-height:100vh}
.col{border-right:1px solid var(--line);display:flex;flex-direction:column;min-width:0}
.col.rail{border-right:0;border-left:1px solid var(--line)}
.hd{padding:10px 12px;border-bottom:1px solid var(--line);color:var(--muted);font-size:11px;letter-spacing:.14em;text-transform:uppercase}
.ctx{padding:8px 12px;border-bottom:1px solid var(--line);color:var(--dim);font-size:11px}
.row{padding:9px 12px;border-bottom:1px solid var(--line2);cursor:default}
.row.sel{background:var(--panel2);border-left:2px solid var(--amber)}
.row .t{display:flex;gap:8px;align-items:baseline}
.num{color:var(--muted)}
.title{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.reason{color:var(--muted);font-size:11px;margin-top:3px;white-space:normal}
.chip{display:inline-block;padding:1px 6px;border:1px solid var(--line2);border-radius:2px;font-size:10px;letter-spacing:.1em}
.lv-pass{color:var(--green);border-color:var(--green)}
.lv-fail{color:var(--red);border-color:var(--red)}
.lv-warn{color:var(--amber);border-color:var(--amber)}
.lv-flight{color:var(--blue);border-color:var(--blue)}
.lv-muted{color:var(--muted)}
.tabs{display:flex;gap:2px;border-bottom:1px solid var(--line);padding:0 12px}
.tab{padding:8px 10px;color:var(--muted);font-size:12px;border-bottom:2px solid transparent}
.tab.on{color:var(--text);border-bottom-color:var(--amber)}
.body{padding:14px 16px;flex:1;overflow:auto}
h2{margin:0 0 4px;font-size:16px;font-weight:500}
.sub{color:var(--muted);font-size:12px;margin-bottom:14px}
.kv{display:grid;grid-template-columns:96px 1fr;gap:4px 12px;margin:10px 0 18px}
.kv .k{color:var(--dim);font-size:11px;letter-spacing:.08em;text-transform:uppercase}
.sig{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:18px}
.sig .card{background:var(--panel);border:1px solid var(--line);padding:8px 10px}
.sig .n{color:var(--dim);font-size:10px;letter-spacing:.1em;text-transform:uppercase}
.sig .v{margin-top:3px}
.ok{color:var(--green)} .bad{color:var(--red)} .warn{color:var(--amber)} .mut{color:var(--muted)}
.find{background:var(--panel);border:1px solid var(--line);border-left:2px solid var(--red);padding:10px 12px;margin-bottom:8px}
.find .p{color:var(--blue);font-size:12px}
.find .ti{margin-top:4px}
.find .ev{color:var(--dim);font-size:11px;margin-top:5px}
.blocker{background:var(--panel);border:1px solid var(--amber);border-left:2px solid var(--amber);color:var(--amber);padding:10px 12px;margin:0 0 14px}
.rail .sec{padding:12px;border-bottom:1px solid var(--line2)}
.rail h3{margin:0 0 8px;font-size:10px;color:var(--dim);letter-spacing:.12em;text-transform:uppercase;font-weight:500}
.meter{margin-bottom:10px}
.meter .lb{display:flex;justify-content:space-between;font-size:11px;color:var(--muted)}
.meter .bar{height:4px;background:var(--line);margin-top:4px}
.meter .fill{height:4px;background:var(--blue)}
.btn{display:block;width:100%;text-align:left;background:var(--panel2);border:1px solid var(--line2);color:var(--text);padding:7px 10px;font-family:var(--mono);font-size:12px;margin-bottom:6px}
.btn.primary{background:var(--amber);color:#1a1206;border-color:var(--amber)}
.btn.off{color:var(--dim);border-style:dashed;cursor:not-allowed}
.btn .why{display:block;color:var(--dim);font-size:10px;margin-top:2px}
.note{color:var(--dim);font-size:11px;line-height:1.5}
.foot{padding:10px 12px;border-top:1px solid var(--line);color:var(--dim);font-size:10px}
.empty{padding:28px 16px;color:var(--muted);text-align:center}
"""

LEVEL_CLASS = {"pass": "lv-pass", "fail": "lv-fail", "warn": "lv-warn",
               "flight": "lv-flight", "muted": "lv-muted"}


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _chip(text: str, level: str) -> str:
    return f'<span class="chip {LEVEL_CLASS.get(level, "lv-muted")}">{_esc(text)}</span>'


def _inbox(projection: Projection) -> str:
    if not projection.rows:
        return '<div class="empty">Nothing in the inbox.<br>No pull requests are being watched.</div>'
    out = []
    for row in projection.rows:
        selected = " sel" if (row.repo == projection.repo and row.pr == projection.pr) else ""
        out.append(
            f'<div class="row{selected}">'
            f'<div class="t">{_chip(row.chip, row.level)}'
            f'<span class="num">#{row.pr}</span>'
            f'<span class="title">{_esc(row.title)}</span></div>'
            f'<div class="reason">{_esc(row.reason)}</div></div>'
        )
    return "".join(out)


def _signals(projection: Projection) -> str:
    cards = []
    for signal in projection.signals:
        cls = "ok" if signal.ok else ("warn" if "unknown" in signal.value else "bad")
        cards.append(
            f'<div class="card"><div class="n">{_esc(signal.name)}</div>'
            f'<div class="v {cls}">{_esc(signal.value)}</div></div>'
        )
    return f'<div class="sig">{"".join(cards)}</div>'


def _findings(projection: Projection) -> str:
    if not projection.findings:
        return '<div class="note">No findings at this head.</div>'
    out = []
    for finding in projection.findings:
        out.append(
            f'<div class="find"><div class="p">{_esc(finding.path)}:{finding.line}</div>'
            f'<div class="ti">{_esc(finding.title)}</div>'
            f'<div class="ev">severity {_esc(finding.severity)} · evidence: {_esc(finding.evidence_kind)}'
            f'</div></div>'
        )
    return "".join(out)


def _rail(projection: Projection) -> str:
    budget = projection.budget
    round_pct = min(100, int(100 * budget.fixed_rounds / max(1, budget.max_rounds)))
    push_pct = min(100, int(100 * budget.pushes / max(1, budget.max_pushes)))

    live = projection.actions()
    buttons = []
    labels = {"review": "Review at head", "fix": "Fix within budget", "stop": "Stop watching",
              "cancel": "Cancel current run", "open-pr": "Open PR on GitHub",
              "explain-policy": "Explain policy", "refresh": "Refresh"}
    for action in live:
        if action in labels:
            primary = " primary" if action == "open-pr" else ""
            buttons.append(f'<button class="btn{primary}">{_esc(labels[action])}</button>')
    # The teaching device: a visibly disabled control with its reason, per the design notes.
    buttons.append(
        '<button class="btn off" disabled>Merge — not available'
        '<span class="why">Code-Shipper never merges. A human merges on GitHub.</span></button>'
    )
    buttons.append(
        '<button class="btn off" disabled>Approve — not available'
        '<span class="why">This tool never approves its own work.</span></button>'
    )

    return f"""
<div class="col rail">
  <div class="hd">Context</div>
  <div class="sec">
    <h3>Run</h3>
    <div class="note">owner {_esc(projection.runtime_owner or "unknown")}<br>
    profile {_esc(projection.profile)}<br>synced {projection.sync_age_seconds}s ago</div>
  </div>
  <div class="sec">
    <h3>Budget</h3>
    <div class="meter"><div class="lb"><span>rounds</span><span>{budget.fixed_rounds}/{budget.max_rounds}</span></div>
      <div class="bar"><div class="fill" style="width:{round_pct}%"></div></div></div>
    <div class="meter"><div class="lb"><span>pushes</span><span>{budget.pushes}/{budget.max_pushes}</span></div>
      <div class="bar"><div class="fill" style="width:{push_pct}%"></div></div></div>
    <div class="meter"><div class="lb"><span>wall clock</span><span>{budget.clock_text()}</span></div></div>
    <div class="note warn">{_esc(budget.spend_text())}</div>
  </div>
  <div class="sec">
    <h3>Actions</h3>
    {''.join(buttons)}
  </div>
  <div class="sec">
    <h3>Authority</h3>
    <div class="note warn">{_esc(projection.authority_note)}</div>
  </div>
  <div class="foot">no merge control · stop ≠ cancel · esc closes</div>
</div>"""


def render(projection: Projection, *, title: str = "Code-Shipper") -> str:
    """The page as one self-contained HTML string."""
    if projection.state == "empty" and not projection.rows:
        inbox = _inbox(projection)
    else:
        inbox = _inbox(projection)

    header = (f'{_chip(projection.chip, projection.level)} '
              f'{_esc(projection.repo)}#{projection.pr}')
    stale = ""
    if projection.head and not projection.head_pinned:
        stale = (f'<div class="blocker">STALE: this verdict was computed at '
                 f'{_esc(projection.reviewed_head[:8] or "none")}, but the head is '
                 f'{_esc(projection.head[:8])}. Review again before acting.</div>')
    blocker = ""
    if projection.refusal:
        blocker = f'<div class="blocker">{_esc(projection.refusal)}</div>'

    findings_html = _findings(projection)
    log_html = "".join(f'<div class="note">{_esc(line)}</div>' for line in projection.log[-8:])

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{_esc(title)}</title>
<style>{CSS}</style></head>
<body><div class="page">
  <div class="col">
    <div class="ctx">{_esc(projection.context_line())}</div>
    <div class="hd">Inbox — needs you first</div>
    {inbox}
  </div>
  <div class="col">
    <div class="tabs">{''.join(f'<div class="tab{" on" if i == 0 else ""}">{_esc(t)}</div>' for i, t in enumerate(projection.tabs))}</div>
    <div class="body">
      <h2>{header}</h2>
      <div class="sub">{_esc(projection.title)} · head {_esc(projection.head[:8])}</div>
      {stale}{blocker}
      {_signals(projection)}
      <div class="kv">
        <div class="k">Verdict</div><div>{_esc(projection.verdict or "—")}</div>
        <div class="k">Head</div><div>{_esc(projection.head[:8] or "—")}</div>
        <div class="k">Reviewed</div><div>{_esc(projection.reviewed_head[:8] or "—")}</div>
        <div class="k">Findings</div><div>{len(projection.findings)}</div>
      </div>
      {findings_html}
      {log_html}
    </div>
  </div>
  {_rail(projection)}
</div></body></html>"""
