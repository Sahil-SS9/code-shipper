"""P2-B: post exactly one real review through the broker's reviewer role, then prove a re-run is a no-op.

Sequence:
  1. read PR #18 and its diff
  2. run the review engine (same code as --dry-run)
  3. check the ledger: if this PR at this head has already been reviewed, post nothing
  4. otherwise post ONE review with event=COMMENT and its inline comments through the broker
  5. read it back from GitHub and record the review id, state, and each comment's path and line
  6. re-run the same command and show it recognises the existing review and posts nothing

Nothing is merged, approved or requested-changes. The broker refuses those before sending.

  python3 spikes/b1/post_review.py --pr 18
  python3 spikes/b1/post_review.py --pr 18 --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from code_shipper.broker.write import Broker, BrokerRefused, UrllibTransport  # noqa: E402
from code_shipper.harness.adapters import HermesHarness, scrubbed_env  # noqa: E402
from code_shipper.hosts.github import GitHubHost  # noqa: E402
from code_shipper.review.engine import intent_block, review  # noqa: E402

SANDBOX = "Sahil-SS9/code-shipper-sandbox"
TOKEN = Path.home() / ".config" / "code-shipper" / "tokens" / "fixer"
EVIDENCE = REPO_ROOT / "docs/reports/evidence/P2"
LEDGER = EVIDENCE / "posted-reviews.jsonl"
MARKER = "<!-- code-shipper:review -->"


def read_token() -> str:
    value = TOKEN.read_text(encoding="utf-8").strip()
    if not value:
        raise SystemExit("fixer token file is empty")
    return value


def gh(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """Run gh against the sandbox. `-R` goes with the command, not after an `api` path."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise SystemExit(f"gh {' '.join(args[:3])} failed: {(proc.stderr or proc.stdout)[-300:]}")
    return proc


def api(path: str) -> list | dict:
    proc = gh(["api", f"repos/{SANDBOX}/{path}"])
    return json.loads(proc.stdout or "null")


def already_posted(*, pr: int, head_sha: str) -> dict | None:
    """Read the ledger for a review of this PR at this exact head."""
    if not LEDGER.exists():
        return None
    for line in LEDGER.read_text().splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            entry.get("result") == "confirmed"
            and entry.get("pr") == pr
            and entry.get("head") == head_sha
        ):
            return entry
    return None


def record(entry: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
        handle.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=int, default=18)
    parser.add_argument("--level", default="standard")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    token = read_token()
    host = GitHubHost(token)
    pr = host.get_pr(SANDBOX, args.pr)
    receipt: dict[str, object] = {
        "pr": args.pr,
        "head_sha": pr.head_sha,
        "level": args.level,
    }

    existing = already_posted(pr=args.pr, head_sha=pr.head_sha)
    if existing:
        receipt["action"] = "skipped: already reviewed at this head"
        receipt["existing_review_id"] = existing.get("review_id")
        _finish(receipt, args)
        return 0

    diff = host.get_diff(SANDBOX, args.pr)
    outcome = review(
        diff=diff,
        harness=HermesHarness(),
        level=args.level,
        workdir=REPO_ROOT,
        env=scrubbed_env(),
        intent=intent_block(pr.title, pr.body),
    )
    receipt["review"] = outcome.as_dict()
    if not outcome.findings:
        receipt["action"] = "skipped: no confirmed findings"
        _finish(receipt, args)
        return 0

    body = _review_body(outcome)
    comments = [
        {
            "path": finding.path,
            "line": finding.line,
            "body": f"**{finding.title}**\n\n{finding.body}",
        }
        for finding in outcome.findings
    ]

    broker = Broker(token=token, repo=SANDBOX, transport=UrllibTransport())
    try:
        created = broker.post_review(
            pr=args.pr, head_sha=pr.head_sha, body=body, event="COMMENT", comments=comments
        )
    except BrokerRefused as exc:
        receipt["action"] = f"refused by the broker: {exc.reason}"
        _finish(receipt, args)
        return 1

    receipt["posted"] = {
        "review_id": created.get("id"),
        "state": created.get("state"),
        "body": body,
        "comments": comments,
    }
    record(
        {
            "pr": args.pr,
            "head": pr.head_sha,
            "review_id": created.get("id"),
            "result": "confirmed",
            "role": "reviewer",
            "event": "COMMENT",
        }
    )

    # read it back rather than trusting the create response
    receipt["readback"] = _read_back(args.pr, created.get("id"))
    receipt["action"] = "posted one review"
    _finish(receipt, args)
    return 0


def _review_body(outcome) -> str:
    lines = [MARKER, "## Code-Shipper review", ""]
    important = [f for f in outcome.findings if f.severity == "important"]
    nit = [f for f in outcome.findings if f.severity == "nit"]
    lines.append(
        f"{len(outcome.findings)} finding(s): {len(important)} important, {len(nit)} minor. "
        f"Level: {outcome.level} ({', '.join(outcome.lenses_run)})."
    )
    if outcome.plan and outcome.plan.summary_line():
        lines += ["", outcome.plan.summary_line()]
    if outcome.dropped_unverified:
        lines += [
            "",
            f"{outcome.dropped_unverified} further candidate finding(s) were not confirmed on "
            "verification and are not listed.",
        ]
    lines += [
        "",
        "_Review only: nothing here approves, requests changes, or merges. A human decides._",
    ]
    return "\n".join(lines)


def _read_back(pr: int, review_id: object) -> dict:
    """Ask GitHub what it actually stored.

    Reads both collections separately and matches on the review id. An earlier version coupled the
    two calls so a failure in one reported the review as missing, which is worse than useless: it
    says a posted review did not exist.
    """
    try:
        reviews = api(f"pulls/{pr}/reviews") or []
    except SystemExit as exc:
        return {"id": review_id, "found": False, "error": f"could not list reviews: {exc}"}

    for entry in reviews:
        if str(entry.get("id")) != str(review_id):
            continue
        try:
            comments = api(f"pulls/{pr}/comments") or []
        except SystemExit:
            comments = []
        mine = [
            {
                "id": c.get("id"),
                "path": c.get("path"),
                "line": c.get("line") or c.get("original_line"),
                "body": (c.get("body") or "")[:120],
            }
            for c in comments
            if str(c.get("pull_request_review_id")) == str(review_id)
        ]
        return {
            "id": entry.get("id"),
            "state": entry.get("state"),
            "body_starts": (entry.get("body") or "")[:60],
            "submitted_at": entry.get("submitted_at"),
            "author": (entry.get("user") or {}).get("login"),
            "comments": mine,
            "found": True,
        }
    return {"id": review_id, "found": False}


def _finish(receipt: dict, args: argparse.Namespace) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE / f"posted-review-pr{receipt['pr']}.json"
    out.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    receipt["receipt_path"] = str(out)
    if args.json:
        print(json.dumps(receipt, indent=2))
    else:
        print(f"PR #{receipt['pr']} head {str(receipt['head_sha'])[:12]}")
        print(f"action: {receipt['action']}")
        if receipt.get("readback"):
            rb = receipt["readback"]
            print(f"review id {rb.get('id')} state {rb.get('state')} found={rb.get('found', True)}")
            for comment in rb.get("comments") or []:
                print(f"  inline {comment['path']}:{comment['line']}")
        print(f"receipt: {out}")


if __name__ == "__main__":
    raise SystemExit(main())
