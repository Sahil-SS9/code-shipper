#!/bin/sh
# The one verification entry point for this repo.
#
#   ./scripts/run_tests.sh          unit tests (fast, no Docker, no network)
#   ./scripts/run_tests.sh --full   unit tests plus the CP2 contained-run battery
#
# `--full` needs the rootless Docker context and a cached worker image; it makes no network calls
# and uses synthetic credentials only. Both modes exit non-zero on the first failure.
set -eu

cd "$(dirname "$0")/.."

# -B: never write bytecode. A stale .pyc once made a reverted fix look applied, because the edit
# preserved the file size and CPython's mtime check is second-granular.
PYTHONDONTWRITEBYTECODE=1
export PYTHONDONTWRITEBYTECODE

echo "== unit tests"
python3 -B -m unittest discover -s tests -t . -v 2>&1 | tail -n 20

[ "${1:-}" = "--full" ] || exit 0

echo
echo "== CP2: contained run"
python3 -B spikes/b1/cp2_run.py | tail -n 8

echo
echo "== CP2: fail closed, broker stopped before the run"
python3 -B spikes/b1/cp2_run.py --stop-broker-before-run | tail -n 4

echo
echo "== CP2: fail closed, broker stopped mid-run"
python3 -B spikes/b1/cp2_run.py --stop-broker-mid-run | tail -n 4
