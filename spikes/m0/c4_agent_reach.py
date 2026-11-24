"""Phase 0 spike, candidate C4: what credentials can an agent process reach?

Runs the same probe twice: (1) scrubbed env + private HOME, no sandbox; (2) inside bubblewrap
with credential paths hidden. The probe reports which credential sources it could read.
It never prints a credential: only "reachable" / "not reachable" and a length.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REAL_HOME = str(Path.home())
CRED_PATHS = [
    f"{REAL_HOME}/.config/gh/hosts.yml",
    f"{REAL_HOME}/.config/code-shipper/tokens/fixer",
    f"{REAL_HOME}/.git-credentials",
    f"{REAL_HOME}/.hermes/.env",
    f"{REAL_HOME}/.ssh/id_ed25519",
    f"{REAL_HOME}/.ssh/id_rsa",
]
HIDE_DIRS = [f"{REAL_HOME}/.config/gh", f"{REAL_HOME}/.config/code-shipper", f"{REAL_HOME}/.hermes",
             f"{REAL_HOME}/.ssh", f"{REAL_HOME}/.local/share/keyrings"]

PROBE = r'''
import json, os, subprocess
paths = json.loads(os.environ["PROBE_PATHS"])
out = {"env_github_vars": sorted(k for k in os.environ if k.startswith(("GH_", "GITHUB_")) or k.endswith("_TOKEN"))}
files = {}
for p in paths:
    try:
        with open(p, "rb") as f:
            files[p] = f"REACHABLE ({len(f.read())} bytes)"
    except FileNotFoundError:
        files[p] = "absent"
    except PermissionError:
        files[p] = "denied"
    except OSError as e:
        files[p] = f"error {e.errno}"
out["files"] = files
try:
    r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=20)
    out["gh_auth_token"] = f"REACHABLE ({len(r.stdout.strip())} chars)" if r.returncode == 0 and r.stdout.strip() else f"no (rc={r.returncode})"
except Exception as e:
    out["gh_auth_token"] = f"no ({type(e).__name__})"
try:
    r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=20,
                       env=dict(os.environ, GH_CONFIG_DIR=os.environ["REAL_HOME"] + "/.config/gh"))
    out["gh_auth_token_real_config_dir"] = f"REACHABLE ({len(r.stdout.strip())} chars)" if r.returncode == 0 and r.stdout.strip() else f"no (rc={r.returncode})"
except Exception as e:
    out["gh_auth_token_real_config_dir"] = f"no ({type(e).__name__})"
print(json.dumps(out))
'''


def scrubbed_env(home):
    keep = {"PATH", "LANG", "LC_ALL", "TERM"}
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.update(HOME=home, PROBE_PATHS=json.dumps(CRED_PATHS), REAL_HOME=REAL_HOME)
    return env


def run(label, argv, env):
    p = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=60)
    try:
        res = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        res = {"error": (p.stderr or p.stdout)[-400:]}
    print(f"== {label}")
    print(json.dumps(res, indent=1))
    return res


with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as home:
    env = scrubbed_env(home)
    a = run("unsandboxed: scrubbed env + private HOME", [sys.executable, "-c", PROBE], env)
    bw = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
          "--bind", home, home, "--unshare-pid", "--die-with-parent"]
    for d in HIDE_DIRS:
        if os.path.isdir(d):
            bw += ["--tmpfs", d]
    for f in [f"{REAL_HOME}/.git-credentials"]:
        if os.path.exists(f):
            bw += ["--ro-bind", "/dev/null", f]
    b = run("bubblewrap: credential dirs hidden", bw + [sys.executable, "-c", PROBE], env)

out = Path(__file__).parent / "out"
out.mkdir(exist_ok=True)
(out / "c4-results.json").write_text(json.dumps({"unsandboxed": a, "bwrap": b}, indent=2))
