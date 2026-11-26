#!/usr/bin/env python3
"""Run the clean-seed measurement with independent seeds in parallel.

The sequential runner takes ~6 hours for 20 seeds because one slow lens costs the full timeout and
everything behind it waits. The seeds share nothing — each is its own file set, its own temporary
directory, its own review — so the only reason they ran in series was that the loop was written that way.

What this does NOT change: the reviewer, the prompts, the lenses, the verdicts. Same pipeline, same
adjudication, more workers.

Isolation, because parallel work invites cross-talk:

* each seed gets its own temporary directory, removed in its own worker;
* each worker builds its own harness instance — no shared client, no shared connection;
* results are collected by a lock and keyed by slug, so completion order does not matter;
* a worker that raises records an error for its seed and does not take the run down with it.

Concurrency is capped at 2 in practice, and that is a measured limit rather than a preference. At 4
workers, every lens in a 4-seed batch timed out: the per-lens budget is 300s, and four concurrent
`hermes chat` calls slow each other past it, so the harness reports `harness timed out` for lenses that
answer fine at 2. Measured: `--workers 4` gave 4/4 UNEVALUATED twice; `--workers 2` gave 2/2 evaluated.
More workers than the provider tolerates does not measure more, it measures nothing.

Usage:
    python3 scripts/run_measurement_parallel.py --workers 4 --out docs/reports/evidence/hosted-20.json
    python3 scripts/run_measurement_parallel.py --workers 4 --dry-run     # verify seeds only
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from code_shipper.harness.adapters import HermesHarness, OpenAICompatHarness  # noqa: E402
from measure_clean_seeds import SEEDS, check_seed, write_seed  # noqa: E402
from run_clean_measurement import diff_for  # noqa: E402

from code_shipper.review.engine import review  # noqa: E402

_PRINT_LOCK = threading.Lock()


def say(message: str) -> None:
    with _PRINT_LOCK:
        print(message, flush=True)


def build_harness(args):
    """A harness per worker.

    Not shared: the Hermes harness shells out, and a shared instance across threads would serialise on
    one process anyway and make failures harder to attribute.
    """
    if args.harness == "hermes":
        return HermesHarness(hermes_bin=args.hermes_bin)
    return OpenAICompatHarness(args.base_url, os.environ.get("CODE_SHIPPER_LLM_KEY", "local"), args.model)


def run_one(seed, args, work_root: Path) -> dict:
    """One seed, start to finish, in its own directory with its own harness."""
    root = work_root / seed.slug
    root.mkdir(parents=True, exist_ok=True)
    write_seed(seed, root)

    clean, why = check_seed(seed, root)
    if not clean:
        # The seed's own control failed: the seed is not clean, so it cannot measure the reviewer.
        return {"slug": seed.slug, "status": "discarded", "why": why}

    harness = build_harness(args)
    started = time.monotonic()
    try:
        outcome = review(
            diff=diff_for(seed),
            harness=harness,
            level=args.level,
            workdir=str(root),
            intent=seed.title,
        )
    except Exception as exc:  # noqa: BLE001 - one seed failing must not end the run
        return {"slug": seed.slug, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
    elapsed = time.monotonic() - started

    if not outcome.lenses_run or outcome.errors:
        # A run where a lens errored measured nothing. Counting it as clean is how a broken harness
        # reports perfect accuracy.
        return {
            "slug": seed.slug, "status": "unevaluated",
            "errors": outcome.errors, "lenses_run": outcome.lenses_run,
        }

    findings = [f.as_dict() for f in outcome.findings]
    return {
        "slug": seed.slug,
        "status": "evaluated",
        "title": seed.title,
        "seconds": round(elapsed, 1),
        "raw_findings": outcome.raw_findings,
        "confirmed": outcome.confirmed,
        "posted": len(findings),
        "findings": findings,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=2,
                        help="2 is the measured ceiling: at 4 every lens times out (see module docstring)")
    parser.add_argument("--limit", type=int, default=len(SEEDS))
    parser.add_argument("--harness", choices=("hermes", "openai"), default="hermes")
    parser.add_argument("--hermes-bin", default="/home/kensei/.local/bin/hermes")
    parser.add_argument("--base-url", default="http://127.0.0.1:8091")
    parser.add_argument("--model", default="auto")
    parser.add_argument("--level", default="standard")
    parser.add_argument("--out", default="")
    parser.add_argument("--seeds", default="",
                        help="comma-separated slugs to run; default is the first --limit seeds")
    parser.add_argument("--dry-run", action="store_true", help="verify the seeds only")
    args = parser.parse_args(argv[1:])

    if args.seeds:
        wanted = [slug.strip() for slug in args.seeds.split(",") if slug.strip()]
        known = {seed.slug for seed in SEEDS}
        missing = [slug for slug in wanted if slug not in known]
        if missing:
            parser.error(f"unknown seed(s): {', '.join(missing)}")
        selected = [seed for seed in SEEDS if seed.slug in wanted]
    else:
        selected = SEEDS[: args.limit]
    if args.dry_run:
        work = Path(tempfile.mkdtemp(prefix="dry-par-"))
        bad = 0
        for seed in selected:
            root = work / seed.slug
            root.mkdir(parents=True, exist_ok=True)
            write_seed(seed, root)
            clean, why = check_seed(seed, root)
            print(f"  {'clean' if clean else 'DISCARD'}  {seed.slug}" + ("" if clean else f": {why}"))
            bad += 0 if clean else 1
        shutil.rmtree(work, ignore_errors=True)
        print(f"\n{len(selected) - bad} clean, {bad} discarded")
        return 0 if bad == 0 else 1

    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    work_root = Path(tempfile.mkdtemp(prefix="par-measure-"))
    say(f"harness: {args.harness} · workers: {args.workers} · seeds: {len(selected)}")
    say(f"work root: {work_root}\n")

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one, seed, args, work_root): seed for seed in selected}
        for future in as_completed(futures):
            seed = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - a worker crash is a result, not a stop
                result = {"slug": seed.slug, "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}
            results.append(result)

            if result["status"] == "evaluated":
                n = result["posted"]
                if n:
                    say(f"  {result['slug']:<12} {n} finding(s) in {result['seconds']:.0f}s")
                    for finding in result["findings"] or []:
                        if not isinstance(finding, dict):
                            continue
                        say(f"               → {finding.get('severity')}: {finding.get('title')}")
                else:
                    say(f"  {result['slug']:<12} clean ({result['seconds']:.0f}s)")
            elif result["status"] == "unevaluated":
                say(f"  {result['slug']:<12} UNEVALUATED ({len(result['errors'])} error(s))")
            elif result["status"] == "discarded":
                say(f"  {result['slug']:<12} DISCARDED — its own control failed")
            else:
                say(f"  {result['slug']:<12} ERROR {result.get('error', '')}")

    # Order matters for reading the evidence: sort by the seed order, not by completion.
    order = {seed.slug: index for index, seed in enumerate(selected)}
    results.sort(key=lambda r: order.get(r["slug"], 999))

    evaluated = [r for r in results if r["status"] == "evaluated"]
    unevaluated = [r for r in results if r["status"] == "unevaluated"]
    discarded = [r for r in results if r["status"] == "discarded"]
    errored = [r for r in results if r["status"] == "error"]

    false_confirms = []
    for run in evaluated:
        for finding in run["findings"] or []:
            if not isinstance(finding, dict):
                continue
            false_confirms.append({
                "slug": run["slug"],
                "title": finding.get("title", ""),
                "path": finding.get("path", ""),
                "line": finding.get("line", 0),
                "severity": finding.get("severity", ""),
                "text": finding.get("body", ""),
            })
    seeds_with_findings = len({f["slug"] for f in false_confirms})

    report = {
        "started": started_at,
        "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "harness_kind": args.harness,
        "workers": args.workers,
        "level": args.level,
        "seeds_attempted": len(selected),
        "seeds_evaluated": len(evaluated),
        "seeds_unevaluated": len(unevaluated),
        "unevaluated_detail": [
            {"slug": r["slug"], "errors": r.get("errors", [])} for r in unevaluated
        ],
        "seeds_discarded": discarded,
        "seeds_errored": errored,
        "seeds_with_findings": seeds_with_findings,
        "finding_count": len(false_confirms),
        "rate": (seeds_with_findings / len(evaluated)) if evaluated else None,
        "runs": evaluated,
        "findings": false_confirms,
    }

    say("")
    say(f"attempted   : {len(selected)}")
    say(f"evaluated   : {len(evaluated)}")
    say(f"unevaluated : {len(unevaluated)}  (lens error — excluded from the rate)")
    say(f"discarded   : {len(discarded)}  (own control failed — excluded from the rate)")
    say(f"errored     : {len(errored)}")
    say(f"findings    : {len(false_confirms)} across {seeds_with_findings} seed(s)")
    if report["rate"] is not None:
        say(f"rate        : {report['rate']:.0%} of evaluated seeds "
            f"({seeds_with_findings}/{len(evaluated)})")
    else:
        say("rate        : not computed — no seed was evaluated")

    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = REPO / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        say(f"wrote {out}")

    shutil.rmtree(work_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
