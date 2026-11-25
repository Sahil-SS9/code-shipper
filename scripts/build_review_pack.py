"""Build the review pack Sahil opens: one self-contained HTML file.

It contains, for every state:
  * the TUI board, rendered as text in a `<pre>` (no image needed — it is already text), and
  * a real screenshot of the Desktop page, inlined as a base64 data URI.

Inlining matters: Sahil opens this over SSH with no route to my machine, so a page that referenced
`./shot-3.png` would show broken images. One file, no external assets, no JavaScript.

Run:  python3 scripts/build_review_pack.py --out <path>
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from code_shipper.surfaces import desktop, fixtures, tui  # noqa: E402


def _tui_block(projection) -> str:
    lines = tui.render(projection, width=113, height=30)
    return html.escape("\n".join(lines))


def _screenshot(projection, workdir: Path) -> str:
    """A PNG of the Desktop page, as a data URI."""
    from playwright.sync_api import sync_playwright

    page_path = workdir / "page.html"
    page_path.write_text(desktop.render(projection), encoding="utf-8")
    with sync_playwright() as p:
        # Chromium is already installed for Playwright here; firefox would need a download.
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
            page.goto(page_path.as_uri())
            page.wait_for_timeout(120)
            png = page.screenshot(full_page=False)
        finally:
            browser.close()
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _compact_block(projection) -> str:
    lines = tui.render(projection, width=60)
    return html.escape("\n".join(lines))


SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Code-Shipper surfaces — review pack</title>
<style>
:root{--bg:#0a0d0f;--panel:#13181b;--line:#252d31;--text:#d4dad5;--muted:#8b968f;--dim:#5d6a63;
--amber:#e0a458;--green:#7fbf7a;--red:#d96a5f;
--mono:ui-monospace,"JetBrains Mono",Menlo,Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--mono);font-size:15px;line-height:1.55}
.wrap{max-width:1180px;margin:0 auto;padding:32px 24px 80px}
h1{font-size:24px;font-weight:500;margin:0 0 6px}
.lede{color:var(--muted);margin:0 0 28px}
h2{font-size:18px;font-weight:500;margin:44px 0 4px;padding-top:18px;border-top:1px solid var(--line)}
h2 .n{color:var(--dim);font-size:12px;letter-spacing:.12em;text-transform:uppercase;display:block;margin-bottom:4px}
.why{color:var(--muted);font-size:13px;margin:0 0 14px}
.lbl{color:var(--dim);font-size:11px;letter-spacing:.12em;text-transform:uppercase;margin:18px 0 6px}
pre{background:var(--panel);border:1px solid var(--line);padding:14px;overflow-x:auto;font-size:12px;line-height:1.35;margin:0;white-space:pre}
img{width:100%;border:1px solid var(--line);display:block}
.note{background:var(--panel);border-left:2px solid var(--amber);padding:12px 14px;color:var(--muted);font-size:13px;margin:16px 0}
table{border-collapse:collapse;width:100%;font-size:13px;margin:12px 0}
th,td{border:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top}
th{color:var(--dim);font-size:11px;letter-spacing:.1em;text-transform:uppercase;font-weight:500}
code{color:var(--amber)}
.pass{color:var(--green)} .fail{color:var(--red)}
</style></head><body><div class="wrap">
<h1>Code-Shipper surfaces — Phase 5b review pack</h1>
<p class="lede">Every screen and state, rendered from one projection. Nothing here is a mock-up: these
are the real renderers, driven by fixtures. The TUI blocks below are literal text output; the Desktop
images are screenshots of the real HTML.</p>
__SECTIONS__
</div></body></html>"""

WHY = {
    "empty": "Nothing being watched. The inbox is the landing surface, so an empty state has to be "
             "legible rather than a blank panel.",
    "reviewing": "A review in flight. Note that <b>stop</b> and <b>cancel</b> are separate keys here: "
                 "stopping ends the watch after the current round settles, cancelling kills the run.",
    "findings": "Two important findings. Verdict is NEEDS-FIX, pinned to the head it was computed at. "
                "Signals stay four: CI is green, approval is not.",
    "incomplete": "A review that did not finish. The verdict is withheld and the surface says so — an "
                  "incomplete review is not a clean bill of health.",
    "approvals-blocked": "Review is fine, approvals are short. The blocker names the count so the "
                         "reader knows whether it is reachable.",
    "approvals-satisfied": "Ready for a human merge. This is the terminal state: one action, open the PR "
                           "on GitHub. On Desktop the merge and approve controls are visible but "
                           "disabled, with their reasons.",
    "observe": "A rule set to observe. It reports and does not block — and says which it is, because "
               "an observed rule that looked enforced would be worse than either.",
    "paused": "The per-PR review cap was reached. Paused is a distinct state from stopped: one waits for "
              "a human to raise the cap, the other is finished.",
    "stopped": "The watch was stopped. The last round settled, so nothing is half-pushed.",
    "error": "An unsatisfiable policy: count 2 with one named approver. This used to render as "
             "\u201c2 more approvals needed\u201d, which was false. It now refuses and names the fix.",
}


def build(out: Path) -> Path:
    sections = []
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for index, name in enumerate(fixtures.SCREENSHOT_ORDER, start=1):
            projection = fixtures.ALL[name]()
            shot = _screenshot(projection, workdir)
            sections.append(f"""
<h2><span class="n">State {index} of {len(fixtures.SCREENSHOT_ORDER)}</span>{html.escape(name)}</h2>
<p class="why">{WHY.get(name, '')}</p>
<div class="lbl">Desktop — real HTML, screenshotted</div>
<img src="{shot}" alt="Desktop, {html.escape(name)} state">
<div class="lbl">TUI — literal text output, 113 columns</div>
<pre>{_tui_block(projection)}</pre>
<div class="lbl">TUI — degraded, 60 columns</div>
<pre>{_compact_block(projection)}</pre>
""")

    out.parent.mkdir(parents=True, exist_ok=True)
    # A placeholder replace, not %-formatting: the CSS contains literal % characters.
    out.write_text(SHELL.replace("__SECTIONS__", "".join(sections)), encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    target = build(Path(args.out).expanduser())
    print(f"wrote {target} ({target.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
