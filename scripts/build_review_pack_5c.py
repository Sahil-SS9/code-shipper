"""Build the Phase 5c review pack: one self-contained HTML file.

For every state it shows four surfaces:
  * Desktop, screenshotted from the real HTML at 1280x800,
  * the wide TUI as literal text,
  * the narrow TUI as literal text,
  * the CLI as literal text.

Images are inlined as data URIs, so the file opens over SSH with no route back to this machine and no
external asset. No JavaScript.

Run:  python3 scripts/build_review_pack_5c.py --out <path>
"""

from __future__ import annotations

import argparse
import base64
import html
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from code_shipper.surfaces import cli_view, desktop, fixtures, tui  # noqa: E402

WHY = {
    "empty": "First run. One sentence and three numbered steps, because an empty board has to be "
             "legible rather than blank.",
    "reviewing": "A review in flight. The Next bar says who is working and offers only Stop — there is "
                 "nothing to decide yet.",
    "findings": "Two important findings. The verdict is <b>Needs fixes</b>, the Next bar names who "
                "decides, and one finding card is open with the code and a suggested diff labelled "
                "\u201cnot applied\u201d.",
    "incomplete": "A review that did not finish. The verdict is <b>Incomplete, not a pass</b> and the "
                  "Next bar says it proves nothing. This is the state the D13 correction is about: on "
                  "GitHub it maps to <code>action_required</code>, never <code>neutral</code>.",
    "approvals-blocked": "Review is complete, approvals are short. The Next bar names how many people "
                         "and who, because \u201cneeds approval\u201d without a name is not actionable.",
    "approvals-satisfied": "Ready for you to merge. This is the terminal state: the lane for "
                           "\u201cYou merge\u201d is the reader's own step, and the footer says the tool "
                           "never approves or merges.",
    "observe": "A rule set to observe. It reports and does not block, and says which — an observed "
               "rule that looked enforced would be worse than either.",
    "paused": "Paused at the review cap. Distinct from stopped: this one waits for a person to raise "
              "the cap.",
    "stopped": "Stopped by a person, at a named round. Nothing is half-pushed.",
    "error": "An unsatisfiable policy. The plain cause is the Next bar's headline — never a stack "
             "trace, and never \u201c2 more approvals needed\u201d, which used to be false.",
}


def _tui(projection, width: int, height: int) -> str:
    return html.escape("\n".join(tui.render(projection, width=width, height=height)))


def _cli(projection) -> str:
    return html.escape("\n".join(cli_view.render_review(projection)))


def _screenshot(projection, workdir: Path) -> str:
    from playwright.sync_api import sync_playwright

    page_path = workdir / "page.html"
    page_path.write_text(desktop.render(projection), encoding="utf-8")
    with sync_playwright() as p:
        # Chromium is already installed for Playwright here; firefox would need a download.
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=2)
            page.goto(page_path.as_uri())
            page.wait_for_timeout(150)
            png = page.screenshot(full_page=False)
        finally:
            browser.close()
    return "data:image/png;base64," + base64.b64encode(png).decode()


SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Code-Shipper surfaces — Phase 5c review pack</title>
<style>
:root{--bg:#08090a;--surface:#0e0f11;--raised:#15161a;--line:#1e2025;--line2:#2a2d33;
--fg:#ececec;--muted:#a0a4ab;--dim:#878c95;--amber:#e3ac5e;--green:#5fc992;--red:#e8686e;--blue:#80aef0;
--sans:Inter,"SF Pro Text","Segoe UI",system-ui,sans-serif;
--mono:"JetBrains Mono","SF Mono",ui-monospace,Menlo,Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:#050506;color:var(--fg);font:14px/1.55 var(--sans)}
.wrap{max-width:1360px;margin:0 auto;padding:40px 32px 96px}
h1{font-size:28px;letter-spacing:-.02em;margin:0 0 6px;font-weight:600}
.lede{color:var(--muted);max-width:860px;margin:0 0 10px}
h2{font-size:20px;margin:56px 0 4px;padding-top:22px;border-top:1px solid var(--line);font-weight:600}
h2 .n{display:block;color:var(--dim);font:11px var(--mono);letter-spacing:.14em;text-transform:uppercase;margin-bottom:6px}
.why{color:var(--muted);max-width:900px;margin:0 0 16px}
.lbl{color:var(--dim);font:10.5px var(--mono);letter-spacing:.12em;text-transform:uppercase;margin:20px 0 7px}
pre{margin:0;background:var(--surface);border:1px solid var(--line);border-radius:9px;padding:14px 16px;
 overflow-x:auto;font:12px/1.4 var(--mono);color:#dfe2e6;white-space:pre}
img{width:100%;max-width:1280px;border:1px solid var(--line2);border-radius:10px;display:block}
code{font-family:var(--mono);font-size:12.5px;color:var(--amber)}
table{border-collapse:collapse;width:100%;font-size:13px;margin:14px 0}
th,td{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.06em}
.note{border:1px solid rgba(227,172,94,.35);background:rgba(227,172,94,.13);border-radius:10px;
 padding:12px 14px;margin:16px 0;font-size:13px}
.ok{color:var(--green)}.bad{color:var(--red)}
</style></head><body><div class="wrap">
<h1>Code-Shipper surfaces — Phase 5c review pack</h1>
<p class="lede">All ten states across all four surfaces, rendered from one projection. Nothing here is a
mock-up: the images are screenshots of the real page and the text blocks are literal program output.</p>
<p class="lede">Verdict names, Next-bar copy, lane states and inbox grouping all come from
<code>surfaces/projection.py</code>. No surface holds any of it.</p>
__SECTIONS__
</div></body></html>"""


def build(out: Path) -> Path:
    sections = []
    total = len(fixtures.SCREENSHOT_ORDER)
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for index, name in enumerate(fixtures.SCREENSHOT_ORDER, start=1):
            projection = fixtures.ALL[name]()
            shot = _screenshot(projection, workdir)
            sections.append(f"""
<h2><span class="n">State {index} of {total}</span>{html.escape(name)}</h2>
<p class="why">{WHY.get(name, '')}</p>
<div class="lbl">Desktop — 1280 × 800, real HTML, screenshotted</div>
<img src="{shot}" alt="Desktop, {html.escape(name)} state">
<div class="lbl">TUI — wide, 124 columns, literal output</div>
<pre>{_tui(projection, 124, 34)}</pre>
<div class="lbl">TUI — narrow, 60 columns, literal output</div>
<pre>{_tui(projection, 60, 20)}</pre>
<div class="lbl">CLI — literal output</div>
<pre>{_cli(projection)}</pre>
""")

    sections.append("""
<h2><span class="n">Reference</span>status — the list, with its interval</h2>
<p class="why">One row per pull request, and the check interval stated, because "watched" is
meaningless without it.</p>
<div class="lbl">CLI — literal output</div>
<pre>__STATUS__</pre>
""".replace("__STATUS__", html.escape("\n".join(cli_view.render_status(
        fixtures.status_board(), repo="Sahil-SS9/code-shipper-sandbox")))))

    out.parent.mkdir(parents=True, exist_ok=True)
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
