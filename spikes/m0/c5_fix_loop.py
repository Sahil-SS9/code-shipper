"""Phase 0 spike: one fix loop end to end.

1. Owner (gh login) opens PR C with a bug that fails CI.
2. Engine reads the failing CI log (owner read), redacts it, prepares a worktree.
3. Agent = Hermes one-shot in the worktree, scrubbed env (no GH_/GITHUB_/*_TOKEN vars). It edits
   files only; it is told not to use git or gh, and it gets no token in its environment.
   NOTE: unsandboxed (bubblewrap is blocked on this machine), so credential FILES stay readable;
   see c4 results. This run proves the loop, not the isolation.
4. Engine checks diff scope, commits, and the broker pushes with the fixer token (fast-forward
   only, hooks off), then comments what changed and how to undo it.
5. Wait for CI on the new head.
"""
import base64
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

REPO = "Sahil-SS9/code-shipper-sandbox"
CLONE = Path.home() / "repos/code-shipper-sandbox"
TOKEN = Path.home().joinpath(".config/code-shipper/tokens/fixer").read_text().strip()
SCRATCH = Path(os.environ.get("TMPDIR", "/tmp"))
WT = SCRATCH / "cs-m0-wt"
OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)
BRANCH = "spike/pr-c"
SCOPE = re.compile(r"^(calc/.*|tests/.*)$")
log = {}


def sh(*a, cwd=CLONE, env=None, check=True):
    p = subprocess.run(list(a), cwd=cwd, capture_output=True, text=True, env=env)
    if check and p.returncode:
        raise SystemExit(f"FAILED {a[:3]}: {(p.stderr + p.stdout)[-400:]}")
    return p.stdout.strip()


def redact(text):
    text = text.replace(TOKEN, "***")
    return re.sub(r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9-]{20,})", "***", text)


def wait_ci(sha, want_done=True, timeout=300):
    end = time.time() + timeout
    while time.time() < end:
        runs = json.loads(sh("gh", "run", "list", "-R", REPO, "--commit", sha, "--json", "status,conclusion,databaseId"))
        if runs and runs[0]["status"] == "completed":
            return runs[0]
        time.sleep(10)
    return {"status": "timeout"}


# 1. owner opens PR C with a bug
sh("git", "checkout", "-q", "main"); sh("git", "pull", "-q", "--ff-only")
sh("git", "checkout", "-q", "-B", BRANCH)
src = (CLONE / "calc/__init__.py").read_text().replace("return a / b", "return a * b  # refactor")
(CLONE / "calc/__init__.py").write_text(src)
sh("git", "commit", "-q", "-am", "Refactor divide")
sh("git", "push", "-q", "-f", "origin", BRANCH)
existing = json.loads(sh("gh", "pr", "list", "-R", REPO, "--head", BRANCH, "--state", "open", "--json", "number"))
if existing:
    pr = existing[0]["number"]
else:
    pr_url = sh("gh", "pr", "create", "-R", REPO, "--head", BRANCH, "--base", "main",
                "--title", "Spike PR C (fix loop)", "--body", "Deliberately broken: divide multiplies.")
    pr = int(pr_url.rsplit("/", 1)[1])
head = sh("git", "rev-parse", "HEAD")
log.update(pr=pr, bad_head=head)
print("PR", pr, "head", head[:7], "waiting for CI...")
run = wait_ci(head)
log["ci_before"] = run
print("CI before fix:", run.get("conclusion"))
ci_log = sh("gh", "run", "view", str(run["databaseId"]), "-R", REPO, "--log-failed", check=False)
ci_tail = redact("\n".join(ci_log.splitlines()[-200:]))

# 2. worktree at the PR head
if WT.exists():
    sh("git", "worktree", "remove", "--force", str(WT), check=False)
    shutil.rmtree(WT, ignore_errors=True)
sh("git", "worktree", "add", "-q", "--detach", str(WT), head)

