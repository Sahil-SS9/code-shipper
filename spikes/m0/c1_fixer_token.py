"""Phase 0 spike, candidate C1: what can the fixer token do on GitHub?

Throwaway script. Reads the token from a file and never prints it.
Owner-side setup (branches, PRs) uses the owner's existing `gh` login.
Writes a JSON results file to spikes/m0/out/ (gitignored) and prints a summary.
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = "Sahil-SS9/code-shipper-sandbox"
OTHER_REPO = "Sahil-SS9/Severian"  # a private repo the token was NOT granted
TOKEN = Path.home().joinpath(".config/code-shipper/tokens/fixer").read_text().strip()
CLONE = Path.home() / "repos/code-shipper-sandbox"
OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)
results = []


def api(method, path, body=None, token=TOKEN, graphql=False):
    url = "https://api.github.com" + ("/graphql" if graphql else path)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode()
            return r.status, (json.loads(txt) if txt else {})
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt)
        except ValueError:
            return e.code, {"raw": txt[:300]}


def record(test, op, expected, status, body):
    msg = ""
    if isinstance(body, dict):
        msg = body.get("message") or ""
        if body.get("errors"):
            msg += " | " + json.dumps(body["errors"])[:300]
    ok = expected(status, body)
    results.append({"test": test, "op": op, "status": status, "message": msg[:400], "as_expected": ok})
    print(f"{test:14} {op:48} -> {status} {'OK ' if ok else 'UNEXPECTED'} {msg[:140]}")


def gh(*args):
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout.strip()


def git(*args, token=None, cwd=CLONE):
    cmd = ["git"]
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        cmd += ["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {basic}",
                "-c", "credential.helper="]
    cmd += list(args)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if token:  # no owner credential helper may be reachable: only the fixer header authenticates
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_ASKPASS="/bin/false",
                   SSH_ASKPASS="/bin/false")
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    return p.returncode, (p.stderr + p.stdout).strip().replace(TOKEN, "***")[-400:]


def owner_branch_pr(name, filename, content, title):
    git("checkout", "-q", "main")
    git("pull", "-q", "--ff-only")
    git("checkout", "-q", "-B", name)
    (CLONE / filename).write_text(content)
    git("add", filename)
    git("commit", "-q", "-m", title)
    rc, out = git("push", "-q", "-f", "origin", name)
    assert rc == 0, out
    url = gh("pr", "create", "-R", REPO, "--head", name, "--base", "main", "--title", title, "--body", "Phase 0 spike PR")
    return int(url.rsplit("/", 1)[1])


# --- identity and scope -------------------------------------------------------
st, me = api("GET", "/user")
record("ID", "GET /user (who is the token)", lambda s, b: s == 200, st, {"message": me.get("login", "")})
st, b = api("GET", f"/repos/{REPO}")
record("SCOPE", f"GET {REPO}", lambda s, b: s == 200, st, {"message": json.dumps(b.get("permissions", {}))})
st, b = api("GET", f"/repos/{OTHER_REPO}")
record("SCOPE", f"GET {OTHER_REPO} (not granted)", lambda s, b: s == 404, st, b)

# --- owner setup: two PRs -----------------------------------------------------
pr_a = owner_branch_pr("spike/pr-a", "note_a.txt", "pr a\n", "Spike PR A (push/approve tests)")
pr_b = owner_branch_pr("spike/pr-b", "note_b.txt", "pr b\n", "Spike PR B (merge tests)")
print(f"PR A #{pr_a}, PR B #{pr_b}")
time.sleep(3)

# --- T-SPIKE-01 push to PR branch with fixer token -----------------------------
git("checkout", "-q", "spike/pr-a")
(CLONE / "note_a.txt").write_text("pr a, edited by fixer token\n")
git("commit", "-q", "-am", "Fixer token push test")
rc, out = git("push", "origin", "spike/pr-a", token=TOKEN)
record("T-SPIKE-01", "git push to PR branch (fixer token)", lambda s, b: s == 0, rc, {"message": out[-200:]})
st, commits = api("GET", f"/repos/{REPO}/pulls/{pr_a}/commits", token=TOKEN)
record("T-SPIKE-01b", "PR A now has 2 commits", lambda s, b: s == 200 and len(b) == 2, st,
       {"message": f"{len(commits) if isinstance(commits, list) else '?'} commits"})

# --- comments ------------------------------------------------------------------
st, b = api("POST", f"/repos/{REPO}/issues/{pr_a}/comments", {"body": "Spike: fixer token comment test"})
record("COMMENT", "POST issue comment on PR (fixer)", lambda s, b: s == 201, st, b)

# --- T-SPIKE-02 approve / review ------------------------------------------------
st, b = api("POST", f"/repos/{REPO}/pulls/{pr_a}/reviews", {"event": "APPROVE", "body": "spike"})
record("T-SPIKE-02", "POST review APPROVE (fixer)", lambda s, b: s in (403, 404), st, b)
st, b = api("POST", f"/repos/{REPO}/pulls/{pr_a}/reviews", {"event": "COMMENT", "body": "spike"})
record("T-SPIKE-02b", "POST review COMMENT (fixer)", lambda s, b: s in (403, 404), st, b)

# --- T-SPIKE-03 merge paths on PR B --------------------------------------------
st, b = api("PUT", f"/repos/{REPO}/pulls/{pr_b}/merge", {"merge_method": "merge"})
record("T-SPIKE-03a", "PUT /pulls/B/merge (fixer)", lambda s, b: s in (403, 404, 405), st, b)
st, pr = api("GET", f"/repos/{REPO}/pulls/{pr_b}", token=TOKEN)
node = pr.get("node_id")
st, b = api("POST", "", {"query": "mutation($id:ID!){mergePullRequest(input:{pullRequestId:$id}){pullRequest{merged}}}",
                         "variables": {"id": node}}, graphql=True)
gql_ok = lambda s, b: not (b.get("data") or {}).get("mergePullRequest")
record("T-SPIKE-03b", "GraphQL mergePullRequest (fixer)", gql_ok, st,
       {"message": json.dumps(b.get("errors") or b.get("data"))[:300]})
st, b = api("POST", "", {"query": "mutation($id:ID!){enablePullRequestAutoMerge(input:{pullRequestId:$id}){clientMutationId}}",
                         "variables": {"id": node}}, graphql=True)
record("T-SPIKE-03c", "GraphQL enablePullRequestAutoMerge (fixer)",
       lambda s, b: not (b.get("data") or {}).get("enablePullRequestAutoMerge"), st,
       {"message": json.dumps(b.get("errors") or b.get("data"))[:300]})

# --- direct writes that bypass a PR ---------------------------------------------
git("checkout", "-q", "main")
git("pull", "-q", "--ff-only")
(CLONE / "direct_main.txt").write_text("fixer token pushed straight to main\n")
git("add", "direct_main.txt")
git("commit", "-q", "-m", "Spike: fixer token direct push to main")
rc, out = git("push", "origin", "main", token=TOKEN)
record("T-SPIKE-03d", "git push straight to main (fixer)", lambda s, b: s != 0, rc, {"message": out[-200:]})
rc, out = git("push", "origin", "HEAD:refs/tags/spike-tag", token=TOKEN)
record("TAG", "git push tag (fixer)", lambda s, b: s != 0, rc, {"message": out[-200:]})
rc, out = git("push", "origin", "--delete", "spike/pr-b", token=TOKEN)
record("DELBRANCH", "git push --delete branch (fixer)", lambda s, b: s != 0, rc, {"message": out[-200:]})
git("fetch", "-q", "origin")
rc, sha = git("rev-parse", "HEAD")
st, b = api("POST", f"/repos/{REPO}/statuses/{sha}", {"state": "success", "context": "spike/status"})
record("STATUS", "POST commit status (fixer)", lambda s, b: s in (403, 404), st, b)

(OUT / "c1-results.json").write_text(json.dumps({"pr_a": pr_a, "pr_b": pr_b, "results": results}, indent=2))
print("unexpected:", [r["test"] for r in results if not r["as_expected"]])
