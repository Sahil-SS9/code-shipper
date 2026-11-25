"""Path filters with default ignores (C1) and per-finding rationale (C6).

**C1.** A review that comments on a lockfile diff, a minified bundle or a vendored directory wastes the
reviewer's attention and the model's budget. Default ignores are the things nobody wants reviewed;
a repository may add to them but the defaults stay unless explicitly cleared, because "I turned off
the default ignores" should be a deliberate act.

Ignored paths are *reported*, not silently dropped: the review says how many files were skipped, so
"no findings" cannot quietly mean "nothing was looked at".

**C6.** Each posted finding carries a short rationale — why this lens raised it and what made
verification keep it — collapsed inside a `<details>` block so it does not push the finding itself out
of view. The rationale is model text, so it is framed as untrusted like everything else.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Paths nobody wants reviewed. Ordered roughly by how much noise each removes in practice.
DEFAULT_IGNORES: tuple[str, ...] = (
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Cargo.lock",
    "go.sum",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.snap",
    "dist/**",
    "build/**",
    "vendor/**",
    "node_modules/**",
    "**/__pycache__/**",
    "*.pyc",
    "*.generated.*",
    "**/*.svg",
    "**/*.png",
    "**/*.jpg",
    "**/*.ico",
    "**/*.woff",
    "**/*.woff2",
)


@dataclass(frozen=True)
class PathFilter:
    """Which changed files a review looks at."""

    ignores: tuple[str, ...] = DEFAULT_IGNORES
    extra_ignores: tuple[str, ...] = ()
    include_ignored: bool = False

    def ignored_patterns(self) -> tuple[str, ...]:
        return self.ignores + self.extra_ignores

    def is_ignored(self, path: str) -> bool:
        if self.include_ignored:
            return False
        from ..steward.policy import _glob_match

        normalised = path.replace("\\", "/").lstrip("./")
        basename = normalised.rsplit("/", 1)[-1]
        for pattern in self.ignored_patterns():
            if _glob_match(normalised, pattern):
                return True
            if "/" not in pattern and _glob_match(basename, pattern):
                return True
        return False

    def partition(self, paths: list[str]) -> tuple[list[str], list[str]]:
        """(reviewed, ignored). Order is preserved within each list."""
        reviewed: list[str] = []
        ignored: list[str] = []
        for path in paths:
            (ignored if self.is_ignored(path) else reviewed).append(path)
        return reviewed, ignored

    @classmethod
    def from_config(cls, data: dict) -> "PathFilter":
        known = {"ignores", "extra_ignores", "include_ignored"}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown filter keys: {sorted(unknown)}")
        for key in ("ignores", "extra_ignores"):
            if key in data and not isinstance(data[key], list):
                raise ValueError(f"filter.{key} must be a list of glob patterns")
        return cls(
            ignores=tuple(data.get("ignores") or DEFAULT_IGNORES),
            extra_ignores=tuple(data.get("extra_ignores") or ()),
            include_ignored=bool(data.get("include_ignored", False)),
        )


@dataclass
class Rationale:
    """Why a finding exists and why verification kept it (C6)."""

    lens: str = ""
    raised_by: str = ""
    kept_because: str = ""
    confidence: str = ""

    def render(self) -> str:
        """A collapsed block: present for the curious, out of the way for everyone else."""
        rows: list[str] = []
        if self.lens:
            rows.append(f"- Lens: {self.lens}")
        if self.raised_by:
            rows.append(f"- Raised because: {self.raised_by}")
        if self.kept_because:
            rows.append(f"- Kept because: {self.kept_because}")
        if self.confidence:
            rows.append(f"- Confidence: {self.confidence}")
        if not rows:
            return ""
        return "\n".join(["<details><summary>Why this finding</summary>", "", *rows, "", "</details>"])


def comment_body(*, title: str, body: str, rationale: Rationale | None = None) -> str:
    """The inline comment for one finding: claim, detail, and optionally the rationale."""
    parts = [f"**{title}**", "", body]
    if rationale is not None:
        rendered = rationale.render()
        if rendered:
            parts += ["", rendered]
    return "\n".join(parts)


def summarise_skipped(ignored: list[str], *, limit: int = 5) -> str:
    """One line for the review body saying what was not looked at."""
    if not ignored:
        return ""
    shown = ", ".join(ignored[:limit])
    more = f" and {len(ignored) - limit} more" if len(ignored) > limit else ""
    return (
        f"{len(ignored)} file(s) not reviewed (lockfiles, generated output and vendored code): "
        f"{shown}{more}."
    )
