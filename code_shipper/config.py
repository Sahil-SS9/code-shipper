"""Configuration: loading, validation, and the refusals that stop a bad setup before it runs.

Validation is not cosmetic. Several combinations are unsafe rather than merely wrong, and the rules
that refuse them are the same rules the security argument depends on:

* ``allow_agent_approval`` in ``single`` identity mode cannot work — GitHub does not let an author
  approve their own pull request — and pretending otherwise would produce a silent no-op.
* ``allow_agent_approval`` with an unverified container backend is the combination where a leaked
  user token plus an agent approval could produce an unwanted merge.
* A spend cap the adapter cannot enforce has to be refused, not treated as zero.

Everything unknown is an error. A typo in a config file that is silently ignored is how a safety
setting stops applying without anyone noticing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

try:  # Python 3.11+ has tomllib
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

LEVELS = frozenset({"quick", "standard", "deep"})
IDENTITY_MODES = frozenset({"single", "separated"})
HARNESSES = frozenset({"hermes", "command", "openai_compat"})
BACKEND_STATUS = frozenset({"VERIFIED", "UNVERIFIED", "UNSUPPORTED"})

TOP_LEVEL_KEYS = frozenset(
    {
        "version",
        "identity_mode",
        "allow_agent_approval",
        "harness",
        "repos",
        "budget",
        "watching",
        "review",
        "container",
        "state_dir",
    }
)


class ConfigError(Exception):
    """Invalid configuration. The message names the field and the reason."""


@dataclass
class Budget:
    daily_usd: float | None = None
    fix_rounds: int = 3
    pushes_per_day: int = 10
    wall_clock_minutes: int = 45
    # Whether the chosen adapter can enforce or reserve the cap. Unknown is not zero.
    enforceable: bool = True


@dataclass
class ContainerSettings:
    backend: str = "docker-rootless"
    status: Literal["VERIFIED", "UNVERIFIED", "UNSUPPORTED"] = "UNVERIFIED"
    image: str = ""


@dataclass
class Repo:
    slug: str
    watching: bool = False
    labels: list[str] = field(default_factory=list)


@dataclass
class Config:
    version: int = 1
    identity_mode: str = "single"
    allow_agent_approval: bool = False
    harness: str = "hermes"
    repos: list[Repo] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)
    watching: bool = False
    review_level: str = "standard"
    container: ContainerSettings = field(default_factory=ContainerSettings)
    state_dir: Path | None = None

    def validate(self) -> "Config":
        """Refuse an unsafe or incoherent configuration. Returns self for chaining."""
        if self.identity_mode not in IDENTITY_MODES:
            raise ConfigError(f"identity_mode must be one of {sorted(IDENTITY_MODES)}")

        if self.harness not in HARNESSES:
            raise ConfigError(f"harness must be one of {sorted(HARNESSES)}")

        if self.review_level not in LEVELS:
            raise ConfigError(f"review.level must be one of {sorted(LEVELS)}")

        if self.allow_agent_approval:
            # An author cannot approve their own PR, so single-identity agent approval is a no-op
            # that looks like it worked.
            if self.identity_mode == "single":
                raise ConfigError(
                    "allow_agent_approval needs identity_mode 'separated': GitHub does not let an "
                    "author approve their own pull request, so this would silently never approve"
                )
            # The dangerous combination: a leaked user token plus an agent approval.
            if self.container.status != "VERIFIED":
                raise ConfigError(
                    "allow_agent_approval needs a container backend that doctor reports VERIFIED "
                    f"for the credential-hiding controls; got {self.container.status!r}"
                )

        if self.budget.daily_usd is not None:
            if not isinstance(self.budget.daily_usd, (int, float)) or self.budget.daily_usd < 0:
                raise ConfigError("budget.daily_usd must be a number >= 0, or omitted")
            if not self.budget.enforceable:
                raise ConfigError(
                    "budget.daily_usd is set but the harness cannot enforce or reserve it; refuse "
                    "the run rather than pretend the cap applies"
                )

        for field_name, minimum in (
            ("fix_rounds", 1),
            ("pushes_per_day", 1),
            ("wall_clock_minutes", 1),
        ):
            value = getattr(self.budget, field_name)
            if not isinstance(value, int) or value < minimum:
                raise ConfigError(f"budget.{field_name} must be an integer >= {minimum}")

        for repo in self.repos:
            if repo.slug.count("/") != 1 or not all(repo.slug.split("/")):
                raise ConfigError(f"repos: {repo.slug!r} is not owner/name")
            if not isinstance(repo.watching, bool):
                raise ConfigError(f"repos: {repo.slug}: watching must be a boolean")

        return self


def _require_type(value: Any, kind: type, label: str) -> Any:
    if not isinstance(value, kind):
        raise ConfigError(f"{label} must be {kind.__name__}, got {type(value).__name__}")
    return value


def read_scope_block(text: str) -> dict[str, Any]:
    """Parse the `scope:` block from a repository's `code-shipper.yml`.

    Standard library only (K1 / ADR 0004), so this handles exactly the shape the policy needs: a
    top-level `scope:` key followed by indented `key: [a, b]` list entries. Anything else raises,
    because a config file that is half-understood is worse than one that is refused — a scope rule
    silently dropped off the end is a path the fixer would then be allowed to write.
    """
    if not text or not text.strip():
        return {}

    result: dict[str, Any] = {}
    in_scope = False
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        if not line.startswith((" ", "\t")):
            in_scope = line.strip().rstrip(":") == "scope" and line.strip().endswith(":")
            if not in_scope:
                continue
            continue

        if not in_scope:
            continue

        stripped = line.strip()
        if ":" not in stripped:
            raise ConfigError(f"line {number}: expected 'key: [values]' inside scope")
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()

        if key not in ("allowed", "forbidden", "tests"):
            raise ConfigError(
                f"line {number}: unknown scope key {key!r}; scope accepts allowed, forbidden, tests"
            )

        if not (value.startswith("[") and value.endswith("]")):
            raise ConfigError(f"line {number}: {key} must be a bracketed list")
        items = [part.strip().strip("'\"") for part in value[1:-1].split(",")]
        result[key] = [item for item in items if item]

    return result


def load(path: Path) -> Config:
    """Load and validate a config file (.toml or .json). Refuses it rather than filling gaps."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config not found: {path}")
    raw_text = path.read_text(encoding="utf-8")
    if path.suffix == ".toml":
        if tomllib is None:
            raise ConfigError("TOML config needs Python 3.11+")
        try:
            data = tomllib.loads(raw_text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML: {exc}") from None
    elif path.suffix == ".json":
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"invalid JSON: {exc}") from None
    else:
        raise ConfigError(f"unsupported config extension: {path.suffix}")

    if not isinstance(data, dict):
        raise ConfigError("config must be a table/object")
    return from_dict(data)


