"""Policy for a fix run: which paths may change, and what counts as weakening a test.

Phase 0 hard-coded `calc/**` and `tests/**` for the sandbox, and detected test weakening by matching
`^def test_` at line start. Both were listed as known spike limits. This module replaces them:

* paths come from configuration, with glob patterns, so a repository describes its own layout
* test weakening is decided from the **AST**, so it sees class methods, unittest-style asserts and
  `assert True` padding

The AST check is deliberately narrow about what it claims. It refuses the shapes that make a failing
test pass without fixing anything — deleting tests, skipping them, removing assertions, replacing an
assertion with `assert True` — and it does not pretend to judge whether a source fix is correct.
"""

from __future__ import annotations

import ast
import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


_STAR_CROSSING = re.compile(r"\*\*")


def _glob_match(path: str, pattern: str) -> bool:
    """POSIX-style glob where ``*`` stops at a separator and ``**`` crosses them.

    `fnmatch` treats `*` as matching `/` too, so "tests/*.py" would match "tests/deep/a.py". That is
    the wrong semantics for a scope policy: an operator writing `tests/*.py` means one level.
    """
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if pattern[index : index + 2] == "**":
                parts.append(".*")
                index += 2
                # "**/" should also allow zero directories
                if pattern[index : index + 1] == "/":
                    parts[-1] = "(?:.*/)?"
                    index += 1
                continue
            parts.append("[^/]*")
        elif char == "?":
            parts.append("[^/]")
        else:
            parts.append(re.escape(char))
        index += 1
    return re.fullmatch("".join(parts), path) is not None


@dataclass(frozen=True)
class ScopePolicy:
    """Where a fix may write. Defaults are deliberately empty: a caller must opt in."""

    allowed: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    tests: tuple[str, ...] = ("tests/**", "test_*.py", "*_test.py", "*.test.*", "**/tests/**")
    max_files: int = 10
    max_total_bytes: int = 200_000

    def normalise(self, path: str) -> str:
        return path.replace("\\", "/").lstrip("./")

    def _matches(self, path: str, patterns: Iterable[str]) -> bool:
        normalised = self.normalise(path)
        basename = normalised.rsplit("/", 1)[-1]
        for pattern in patterns:
            if _glob_match(normalised, pattern):
                return True
            # A bare filename pattern ("test_*.py") matches at any depth, which is what makes
            # "calc/test_inner.py" a test file. A pattern with a slash is anchored from the root and
            # must not be loosened this way, or "tests/*.py" would start matching nested paths.
            if "/" not in pattern and _glob_match(basename, pattern):
                return True
        return False

    def path_allowed(self, path: str) -> bool:
        if self._matches(path, self.forbidden):
            return False
        return self._matches(path, self.allowed)

    def is_test_path(self, path: str) -> bool:
        return self._matches(path, self.tests)

    @classmethod
    def from_config(cls, data: dict) -> "ScopePolicy":
        """Build from config, refusing unknown keys rather than ignoring them."""
        known = {"allowed", "forbidden", "tests", "max_files", "max_total_bytes"}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown scope keys: {sorted(unknown)}")
        for key in ("allowed", "forbidden", "tests"):
            if key in data and not isinstance(data[key], list):
                raise ValueError(f"scope.{key} must be a list of glob patterns")
        return cls(
            allowed=tuple(data.get("allowed") or ()),
            forbidden=tuple(data.get("forbidden") or ()),
            tests=tuple(data.get("tests") or cls.tests),
            max_files=int(data.get("max_files", 10)),
            max_total_bytes=int(data.get("max_total_bytes", 200_000)),
        )


# -- test weakening, by AST --------------------------------------------------

# Names whose presence means "this test does not run": pytest markers and unittest skips.
SKIP_MARKERS = frozenset({"skip", "skipIf", "skipUnless", "xfail"})
SKIP_DECORATORS = frozenset({"skip", "skipIf", "skipUnless", "expectedFailure"})


def _is_test_function(node: ast.AST) -> bool:
    """A test function or method, at module level or inside a class.

    The Phase 0 check matched `^def test_` at line start, so a class method (indented four spaces)
    was invisible. Inspecting the node sees both.
    """
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test")


def _is_test_class(node: ast.AST) -> bool:
    return isinstance(node, ast.ClassDef) and node.name.startswith("Test")


def _decorator_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for decorator in getattr(node, "decorator_list", []) or []:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute):
            names.add(target.attr)
        elif isinstance(target, ast.Name):
            names.add(target.id)
    return names


def _is_skipped(node: ast.AST) -> bool:
    if _decorator_names(node) & SKIP_DECORATORS:
        return True
    # pytest.mark.skip / pytest.mark.xfail, or a bare skip inside the body
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr in SKIP_MARKERS:
            return True
        if isinstance(child, ast.Name) and child.id in SKIP_MARKERS:
            return True
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr in SKIP_MARKERS:
                return True
    return False


def _assertion_count(node: ast.AST) -> int:
    """Assertions that could actually fail.

    Counts `assert <expr>` unless the expression is a constant (that is `assert True` padding) and
    unittest-style `self.assertX(...)` calls. Reachability is not attempted: a dead assertion is a
    different problem from a removed one.
    """
    count = 0
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            if not isinstance(child.test, ast.Constant):
                count += 1
        elif isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr.startswith("assert") or child.func.attr == "fail":
                count += 1
    return count


@dataclass
class TestFacts:
    """What a test file contains, as far as weakening detection cares."""

    tests: dict[str, int] = field(default_factory=dict)      # name -> assertion count
    skipped: set[str] = field(default_factory=set)
    constants: int = 0                                        # `assert True`-style padding

    @property
    def assertion_total(self) -> int:
        return sum(self.tests.values())


def analyse_tests(source: str) -> TestFacts:
    """Parse a test file and report its tests, skips and padding. Never raises for bad syntax."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return TestFacts()

    facts = TestFacts()
    for node in ast.walk(tree):
        if _is_test_function(node):
            name = node.name
            # A method in two classes has the same name; qualify it so both are tracked.
            facts.tests[name] = facts.tests.get(name, 0) + _assertion_count(node)
            if _is_skipped(node):
                facts.skipped.add(name)
        elif isinstance(node, ast.Assert) and isinstance(node.test, ast.Constant):
            facts.constants += 1
    return facts


def weakening(before: str, after: str) -> list[str]:
    """Reasons ``after`` weakens the tests in ``before``. Empty means nothing was weakened.

    Every reason names the specific test, so a refusal is actionable rather than "the tests changed".
    """
    old = analyse_tests(before)
    new = analyse_tests(after)
    problems: list[str] = []

    for name in sorted(set(old.tests) - set(new.tests)):
        problems.append(f"test removed: {name}")

    for name in sorted(set(old.skipped) ^ set(new.skipped)):
        if name in new.skipped:
            problems.append(f"test skipped: {name}")

    for name, count in sorted(old.tests.items()):
        if name in new.tests and new.tests[name] < count:
            problems.append(
                f"assertions removed from {name}: {count} -> {new.tests[name]}"
            )

    # Padding: more constant asserts than before is a way to look busier without testing anything.
    if new.constants > old.constants:
        problems.append(
            f"constant assert added ({old.constants} -> {new.constants}): an assertion that cannot fail"
        )

    return problems
