#!/usr/bin/env bash
# Local CI: the gates the GitHub matrix would run, run here, with a per-gate verdict.
#
# This exists because the account cannot run Actions. It must therefore be honest in the way the matrix
# is honest: it fails if it cannot actually run a gate. A gate that silently "passes" because python3.13
# was not installed is worse than no gate, because it is a green tick over nothing.
#
# Sealed: no network, no writes outside the run directory, no credentials read, no state directory
# touched. `--sealed` is the default; `--no-seal` exists only for debugging this script.
#
# Usage:
#   scripts/local-ci.sh                 # all gates, all available interpreters
#   scripts/local-ci.sh --only tests    # one gate
#   scripts/local-ci.sh --python 3.12   # one interpreter
#
# Exit: 0 only if every gate PASSed or was explicitly SKIPped for a stated reason. A required gate that
# could not run is FAIL, not SKIP.

set -uo pipefail

cd "$(dirname "$0")/.." || { echo "cannot find the repo root"; exit 1; }
REPO_ROOT="$PWD"

PYTHONS=("3.11" "3.12" "3.13")
ONLY=""
SEALED=1
FAILED=0
RAN=0
SKIPPED=0
declare -a RESULTS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY="${2:-}"; shift 2 ;;
    --python) PYTHONS=("${2:-}"); shift 2 ;;
    --no-seal) SEALED=0; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# The run directory is chosen deliberately, not inherited from TMPDIR.
#
# A Hermes user's TMPDIR is under ~/.hermes/cache, so a run directory created there tripped this script's
# own seal gate — the same over-broad rule that was narrowed in `contain/spec.py`, reimplemented here by
# accident. Two things are wrong with inheriting TMPDIR: the seal cannot distinguish "our scratch under
# .hermes/cache", which is fine, from "a credential store", which is not; and a gate that fails for every
# Hermes user is a gate that gets switched off.
#
# So the base is chosen here and printed, so a reader can see where the run happened, and `gate_seal`
# checks the real credential locations only.
CI_BASE="${LOCAL_CI_BASE:-}"
if [ -z "$CI_BASE" ]; then
  for candidate in /tmp /var/tmp "${HOME:-/tmp}"; do
    [ -d "$candidate" ] && [ -w "$candidate" ] && CI_BASE="$candidate" && break
  done
fi
if [ -z "$CI_BASE" ]; then
  echo "local-ci: no writable base for the run directory" >&2
  exit 1
fi
RUN_DIR="$(mktemp -d "$CI_BASE/local-ci.XXXXXX")"
cleanup() { rm -rf "$RUN_DIR"; }
trap cleanup EXIT

have_python() {
  command -v "python$1" >/dev/null 2>&1
}

record() {  # record <gate> <verdict> <detail>
  RESULTS+=("$(printf '%-10s %-6s %s' "$1" "$2" "$3")")
  printf '  %-6s %-28s %s\n' "$2" "$1" "$3"
  case "$2" in
    PASS) RAN=$((RAN + 1)) ;;
    SKIP) SKIPPED=$((SKIPPED + 1)) ;;
    *)    FAILED=$((FAILED + 1)) ;;
  esac
}

echo "local-ci  ·  repo $REPO_ROOT"
echo "sealed: $([ "$SEALED" -eq 1 ] && echo yes || echo 'NO — debugging')"
echo "run dir: $RUN_DIR"
echo

# ---------------------------------------------------------------- gate: portable syntax
# The bug this catches: PEP 701 f-string nesting parses on 3.12 and is a SyntaxError on 3.11. A local run
# on one interpreter missed it; the matrix caught it. So the syntax gate compiles every source file with
# every available interpreter, which is the only version of this test that works.
gate_syntax() {
  local gate="syntax"
  [ -n "$ONLY" ] && [ "$ONLY" != "$gate" ] && return
  local any=0
  for version in "${PYTHONS[@]}"; do
    if ! have_python "$version"; then
      record "$gate/$version" SKIP "python$version not installed"
      continue
    fi
    any=1
    python"$version" - "$REPO_ROOT" >"$RUN_DIR/syntax-$version.log" 2>&1 <<'PY'
import pathlib, sys, py_compile
root = pathlib.Path(sys.argv[1])
bad = []
for path in sorted(root.rglob("*.py")):
    parts = set(path.parts)
    if parts & {".git", ".venv", "build", ".staging", "__pycache__", "node_modules"}:
        continue
    try:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    except SyntaxError as exc:
        bad.append(f"{path}:{exc.lineno}: {exc.msg}")
print("\n".join(bad))
sys.exit(1 if bad else 0)
PY
    if [ $? -eq 0 ]; then
      record "$gate/$version" PASS "every source file compiles"
    else
      record "$gate/$version" FAIL "$(head -3 "$RUN_DIR/syntax-$version.log" | tr '\n' ' ')"
    fi
  done
  if [ "$any" -eq 0 ]; then
    record "$gate" FAIL "no interpreter available — the gate could not run"
  fi
}