def from_dict(data: dict[str, Any]) -> Config:
    """Build a Config from a mapping, refusing unknown keys."""
    unknown = set(data) - TOP_LEVEL_KEYS
    if unknown:
        # Silently ignoring an unknown key is how a safety setting stops applying unnoticed.
        raise ConfigError(f"unknown config keys: {sorted(unknown)}")

    config = Config(
        version=_require_type(data.get("version", 1), int, "version"),
        identity_mode=data.get("identity_mode", "single"),
        allow_agent_approval=data.get("allow_agent_approval", False),
        harness=data.get("harness", "hermes"),
    )
    if config.version != 1:
        raise ConfigError(f"unsupported config version: {config.version}")

    budget_data = data.get("budget") or {}
    _require_type(budget_data, dict, "budget")
    unknown_budget = set(budget_data) - {
        "daily_usd", "fix_rounds", "pushes_per_day", "wall_clock_minutes", "enforceable"
    }
    if unknown_budget:
        raise ConfigError(f"unknown budget keys: {sorted(unknown_budget)}")
    config.budget = Budget(
        daily_usd=budget_data.get("daily_usd"),
        fix_rounds=budget_data.get("fix_rounds", 3),
        pushes_per_day=budget_data.get("pushes_per_day", 10),
        wall_clock_minutes=budget_data.get("wall_clock_minutes", 45),
        enforceable=budget_data.get("enforceable", True),
    )

    container_data = data.get("container") or {}
    _require_type(container_data, dict, "container")
    unknown_container = set(container_data) - {"backend", "status", "image"}
    if unknown_container:
        raise ConfigError(f"unknown container keys: {sorted(unknown_container)}")
    status = container_data.get("status", "UNVERIFIED")
    if status not in BACKEND_STATUS:
        raise ConfigError(f"container.status must be one of {sorted(BACKEND_STATUS)}")
    config.container = ContainerSettings(
        backend=container_data.get("backend", "docker-rootless"),
        status=status,
        image=container_data.get("image", ""),
    )

    for entry in data.get("repos") or []:
        _require_type(entry, dict, "repos entry")
        unknown_repo = set(entry) - {"slug", "watching", "labels"}
        if unknown_repo:
            raise ConfigError(f"unknown repos keys: {sorted(unknown_repo)}")
        slug = entry.get("slug")
        if not isinstance(slug, str):
            raise ConfigError("repos: each entry needs a 'slug' string")
        config.repos.append(
            Repo(
                slug=slug,
                watching=bool(entry.get("watching", False)),
                labels=list(entry.get("labels") or []),
            )
        )

    review_data = data.get("review") or {}
    _require_type(review_data, dict, "review")
    if set(review_data) - {"level"}:
        raise ConfigError(f"unknown review keys: {sorted(set(review_data) - {'level'})}")
    config.review_level = review_data.get("level", "standard")

    config.watching = bool(data.get("watching", False))
    if data.get("state_dir"):
        config.state_dir = Path(data["state_dir"])

    return config.validate()
