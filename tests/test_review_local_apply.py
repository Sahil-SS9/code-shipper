"""`review --local --apply`: contained fixes written back to the developer's checkout, safely."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from code_shipper.review.local_apply import apply_findings, export_tree, local_scope


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                               "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin", "HOME": str(repo)}).stdout


@dataclass
class Finding:
    title: str = "clamp ignores hi"
    severity: str = "important"
    path: str = "calc.py"
    line: int = 2
    fingerprint: str = "f1"


class EditingWorker:
    """Stands in for the container: edits the workspace copy, never the checkout."""

    def __init__(self, edits: dict[str, str], during=None) -> None:
        self.edits, self.during, self.seen = edits, during, []

    def run(self, *, workspace, item, prompt, budget):
        self.seen.append(sorted(p.relative_to(workspace.checkout).as_posix()
                                for p in workspace.checkout.rglob("*") if p.is_file() and ".git" not in p.parts))
        for rel, text in self.edits.items():
            (workspace.checkout / rel).parent.mkdir(parents=True, exist_ok=True)
            (workspace.checkout / rel).write_text(text)
        if self.during:
            self.during()
        return {"ok": True, "reply_tail": ""}


FIXED = "def clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n"


class ApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(tempfile.mkdtemp(prefix="cs-apply-"))
        self.state = Path(tempfile.mkdtemp(prefix="cs-apply-state-"))
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "calc.py").write_text("def clamp(x, lo, hi):\n    return lo\n")
        (self.repo / "other.py").write_text("X = 1\n")
        (self.repo / ".gitignore").write_text(".env\n.venv/\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")
        self.base = git(self.repo, "rev-parse", "HEAD").strip()
        (self.repo / "calc.py").write_text("def clamp(x, lo, hi):\n    return max(lo, x)\n")
        (self.repo / ".env").write_text("SECRET=do-not-copy\n")
        (self.repo / ".venv").mkdir()
        (self.repo / ".venv" / "big.py").write_text("pass\n")

    def tearDown(self) -> None:
        shutil.rmtree(self.repo, ignore_errors=True)
        shutil.rmtree(self.state, ignore_errors=True)

    def _apply(self, worker, findings=None, scope=None):
        return apply_findings(repo_dir=self.repo, findings=findings or [Finding()], changed_paths=["calc.py"],
                              merge_base=self.base, worker=worker, scope_config=scope,
                              staging_parent=self.state)

    def test_an_in_scope_fix_lands_in_the_working_tree_and_nothing_is_committed(self) -> None:
        head = git(self.repo, "rev-parse", "HEAD")
        results = self._apply(EditingWorker({"calc.py": FIXED}))
        self.assertEqual([(r.verdict, r.applied) for r in results], [("applied", ["calc.py"])])
        self.assertEqual((self.repo / "calc.py").read_text(), FIXED)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual(git(self.repo, "diff", "--cached", "--name-only"), "")
        self.assertEqual(list(self.state.iterdir()), [], "scratch copies are removed")

    def test_ignored_files_and_virtualenvs_are_never_copied_to_the_worker(self) -> None:
        worker = EditingWorker({"calc.py": FIXED})
        self._apply(worker)
        self.assertNotIn(".env", worker.seen[0])
        self.assertFalse(any(p.startswith(".venv/") for p in worker.seen[0]))
        self.assertIn("calc.py", worker.seen[0])

    def test_without_a_scope_block_only_files_the_change_touches_may_be_edited(self) -> None:
        results = self._apply(EditingWorker({"calc.py": FIXED, "other.py": "X = 2\n"}))
        self.assertEqual(results[0].verdict, "out_of_scope")
        self.assertIn("out of scope: other.py", results[0].problems)
        self.assertEqual((self.repo / "other.py").read_text(), "X = 1\n")
        self.assertNotEqual((self.repo / "calc.py").read_text(), FIXED, "all-or-nothing")

    def test_protected_paths_are_refused_even_when_the_scope_allows_everything(self) -> None:
        results = self._apply(EditingWorker({".github/workflows/ci.yml": "on: push\n"}), scope={"allowed": ["**"]})
        self.assertEqual(results[0].verdict, "out_of_scope")
        self.assertFalse((self.repo / ".github").exists())

    def test_a_file_you_edit_while_the_fix_runs_is_not_overwritten(self) -> None:
        mine = "# my edit\n"
        results = self._apply(EditingWorker({"calc.py": FIXED},
                                            during=lambda: (self.repo / "calc.py").write_text(mine)))
        self.assertEqual(results[0].verdict, "refused")
        self.assertIn("changed in your checkout", results[0].problems[0])
        self.assertEqual((self.repo / "calc.py").read_text(), mine)

    def test_nits_are_not_fixed_and_at_most_three_fixes_run(self) -> None:
        worker = EditingWorker({"calc.py": FIXED})
        self.assertEqual(self._apply(worker, findings=[Finding(severity="nit")]), [])
        many = [Finding(title=f"f{i}", fingerprint=f"p{i}") for i in range(5)]
        self.assertEqual(len(self._apply(EditingWorker({"calc.py": FIXED}), findings=many)), 3)

    def test_local_scope_drops_protected_paths_from_the_default(self) -> None:
        policy = local_scope(None, ["calc.py", ".github/workflows/ci.yml", ".env"])
        self.assertEqual(policy.allowed, ("calc.py",))

    def test_export_skips_symlinks(self) -> None:
        (self.repo / "link.py").symlink_to("/etc/hostname")
        out = Path(tempfile.mkdtemp(dir=self.state))
        export_tree(self.repo, out)
        self.assertFalse((out / "link.py").exists())
        self.assertTrue((out / "calc.py").exists())


class TheFlagTests(unittest.TestCase):
    def test_apply_is_wired_after_a_local_review(self) -> None:
        from code_shipper import cli

        parser = cli.build_parser() if hasattr(cli, "build_parser") else None
        if parser is not None:
            args = parser.parse_args(["review", "--local", "--apply"])
            self.assertTrue(args.apply and args.local)
        self.assertTrue(callable(cli._apply_local_fixes))


if __name__ == "__main__":
    unittest.main()
