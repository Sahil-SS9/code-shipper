#!/usr/bin/env python3
"""Run the real review pipeline over clean seeds and count false confirms.

This is the measurement, not a simulation. It builds a `Diff` from each seed's files the way a GitHub patch
would look, calls the same `review()` the CLI calls, and records what came back.

What counts as a false confirm: a finding that survives verification and reaches the posting plan on a
pull request whose own control check passes. Every one is recorded with its text, because "one finding"
and "one blocking finding about a real line" are different claims.

What does not count: a seed that fails its own control. Those are discarded and reported — the first
measurement counted them as false confirms, and that was wrong.

    python3 scripts/run_clean_measurement.py --limit 20 --out docs/reports/evidence/clean-run.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from code_shipper.harness.adapters import HermesHarness, OpenAICompatHarness  # noqa: E402
from code_shipper.hosts.base import Diff, FilePatch  # noqa: E402
from code_shipper.review.engine import review  # noqa: E402
from scripts.measure_clean_seeds import SEEDS, check_seed, write_seed  # noqa: E402


def patch_for(path: str, content: str) -> FilePatch:
    """A unified diff for a newly added file, as GitHub would present it."""
    lines = content.splitlines()
    body = [f"@@ -0,0 +1,{len(lines)} @@"]
    body.extend(f"+{line}" for line in lines)
    return FilePatch(path=path, status="added", old_path=path, patch="\n".join(body))


def diff_for(seed) -> Diff:
    return Diff(files=tuple(patch_for(path, content) for path, content in seed.files.items()))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--out", default="")
    # The adapter appends `/v1/chat/completions` itself, so this is the host, not the API root. Passing
    # ".../v1" produced ".../v1/v1/chat/completions" and every lens failed with a 503 that looked like a
    # provider outage.
    parser.add_argument("--harness", choices=("hermes", "openai"), default="hermes",
                        help="hermes = the CLI's configured (hosted) model, no credential handled here")
    parser.add_argument("--hermes-bin", default="/home/kensei/.local/bin/hermes")
    parser.add_argument("--base-url", default=os.environ.get("CODE_SHIPPER_LLM_BASE",
                                                             "http://127.0.0.1:8091"))
    parser.add_argument("--model", default=os.environ.get("CODE_SHIPPER_LLM_MODEL", "auto"))
    parser.add_argument("--level", default="standard")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv[1:])

    if args.harness == "hermes":
        # The hosted path a real user runs: `hermes chat` with whatever provider that install is
        # configured for. No API key passes through this script, and none is read from disk.
        harness = HermesHarness(hermes_bin=args.hermes_bin)
        args.model = "hermes-configured"
    else:
        api_key = os.environ.get("CODE_SHIPPER_LLM_KEY", "local")
        harness = OpenAICompatHarness(args.base_url, api_key, args.model)
    available, detail = harness.available()
    if not available:
        print(f"harness unavailable: {detail}", file=sys.stderr)
        return 2
    print(f"harness: {detail}")

    work = Path(tempfile.mkdtemp(prefix="clean-run-"))
    results = {
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "harness": detail,
        "harness_kind": args.harness,
        "model": args.model,
        "base_url": args.base_url,
        "level": args.level,
        "seeds_checked": 0,
        "seeds_discarded": [],
        "seeds_run": [],
        "runs": [],
        "false_confirms": [],
        "merged": 0,
        "missed_merges": 0,
    }

    for seed in SEEDS[: args.limit]:
        root = work / seed.slug
        root.mkdir(parents=True, exist_ok=True)
        write_seed(seed, root)
        clean, why = check_seed(seed, root)
        results["seeds_checked"] += 1
        if not clean:
            # Not a false confirm. A defective seed measures nothing about the reviewer.
            results["seeds_discarded"].append({"slug": seed.slug, "why": why})
            print(f"  DISCARD {seed.slug}: control failed — {why}")
            continue

        diff = diff_for(seed)
        print(f"  review  {seed.slug} … ", end="", flush=True)
        started = time.monotonic()
        try:
            outcome = review(
                diff=diff,
                harness=harness,
                level=args.level,
                workdir=str(root),
                intent=seed.title,
            )
        except Exception as exc:  # noqa: BLE001 - a crash is a result, not a reason to stop
            results["runs"].append({"slug": seed.slug, "error": f"{type(exc).__name__}: {exc}"})
            print(f"ERROR {type(exc).__name__}: {exc}")
            continue
        elapsed = time.monotonic() - started

        findings = [f.as_dict() for f in outcome.findings]

        # A run where lenses errored measured nothing. Counting it as "clean" is how a broken harness
        # reports perfect accuracy — which is exactly what happened on the first attempt: three lenses
        # failed with HTTP 503 and the tool said "0 false confirms".
        evaluated = bool(outcome.lenses_run) and not outcome.errors
        if not evaluated:
            results.setdefault("unevaluated", []).append({
                "slug": seed.slug,
                "errors": outcome.errors,
                "lenses_run": outcome.lenses_run,
            })
            print(f"UNEVALUATED ({len(outcome.errors)} lens error(s)) — not counted")
            if not outcome.lenses_run and not outcome.errors:
                print("          no lens ran and no lens reported an error: the run stopped before the lens")
            for error in outcome.errors:
                print(f"          {error}")
            continue

        results["seeds_run"].append(seed.slug)
        run = {
            "slug": seed.slug,
            "title": seed.title,
            "seconds": round(elapsed, 1),
            "lenses_run": outcome.lenses_run,
            "raw_findings": outcome.raw_findings,
            "confirmed": outcome.confirmed,
            "posted": len(findings),
            "complete": outcome.complete,
            "errors": outcome.errors,
            "findings": findings,
        }
        results["runs"].append(run)

        if findings:
            # The seed's own control passed, so anything posted here is a false confirm.
            for finding in findings:
                results["false_confirms"].append({
                    "slug": seed.slug,
                    "title": finding.get("title", ""),
                    "path": finding.get("path", ""),
                    "line": finding.get("line", 0),
                    "severity": finding.get("severity", ""),
                    "text": finding.get("body", ""),
                })
            print(f"{len(findings)} false confirm(s) in {elapsed:.0f}s")
            for finding in findings:
                print(f"          → {finding.get('severity')}: {finding.get('title')}")
        else:
            print(f"clean ({elapsed:.0f}s, {outcome.lenses_run and len(outcome.lenses_run)} lenses)")

        if args.verbose and outcome.errors:
            for error in outcome.errors:
                print(f"          note: {error}")

    reviewed = len(results["seeds_run"])
    results["reviewed"] = reviewed
    results["false_confirm_count"] = len(results["false_confirms"])
    results["seeds_with_false_confirm"] = len({f["slug"] for f in results["false_confirms"]})
    results["rate"] = (results["seeds_with_false_confirm"] / reviewed) if reviewed else None
    results["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    print()
    print(f"reviewed {reviewed} clean seeds, {len(results['seeds_discarded'])} discarded")
    print(f"false confirms: {results['false_confirm_count']} across "
          f"{results['seeds_with_false_confirm']} seed(s)")
    if results["rate"] is not None:
        print(f"rate: {results['rate']:.0%} of clean seeds ({results['seeds_with_false_confirm']}"
              f"/{reviewed})")

    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = REPO / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"wrote {out}")

    import shutil

    shutil.rmtree(work, ignore_errors=True)
    return 1 if results["false_confirm_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