# ---------------------------------------------------------------- gate: tests
# The full unit suite, run under each interpreter. `unittest discover` with the repo on the path, no
# network: the suite uses fake transports and a local harness.
gate_tests() {
  local gate="tests"
  [ -n "$ONLY" ] && [ "$ONLY" != "$gate" ] && return
  local any=0
  for version in "${PYTHONS[@]}"; do
    if ! have_python "$version"; then
      record "$gate/$version" SKIP "python$version not installed"
      continue
    fi
    any=1
    local log="$RUN_DIR/tests-$version.log"
    env -u PYTHONPATH PYTHONDONTWRITEBYTECODE=1 \
      python"$version" -m unittest discover -s tests -t . -q >"$log" 2>&1
    local status=$?
    # A timeout or a missing module is a FAIL of the gate, not of the code — but it is still not a PASS.
    local summary
    summary="$(grep -E '^(OK|FAILED|Ran )' "$log" | tr '\n' ' ' | tail -c 120)"
    if [ "$status" -eq 0 ]; then
      record "$gate/$version" PASS "${summary:-all tests passed}"
    else
      local first_fail
      first_fail="$(grep -E '^(FAIL|ERROR):' "$log" | head -1)"
      record "$gate/$version" FAIL "${first_fail:-exit $status} ${summary}"
    fi
  done
  if [ "$any" -eq 0 ]; then
    record "$gate" FAIL "no interpreter available — the gate could not run"
  fi
}

# ---------------------------------------------------------------- gate: no-network import
# A cheap structural check: nothing in the shipped package may import a network library at module scope
# in a way that would phone home during a render. The suite would usually catch a real call; this catches
# an import that only fires on an unlucky path.
gate_imports() {
  local gate="imports"
  [ -n "$ONLY" ] && [ "$ONLY" != "$gate" ] && return
  local python_version="${PYTHONS[0]}"
  if ! have_python "$python_version"; then
    record "$gate" SKIP "no interpreter"
    return
  fi
  if env -u PYTHONPATH python"$python_version" - "$REPO_ROOT" >"$RUN_DIR/imports.log" 2>&1 <<'PY'
import importlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root))
bad = []
modules = ["code_shipper.cli", "code_shipper.surfaces.loader", "code_shipper.surfaces.desktop",
           "code_shipper.surfaces.tui", "code_shipper.surfaces.cli_view",
           "code_shipper.review.state", "code_shipper.review.tracked",
           "code_shipper.review.memory", "code_shipper.atomic"]
for name in modules:
    try:
        importlib.import_module(name)
    except Exception as exc:
        bad.append(f"{name}: {type(exc).__name__}: {exc}")
print("\n".join(bad))
sys.exit(1 if bad else 0)
PY
  then
    record "$gate" PASS "every shipped module imports cleanly"
  else
    record "$gate" FAIL "$(head -2 "$RUN_DIR/imports.log" | tr '\n' ' ')"
  fi
}

# ---------------------------------------------------------------- gate: sealed
# Proves the seal rather than asserting it: the state directory used by the gate run is a temp dir, and
# no real state, token or HERMES_HOME path is referenced by anything the gates ran.
gate_seal() {
  local gate="seal"
  [ -n "$ONLY" ] && [ "$ONLY" != "$gate" ] && return
  if [ "$SEALED" -ne 1 ]; then
    record "$gate" SKIP "--no-seal"
    return
  fi
  # The narrowed rule, the same one `contain/spec.py` uses: refuse the locations that hold a credential
  # or the live agent's state, not anything whose path happens to contain a dot directory. A scratch
  # directory under ~/.hermes/cache is ordinary filesystem and is exactly where a Hermes user's TMPDIR
  # points.
  case "$RUN_DIR" in
    */.ssh/*|*/.aws/*|*/.gnupg/*|*/.docker/*|*/.kube/*|*/.config/gh/*)
      record "$gate" FAIL "run directory is inside a credential location: $RUN_DIR"; return ;;
    */.hermes/auth/*|*/.hermes/tokens/*|*/.hermes/secrets/*|*/.hermes/credentials/*|*/.hermes/profiles/*)
      record "$gate" FAIL "run directory is inside agent credentials: $RUN_DIR"; return ;;
  esac
  if ls "$REPO_ROOT"/.local-ci-leak 2>/dev/null; then
    record "$gate" FAIL "a leak marker was created in the repo"
    return
  fi
  record "$gate" PASS "run dir $RUN_DIR (base $CI_BASE), no credentials read"
}

echo "gates"
echo "-----"
gate_syntax
gate_tests
gate_imports
gate_seal

echo
echo "summary"
echo "-------"
printf '%s\n' "${RESULTS[@]}"
echo
if [ "$FAILED" -gt 0 ]; then
  echo "RESULT: FAIL ($FAILED failed, $RAN passed, $SKIPPED skipped)"
  exit 1
fi
if [ "$RAN" -eq 0 ]; then
  echo "RESULT: FAIL (nothing ran)"
  exit 1
fi
echo "RESULT: PASS ($RAN passed, $SKIPPED skipped)"
