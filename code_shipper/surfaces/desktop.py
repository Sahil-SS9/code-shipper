"""Desktop: the approved redesign, rendered from the projection (Phase 5c).

Self-contained by construction: inline CSS, no JavaScript, no external assets.

Structure, from the spec: two panes (320px inbox, then detail) and a status bar carrying the run line.
No right-hand rail. The Next bar comes first in the detail pane because it answers the only question a
reader arrives with. The lanes line replaces the disabled Merge and Approve buttons *and* explains why
those buttons were never there: Review and Fix belong to Code-Shipper; Approvals and Merge belong to a
person.

Nothing here decides anything. Verdict names, Next-bar copy, lane states and inbox grouping all come from
`projection.py`; a surface that computed any of them could disagree with the others.
"""

from __future__ import annotations

from .projection import Projection

# Tokens copied from the approved reference (phase5c-reference-gen.py), not imported.
CSS = """
:root{
 --bg:#08090a;--surface:#0e0f11;--raised:#15161a;--hover:#1a1c20;--line:#1e2025;--line2:#2a2d33;
 --fg:#ececec;--muted:#a0a4ab;--dim:#878c95;
 --red:#e8686e;--red-s:rgba(232,104,110,.12);--green:#5fc992;--green-s:rgba(95,201,146,.12);
 --amber:#e3ac5e;--amber-s:rgba(227,172,94,.13);--blue:#80aef0;--blue-s:rgba(128,174,240,.12);
 --sans:Inter,"SF Pro Text","Segoe UI",system-ui,-apple-system,Roboto,sans-serif;
 --mono:"JetBrains Mono","SF Mono","Cascadia Mono",ui-monospace,Menlo,Consolas,monospace;
 --pop:0 1px 0 rgba(255,255,255,.05) inset,0 0 0 1px rgba(0,0,0,.4),0 24px 64px -20px rgba(0,0,0,.95);
}
*{box-sizing:border-box}
body{margin:0;background:#050506;color:var(--fg);font:14px/1.5 var(--sans);-webkit-font-smoothing:antialiased}
.win{width:1280px;height:800px;background:var(--bg);border:1px solid var(--line2);border-radius:12px;overflow:hidden;
 display:grid;grid-template-rows:44px 1fr 30px;box-shadow:var(--pop);margin:0 auto}
.bar{display:flex;align-items:center;gap:14px;padding:0 14px;border-bottom:1px solid var(--line);background:var(--surface)}
.dots{display:flex;gap:6px}.dots i{width:11px;height:11px;border-radius:50%;background:#2a2d33;display:block}
.brand{font-weight:600;font-size:13px;display:flex;align-items:center;gap:8px}
.brand b{width:18px;height:18px;border-radius:5px;background:linear-gradient(135deg,#e3ac5e,#c9773d);display:inline-block}
.search{margin-left:auto;margin-right:auto;width:420px;height:28px;border:1px solid var(--line2);border-radius:8px;background:var(--bg);
 display:flex;align-items:center;gap:8px;padding:0 10px;color:var(--dim);font-size:13px}
.search .k{margin-left:auto}
.kbd{display:inline-flex;align-items:center;justify-content:center;min-width:20px;height:20px;padding:0 5px;border-radius:5px;
 border:1px solid var(--line2);border-bottom-width:2px;background:var(--raised);color:var(--muted);font:500 11px/1 var(--mono)}
.who{font-size:12px;color:var(--dim)}
.main{display:grid;grid-template-columns:320px 1fr;min-height:0}
.status{display:flex;align-items:center;gap:14px;padding:0 14px;border-top:1px solid var(--line);background:var(--surface);
 font-size:12px;color:var(--dim);min-width:0}
.status .sep{width:1px;height:12px;background:var(--line2);flex:none}
.status .r{margin-left:auto;white-space:nowrap}

.inbox{border-right:1px solid var(--line);overflow:hidden;display:flex;flex-direction:column}
.ihead{display:flex;align-items:center;gap:8px;padding:14px 16px 10px}
.ihead h2{font-size:14px;margin:0;font-weight:600}
.count{font:500 11px/1 var(--mono);color:var(--dim);padding:3px 6px;border:1px solid var(--line2);border-radius:999px}
.ihead .filter{margin-left:auto;font-size:12px;color:var(--dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.group{padding:12px 16px 4px;font-size:11.5px;color:var(--dim);font-weight:600;letter-spacing:.04em;display:flex;justify-content:space-between}
.row{display:flex;gap:11px;padding:10px 12px;margin:0 6px;border-radius:10px;position:relative}
.row:hover{background:var(--hover)}
.row.sel{background:var(--raised);box-shadow:inset 0 0 0 1px var(--line2)}
.row.sel:before{content:"";position:absolute;left:-6px;top:12px;bottom:12px;width:3px;border-radius:3px;background:var(--amber)}
.dot{width:8px;height:8px;border-radius:50%;margin-top:6px;flex:none}
.dot.red{background:var(--red);box-shadow:0 0 0 3px var(--red-s)}.dot.amber{background:var(--amber);box-shadow:0 0 0 3px var(--amber-s)}
.dot.green{background:var(--green);box-shadow:0 0 0 3px var(--green-s)}.dot.grey{background:var(--dim)}
.dot.blue{background:var(--blue);box-shadow:0 0 0 3px var(--blue-s)}
.rt{flex:1;min-width:0}
.rt .ttl{font-size:13.5px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rt .why{font-size:12.5px;color:var(--muted);margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rt .meta{font:11.5px var(--mono);color:var(--dim);margin-top:3px}
.when{font-size:11.5px;color:var(--dim);white-space:nowrap}

.detail{overflow:hidden;display:flex;flex-direction:column;min-width:0}
.dscroll{padding:16px 32px 16px;overflow:hidden;min-width:0}
.crumb{font:12px var(--mono);color:var(--dim);display:flex;gap:8px;align-items:center}
.title{display:flex;align-items:center;gap:12px;margin:6px 0 4px;min-width:0}
.title h1{font-size:22px;letter-spacing:-.015em;margin:0;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pill{display:inline-flex;align-items:center;gap:6px;height:24px;padding:0 10px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap;flex:none}
.pill i{width:6px;height:6px;border-radius:50%;background:currentColor}
.pill.red{color:var(--red);background:var(--red-s)}.pill.amber{color:var(--amber);background:var(--amber-s)}
.pill.green{color:var(--green);background:var(--green-s)}.pill.grey{color:var(--muted);background:var(--raised)}
.pill.blue{color:var(--blue);background:var(--blue-s)}
.sub{color:var(--muted);font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sub .mono{color:var(--fg);font-family:var(--mono);font-size:12.5px}

.next{margin:14px 0 12px;display:flex;gap:14px;align-items:center;padding:14px 16px;border-radius:12px;border:1px solid var(--line2);background:var(--surface)}
.next .ic{width:32px;height:32px;border-radius:9px;display:grid;place-items:center;font-weight:700;flex:none}
.next.red .ic{background:var(--red-s);color:var(--red)}.next.amber .ic{background:var(--amber-s);color:var(--amber)}
.next.green .ic{background:var(--green-s);color:var(--green)}.next.grey .ic{background:var(--raised);color:var(--muted)}
.next.blue .ic{background:var(--blue-s);color:var(--blue)}
.next .tx{flex:1;min-width:0}.next .tx b{display:block;font-size:14.5px;font-weight:600}
.next .tx span{color:var(--muted);font-size:13px}
.btn{display:inline-flex;align-items:center;gap:8px;height:32px;padding:0 13px;border-radius:8px;font-size:13px;font-weight:500;
 border:1px solid var(--line2);background:var(--raised);color:var(--fg);white-space:nowrap;flex:none}
.btn.pri{background:var(--fg);color:#0b0b0c;border-color:var(--fg)}
.btn .kbd{height:18px;min-width:18px;font-size:10.5px}
.btn.pri .kbd{background:#d9d9d9;border-color:#bcbcbc;color:#333}

.lanes{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;margin:0 0 14px}
.lanes .lab{font-size:11px;color:var(--dim);font-weight:600;letter-spacing:.05em;padding:0 0 6px;border-bottom:1px solid var(--line)}
.lanes .lab.you{grid-column:3/5;padding-left:14px;border-left:1px dashed var(--line2)}
.lanes .lab.cs{grid-column:1/3}
.step{display:flex;gap:10px;align-items:flex-start;padding:10px 12px 0 0;min-width:0}
.step.y1{padding-left:14px;border-left:1px dashed var(--line2)}
.sic{width:20px;height:20px;border-radius:50%;display:grid;place-items:center;font-size:11px;font-weight:700;flex:none;margin-top:1px;border:1.5px solid var(--line2);color:var(--dim)}
.sic.ok{border-color:var(--green);color:var(--green);background:var(--green-s)}
.sic.bad{border-color:var(--red);color:var(--red);background:var(--red-s)}
.sic.wait{border-color:var(--amber);color:var(--amber);background:var(--amber-s)}
.sic.run{border-color:var(--blue);color:var(--blue);background:var(--blue-s)}
.step b{display:block;font-size:13px;font-weight:600}
.step span{font-size:12px;color:var(--muted);display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.step.blocking b{color:var(--fg)}.step.blocking span{color:var(--amber)}

.seg{display:inline-flex;gap:2px;padding:3px;border-radius:9px;background:var(--surface);border:1px solid var(--line);margin:0 0 10px}
.seg span{padding:5px 12px;border-radius:6px;font-size:13px;color:var(--muted)}
.seg span.on{background:var(--raised);color:var(--fg);box-shadow:inset 0 0 0 1px var(--line2)}
.seg em{font-style:normal;font:11px var(--mono);color:var(--dim);margin-left:5px}

.fc{border:1px solid var(--line2);border-radius:12px;background:var(--surface);margin:0 0 12px;overflow:hidden}
.fh{display:flex;align-items:center;gap:10px;padding:10px 14px}
.sev{font:600 10.5px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;padding:4px 7px;border-radius:5px;flex:none}
.sev.imp{color:var(--red);background:var(--red-s)}.sev.nit{color:var(--muted);background:var(--raised)}
.fh .ft{font-weight:600;font-size:14px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fh .loc{font:12px var(--mono);color:var(--muted);flex:none}
.fb{padding:0 14px 14px}
.fb p{margin:0 0 10px;color:var(--muted);font-size:13.5px;max-width:760px}
.code{border:1px solid var(--line);border-radius:9px;background:var(--bg);font:12.5px/1.65 var(--mono);overflow:hidden}
.code .cl{display:grid;grid-template-columns:44px 18px 1fr;padding:0 10px 0 0;white-space:pre}
.code .n{color:var(--dim);text-align:right;padding-right:10px}
.code .cl.del{background:var(--red-s)}.code .cl.del .m{color:var(--red)}
.code .cl.add{background:var(--green-s)}.code .cl.add .m{color:var(--green)}
.code .cl.hit{background:rgba(227,172,94,.07);box-shadow:inset 2px 0 0 var(--amber)}
.code .cap{display:flex;justify-content:space-between;padding:6px 12px;border-bottom:1px solid var(--line);font:11.5px var(--mono);color:var(--dim)}
.code .cap b{color:var(--green);font-weight:600}
.fx{display:flex;gap:8px;align-items:center;margin-top:10px}
.fx .btn{height:28px;font-size:12.5px}
details.how{margin-left:auto;font-size:12.5px;color:var(--dim)}
details.how summary{cursor:pointer;list-style:none}
.collapsed .fb{display:none}
.chev{color:var(--dim);font-size:12px}
.also{font-size:12px;color:var(--dim);padding:0 14px 12px}

.empty{grid-column:1/3;display:grid;place-items:center}
.ebox{width:520px;text-align:center}
.eart{width:72px;height:72px;margin:0 auto 18px;border-radius:18px;border:1px solid var(--line2);background:var(--surface);display:grid;place-items:center;box-shadow:0 0 0 8px rgba(255,255,255,.015),0 0 0 16px rgba(255,255,255,.01)}
.eart i{width:22px;height:22px;border-radius:6px;background:linear-gradient(135deg,#e3ac5e,#c9773d)}
.ebox h2{margin:0 0 6px;font-size:19px}
.ebox p{color:var(--muted);margin:0 0 20px}
.esteps{text-align:left;border:1px solid var(--line);border-radius:12px;background:var(--surface);overflow:hidden}
.esteps div{display:flex;gap:12px;align-items:center;padding:11px 14px;border-top:1px solid var(--line);font-size:13px}
.esteps div:first-child{border-top:0}
.esteps .num{width:22px;height:22px;border-radius:50%;display:grid;place-items:center;background:var(--raised);font:600 11px var(--mono);color:var(--muted);flex:none}
.esteps code{margin-left:auto;color:var(--muted);font-family:var(--mono);font-size:12px}

.warnbar{border:1px solid rgba(227,172,94,.35);background:var(--amber-s);border-radius:10px;padding:10px 14px;margin:0 0 12px;font-size:13px;color:var(--fg)}
.warnbar b{color:var(--amber)}
"""

