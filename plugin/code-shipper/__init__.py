"""/ship: review, fix and prepare pull requests from inside Hermes.

    /ship <owner/repo> <pr> [review|fix|watch|stop|status|publish]

This module is a wrapper, not a second implementation. Every rule lives in the CLI, which holds the
broker, the secret scan and the publish gate; duplicating any of them here would be a second copy that
silently stops agreeing with the first.

`register()` is deliberately cheap: the loader mounts this file as a package and the host's capability
probe calls `register()` in a recording context, so nothing is imported or run at import time.
"""

from __future__ import annotations

__version__ = "0.1.0"

DESCRIPTION = (
    "Review a pull request, fix it within a budget, and get it ready to merge. "
    "Comment-only: never approves and never merges."
)

ARGS_HINT = "review|fix|watch|stop|status|publish"


def register(ctx) -> None:
    def _get(key, default):
        try:
            value = ctx.get_config(key, default)
        except Exception:  # noqa: BLE001 - a recording context may not implement config
            return default
        return default if value is None else value

    def handler(raw_args: str = "", **_kwargs) -> str:
        from .commands import ship as ship_command

        config = {
            "binary": _get("binary", "code-shipper"),
            "default_profile": _get("default_profile", "standard"),
            "allow_ship": _get("allow_ship", False),
        }
        return ship_command.handle(raw_args, config)

    ctx.register_command("ship", handler=handler, description=DESCRIPTION, args_hint=ARGS_HINT)
