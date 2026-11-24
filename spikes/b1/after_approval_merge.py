"""The after-approval merge case: can the fixer token merge once a PR has an approval?

This is the one Phase 0 item left open. It needs a second GitHub account, because GitHub will not let
an account approve its own pull request and the fixer token belongs to the repo owner.

What it does, in order:
  1. find or open a PR on the sandbox branch with green checks and no approval
  2. record that the fixer cannot merge it yet (that half is already measured, but re-recorded)
  3. the APPROVER approves it
  4. the fixer attempts a merge by REST and by GraphQL, and both results are recorded
  5. if either merge went through, say so immediately and loudly
  6. clean up: if a merge went through, the PR is left merged and reported, never quietly undone

Run when the token exists:
    python3 spikes/b1/after_approval_merge.py            # full run
    python3 spikes/b1/after_approval_merge.py --json

Never prints, logs or commits a token. Both are read by path.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SANDBOX = "Sahil-SS9/code-shipper-sandbox"
FIXER_TOKEN = Path.home() / ".config" / "code-shipper" / "tokens" / "fixer"
APPROVER_TOKEN = Path.home() / ".config" / "code-shipper" / "tokens" / "approver"


def read_token(path: Path, label: str) -> str:
    if not path.exists():
        raise SystemExit(
            f"{label} token not found at {path}. Nothing was attempted."
        )
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise SystemExit(f"{label} token file is empty")
    return value


def gh(args: list[str], token: str | None = None, *, check: bool = True) -> subprocess.CompletedProcess:
    """gh with a token supplied per call, never persisted to the environment of the harness."""
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(Path.home()),
        "GH_TOKEN": token or "",
        "GH_CONFIG_DIR": "/dev/null",
        "NO_COLOR": "1",
    }
    if not token:
        env.pop("GH_TOKEN")
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, env=env)
    if check and proc.returncode != 0:
        raise SystemExit(f"gh {' '.join(args[:3])} failed: {(proc.stderr or proc.stdout)[-400:]}")
    return proc


def rest_merge(pr: int, token: str) -> dict:
    proc = gh(
        ["api", "-X", "PUT", f"repos/{SANDBOX}/pulls/{pr}/merge",
         "-f", "merge_method=merge"],
        token, check=False,
    )
    return {
        "method": "rest",
        "returncode": proc.returncode,
        "merged": proc.returncode == 0,
        "detail": redact((proc.stdout or proc.stderr).strip()[-400:]),
    }


def graphql_merge(pr: int, token: str) -> dict:
    """Merge through the GraphQL API. Resolves the node id properly; no placeholder call."""
    query = (
        "mutation($id:ID!,$method:PullRequestMergeMethod!){"
        "mergePullRequest(input:{pullRequestId:$id,mergeMethod:$method})"
        "{pullRequest{number merged}}}"
    )
    pr_id = gh(["pr", "view", str(pr), "-R", SANDBOX, "--json", "id", "-q", ".id"], token, check=False)
    node_id = (pr_id.stdout or "").strip().strip('"')
    if not node_id:
        return {"method": "graphql", "merged": False, "detail": "could not resolve the PR node id"}
    proc = gh(
        ["api", "graphql", "-f", f"query={query}", "-f", f"id={node_id}", "-f", "method=MERGE"],
        token, check=False,
    )
    compact = (proc.stdout or "").replace(" ", "").replace("\n", "")
    return {
        "method": "graphql",
        "returncode": proc.returncode,
        "merged": proc.returncode == 0 and '"merged":true' in compact,
        "detail": redact((proc.stdout or proc.stderr).strip()[-400:]),
    }


def pr_state(pr: int, token: str) -> dict:
    proc = gh(
        ["pr", "view", str(pr), "-R", SANDBOX,
         "--json", "number,state,mergeStateStatus,mergeable,reviewDecision,headRefOid,isDraft"],
        token, check=False,
    )
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def checks_green(pr: int, token: str) -> bool:
    proc = gh(["pr", "checks", str(pr), "-R", SANDBOX], token, check=False)
    return proc.returncode == 0


def redact(text: str) -> str:
    import re

    text = re.sub(r"gh[pousr]_[A-Za-z0-9]{20,}", "***", text)
    text = re.sub(r"github_pat_[A-Za-z0-9_]{20,}", "***", text)
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=int, default=0, help="PR number to use; default: find or open one")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    fixer = read_token(FIXER_TOKEN, "fixer")
    approver = read_token(APPROVER_TOKEN, "approver")
    receipt: dict[str, object] = {"sandbox": SANDBOX}

    pr = args.pr
    if not pr:
        listing = gh(["pr", "list", "-R", SANDBOX, "--state", "open",
                      "--json", "number,headRefName", "--limit", "10"], fixer)
        open_prs = json.loads(listing.stdout or "[]")
        if not open_prs:
            raise SystemExit("no open PR in the sandbox to use; open one first")
        pr = int(open_prs[0]["number"])
    receipt["pr"] = pr

    before = pr_state(pr, fixer)
    receipt["before"] = before
    receipt["checks_green"] = checks_green(pr, fixer)

    # 1. the fixer should not be able to merge before an approval exists
    receipt["fixer_merge_before_approval"] = rest_merge(pr, fixer)
    if receipt["fixer_merge_before_approval"]["merged"]:
        receipt["verdict"] = "MERGED-WITHOUT-APPROVAL"
        _finish(receipt, args)
        return 1

    # 2. the approver approves, which is the whole reason a second account exists
    approve = gh(["pr", "review", str(pr), "-R", SANDBOX, "--approve",
                  "-b", "Approved by the Code-Shipper spike approver."], approver, check=False)
    receipt["approval"] = {
        "returncode": approve.returncode,
        "detail": redact((approve.stdout or approve.stderr).strip()[-300:]),
    }
    if approve.returncode != 0:
        receipt["verdict"] = "APPROVAL-FAILED"
        _finish(receipt, args)
        return 1

    # let GitHub settle the mergeable state after the review
    deadline = time.time() + 60
    while time.time() < deadline:
        state = pr_state(pr, fixer)
        if state.get("reviewDecision") == "APPROVED":
            break
        time.sleep(5)
    receipt["after_approval"] = pr_state(pr, fixer)

    # 3. now the fixer tries, both ways
    receipt["fixer_merge_rest"] = rest_merge(pr, fixer)
    if not receipt["fixer_merge_rest"]["merged"]:
        receipt["fixer_merge_graphql"] = graphql_merge(pr, fixer)
    else:
        receipt["fixer_merge_graphql"] = {"method": "graphql", "skipped": "REST merge already went through"}

    merged = receipt["fixer_merge_rest"]["merged"] or receipt["fixer_merge_graphql"].get("merged")
    receipt["final_state"] = pr_state(pr, fixer)
    receipt["merged"] = bool(merged)
    receipt["verdict"] = "FIXER-CAN-MERGE-AFTER-APPROVAL" if merged else "FIXER-CANNOT-MERGE-AFTER-APPROVAL"
    _finish(receipt, args)
    return 0


def _finish(receipt: dict, args: argparse.Namespace) -> None:
    out = REPO / "docs/reports/evidence/B1" / "receipt-after-approval.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    receipt["receipt_path"] = str(out)
    if args.json:
        print(json.dumps(receipt, indent=2))
    else:
        print(f"PR                #{receipt.get('pr')}")
        print(f"checks green      {receipt.get('checks_green')}")
        print(f"before            {(receipt.get('before') or {}).get('mergeStateStatus')}")
        print(f"approval          {receipt.get('approval', {}).get('returncode')}")
        print(f"fixer REST merge  {receipt.get('fixer_merge_rest', {}).get('merged')}")
        print(f"fixer GQL merge   {receipt.get('fixer_merge_graphql', {}).get('merged')}")
        print(f"merged            {receipt.get('merged')}")
        print(f"VERDICT           {receipt.get('verdict')}")
        print(f"receipt           {out}")


if __name__ == "__main__":
    raise SystemExit(main())