LEVEL_TO_PILL = {"fail": "red", "warn": "amber", "pass": "green", "muted": "grey", "flight": "blue"}
# ◐ means work is happening now, and nothing else: a waiting decision shown as a spinner reads as
# activity and invites a reader to wait for it to finish.
ICON_TO_CLASS = {"done": ("ok", "✓"), "failed": ("bad", "✕"), "waiting": ("wait", "▸"),
                 "running": ("run", "◐"), "notstarted": ("", "○"), "notneeded": ("", "–")}
DOT_CLASS = {"fail": "red", "warn": "amber", "pass": "green", "muted": "grey", "flight": "blue"}


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _kbd(key: str) -> str:
    return f'<span class="kbd">{_esc(key)}</span>'


def _relative(seconds: int) -> str:
    if seconds <= 0:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _inbox(projection: Projection) -> str:
    repo = projection.repo or "no repository"
    out = [f'<aside class="inbox"><div class="ihead"><h2>Pull requests</h2>'
           f'<span class="count">{len(projection.rows)}</span>'
           f'<span class="filter">{_esc(repo)}</span></div>']
    for name, rows in projection.inbox_groups():
        out.append(f'<div class="group"><span>{_esc(name)}</span><span>{len(rows)}</span></div>')
        for row in rows:
            selected = " sel" if (row.repo == projection.repo and row.pr == projection.pr) else ""
            meta = f"#{row.pr} · {row.branch}" if row.branch else f"#{row.pr}"
            out.append(
                f'<div class="row{selected}">'
                f'<span class="dot {DOT_CLASS.get(row.level, "grey")}"></span>'
                f'<div class="rt"><div class="ttl">{_esc(row.title)}</div>'
                f'<div class="why">{_esc(row.reason)}</div>'
                f'<div class="meta">{_esc(meta)}</div></div>'
                f'<span class="when">{_relative(row.age_seconds)}</span></div>'
            )
    out.append("</aside>")
    return "".join(out)