# 3. agent run (Hermes one-shot), scrubbed env
prompt = f"""You are fixing a failing CI check on a pull request. Work only in the current directory.
Rules: edit files only. Do not run git, gh, curl or any network command. Do not push anything.
Fix the bug in the code that the PR changed so the tests pass; do not weaken or delete tests.
When done, reply with only this JSON: {{"status": "changed|no_change|declined", "summary": "<one sentence>"}}

<untrusted source="ci_log">
{ci_tail}
</untrusted>
"""
keep = {"PATH", "LANG", "LC_ALL", "TERM", "HOME", "HERMES_HOME", "USER", "LOGNAME", "TMPDIR"}
env = {k: v for k, v in os.environ.items() if k in keep}
assert not any(k.startswith(("GH_", "GITHUB_")) or k.endswith("_TOKEN") for k in env)
t0 = time.time()
p = subprocess.run(["hermes", "chat", "--query-file", "-", "--oneshot", "-Q", "--source", "tool",
                    "--max-turns", "20", "--run-budget", "600"],
                   input=prompt, cwd=WT, capture_output=True, text=True, env=env, timeout=900)
log["agent"] = {"rc": p.returncode, "seconds": round(time.time() - t0), "tail": redact(p.stdout[-800:])}
print("agent rc", p.returncode, "in", log["agent"]["seconds"], "s:", redact(p.stdout.strip()[-300:]))

# 4. scope check, commit, broker push
sh("git", "clean", "-fdqX", cwd=WT)  # drop ignored build junk; untracked junk below fails the scope check
for junk in WT.rglob("__pycache__"):
    shutil.rmtree(junk, ignore_errors=True)
changed = sh("git", "diff", "--name-only", cwd=WT).splitlines() + \
    sh("git", "ls-files", "--others", "--exclude-standard", cwd=WT).splitlines()
log["changed"] = changed
bad = [c for c in changed if not SCOPE.match(c)]
if not changed or bad:
    raise SystemExit(f"scope check failed: changed={changed} out_of_scope={bad}")
local = subprocess.run(["uv", "run", "--quiet", "--with", "pytest", "python", "-m", "pytest", "-q"],
                       cwd=WT, capture_output=True, text=True)
log["local_tests"] = local.stdout.strip().splitlines()[-1:]
print("local tests:", log["local_tests"])
sh("git", "-c", "user.name=Code-Shipper (spike)", "-c", "user.email=noreply@example.invalid",
   "commit", "-q", "-am", "Fix divide (Code-Shipper spike)", cwd=WT)
new = sh("git", "rev-parse", "HEAD", cwd=WT)
remote_now = sh("git", "ls-remote", "origin", f"refs/heads/{BRANCH}").split()[0]
assert remote_now == head, f"head moved: {remote_now} != {head}"   # exact-head check
basic = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
penv = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
            GIT_ASKPASS="/bin/false")
empty_hooks = SCRATCH / "cs-empty-hooks"; empty_hooks.mkdir(exist_ok=True)
push = subprocess.run(["git", "-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {basic}",
                       "-c", f"core.hooksPath={empty_hooks}", "push", "origin", f"{new}:refs/heads/{BRANCH}"],
                      cwd=WT, capture_output=True, text=True, env=penv)
log["push"] = {"rc": push.returncode, "out": redact((push.stderr + push.stdout)[-300:])}
print("broker push rc", push.returncode)
body = (f"Code-Shipper (spike) pushed `{new[:7]}`: fixed the failing check.\n\n"
        f"Changed: {', '.join(changed)}\n\nUndo: `git revert {new[:7]}`")
sh("gh", "api", f"repos/{REPO}/issues/{pr}/comments", "-f", f"body={body}")  # TODO in build: via broker role

# 5. CI on new head
run2 = wait_ci(new)
log["ci_after"] = run2
print("CI after fix:", run2.get("conclusion"))
sh("git", "worktree", "remove", "--force", str(WT), check=False)
(OUT / "fix-loop-results.json").write_text(json.dumps(log, indent=2))