def _header(projection: Projection) -> str:
    pill = ""
    if projection.chip:
        pill = (f'<span class="pill {LEVEL_TO_PILL.get(projection.level, "grey")}"><i></i>'
                f'{_esc(projection.chip)}</span>')
    return (
        f'<div class="crumb">{_esc(projection.repo)} <span>›</span> #{projection.pr}</div>'
        f'<div class="title"><h1>{_esc(projection.title)}</h1>{pill}</div>'
        f'<div class="sub">{_esc(projection.author or "someone")} wants to merge '
        f'<span class="mono">{_esc(projection.branch or "—")}</span> into '
        f'<span class="mono">{_esc(projection.base_ref or "—")}</span>'
        f' · head <span class="mono">{_esc(projection.head[:8] or "—")}</span></div>'
    )


def _next_bar(projection: Projection) -> str:
    headline, sub, action, label = projection.next_bar()
    icon = {"fail": "!", "warn": "!", "pass": "✓", "flight": "◐"}.get(projection.level, "›")
    live = projection.actions()
    buttons = []
    if action in live and label:
        key = {"fix": "f", "review": "r", "stop": "s", "refresh": "R"}.get(action)
        buttons.append(f'<span class="btn pri">{_esc(label)}{" " + _kbd(key) if key else ""}</span>')
    # "Open on GitHub" is the second action wherever a person acts on the pull request itself. Stop and
    # the empty state are the exceptions: there is nothing there for a person to do on GitHub.
    if projection.state not in ("empty", "reviewing"):
        buttons.append(f'<span class="btn">Open on GitHub {_kbd("o")}</span>')
    return (f'<div class="next {projection.level}"><div class="ic">{icon}</div>'
            f'<div class="tx"><b>{_esc(headline)}</b><span>{_esc(sub)}</span></div>'
            f'{"".join(buttons)}</div>')


def _lanes(projection: Projection) -> str:
    steps = projection.lanes()
    blocking = ""
    for name, state, _sub in steps:
        if state not in ("done", "notneeded"):
            blocking = name
            break
    out = ['<div class="lanes"><div class="lab cs">CODE-SHIPPER</div>'
           '<div class="lab you">YOU AND GITHUB</div>']
    for index, (name, state, sub) in enumerate(steps):
        cls, icon = ICON_TO_CLASS.get(state, ("", "○"))
        extra = (" y1" if index == 2 else "") + (" blocking" if name == blocking else "")
        out.append(f'<div class="step{extra}"><span class="sic {cls}">{icon}</span>'
                   f'<div><b>{_esc(name)}</b><span>{_esc(sub)}</span></div></div>')
    out.append("</div>")
    return "".join(out)


def _seg(projection: Projection) -> str:
    counts: dict[str, object] = {}
    if projection.findings:
        counts["Findings"] = len(projection.findings)
    if projection.approvals_needed_count:
        counts["Approvals"] = f"{projection.approvals_have}/{projection.approvals_needed_count}"
    parts = []
    for index, name in enumerate(projection.tabs):
        on = " on" if index == 0 else ""
        badge = f"<em>{counts[name]}</em>" if name in counts else ""
        parts.append(f'<span class="{on.strip()}">{_esc(name)}{badge}</span>')
    return f'<div class="seg">{"".join(parts)}</div>'


def _findings(projection: Projection) -> str:
    if not projection.findings:
        # The wording comes from the projection: in a review that did not complete, "No findings at this
        # head" would claim the code was examined when nothing was.
        return (f'<div class="fc"><div class="fh"><span class="ft">'
                f'{_esc(projection.findings_placeholder())}</span></div></div>')
    open_index = 0
    for index, finding in enumerate(projection.findings):
        if finding.severity.lower() in ("important", "blocking"):
            open_index = index
            break
    out = []
    for index, finding in enumerate(projection.findings):
        is_open = index == open_index
        important = finding.severity.lower() in ("important", "blocking")
        card = [f'<div class="fc{" collapsed" if not is_open else ""}">'
                f'<div class="fh"><span class="chev">{"▾" if is_open else "▸"}</span>'
                f'<span class="sev {"imp" if important else "nit"}">'
                f'{"IMPORTANT" if important else "NIT"}</span>'
                f'<span class="ft">{_esc(finding.title)}</span>'
                f'<span class="loc">{_esc(finding.path)}:{finding.line}</span></div>']
        if is_open:
            card.append('<div class="fb">')
            card.append(f'<p>{_esc(finding.why or finding.title)}</p>')
            card.append('<div class="code">')
            if finding.suggested_fix:
                card.append('<div class="cap"><span>Suggested fix · not applied · you decide</span>'
                            '<b>you decide</b></div>')
            for line in (finding.code or ()):
                hit = " hit" if line[1] == finding.line else ""
                card.append(f'<div class="cl{hit}"><span class="n">{line[1]}</span>'
                            f'<span class="m"></span><span>{_esc(line[2])}</span></div>')
            if finding.suggested_fix:
                card.append(f'<div class="cl del"><span class="n">{finding.line}</span>'
                            f'<span class="m">−</span>'
                            f'<span>{_esc(finding.suggested_fix[0])}</span></div>')
                card.append(f'<div class="cl add"><span class="n">{finding.line}</span>'
                            f'<span class="m">+</span>'
                            f'<span>{_esc(finding.suggested_fix[1])}</span></div>')
            card.append("</div>")
            card.append('<div class="fx">'
                        f'<span class="btn">Copy fix {_kbd("c")}</span>'
                        f'<span class="btn">Open on GitHub {_kbd("o")}</span>'
                        f'<span class="btn">Track this finding {_kbd("t")}</span>'
                        '<details class="how"><summary>How this was confirmed ▸</summary>'
                        f'<p style="margin-top:8px">'
                        f'{_esc(finding.evidence or "seen in the diff at this head")}</p>'
                        '</details></div>')
            card.append("</div>")
        card.append("</div>")
        out.append("".join(card))
        if finding.also_noted:
            out.append(f'<div class="also">Also noted on this line: '
                       f'{_esc(", ".join(finding.also_noted))}</div>')
    return "".join(out)


def _empty(projection: Projection) -> str:
    # The headline comes from the projection like every other state's: the empty state is not a licence
    # to hardcode copy, and a hardcoded sentence here is exactly how two surfaces drift apart.
    headline, sub, _action, _label = projection.next_bar()
    return (
        '<div class="empty"><div class="ebox">'
        '<div class="eart"><i></i></div>'
        f'<h2>{_esc(headline)}</h2>'
        f'<p>{_esc(sub)} It never approves and never merges.</p>'
        '<div class="esteps">'
        '<div><span class="num">1</span>Initialise a policy<code>code-shipper init</code></div>'
        '<div><span class="num">2</span>Review one pull request<code>code-shipper review 25</code></div>'
        '<div><span class="num">3</span>Or watch it for new commits<code>code-shipper watch 25</code></div>'
        "</div></div></div>"
    )


def _status_bar(projection: Projection) -> str:
    budget = projection.budget
    parts = [
        _esc(projection.chip or "Idle"),
        f"round {budget.fixed_rounds} of {budget.max_rounds}",
        f"pushes {budget.pushes} of {budget.max_pushes}",
        budget.clock_text(),
        budget.spend_text(),
    ]
    left = '<span class="sep"></span>'.join(f"<span>{p}</span>" for p in parts)
    return f'{left}<span class="r">Advisory. Code-Shipper never approves or merges.</span>'


def render(projection: Projection, *, title: str = "Code-Shipper") -> str:
    """The 1280x800 page as one self-contained HTML string."""
    if projection.state == "empty":
        main = f'<div class="main" style="grid-template-columns:1fr">{_empty(projection)}</div>'
    else:
        stale = ""
        if projection.head and not projection.head_pinned:
            stale = (f'<div class="warnbar"><b>Stale verdict.</b> Computed at '
                     f'{_esc(projection.reviewed_head[:8] or "none")}, but the head is '
                     f'{_esc(projection.head[:8])}. Review again before acting.</div>')
        refusal = ""
        if projection.refusal and projection.state == "error":
            refusal = ""  # the Next bar already carries the plain cause as its headline
        main = (f'<div class="main">{_inbox(projection)}'
                f'<section class="detail"><div class="dscroll">'
                f'{_header(projection)}{_next_bar(projection)}{_lanes(projection)}'
                f'{stale}{refusal}{_seg(projection)}{_findings(projection)}'
                f'</div></section></div>')

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{_esc(title)}</title>
<style>{CSS}</style></head><body>
<div class="win">
  <div class="bar">
    <div class="dots"><i></i><i></i><i></i></div>
    <div class="brand"><b></b>Code-Shipper</div>
    <div class="search"><span>Search pull requests or run a command</span>
      <span class="k">{_kbd("Ctrl")} {_kbd("K")}</span></div>
    <div class="who">{_esc(projection.who or "kensei · standard")}</div>
  </div>
  {main}
  <div class="status">{_status_bar(projection)}</div>
</div>
</body></html>"""
