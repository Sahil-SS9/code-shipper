"""Framing for untrusted text.

Everything in a review prompt that came from outside — the diff, the PR description, CI logs,
finding text quoted back to the verifier — goes inside an ``<untrusted>`` block. This module builds
those blocks so there is one implementation and one set of tests.

**Framing is a mitigation, not a boundary.** It reduces the chance a model treats author-controlled
text as instructions. It does not stop it: a model can still be persuaded by prose. The boundaries
that actually hold are elsewhere — the broker posts with ``event=COMMENT`` only, and every harness
result is validated against a schema before anyone acts on it. Do not describe this module as a
security control.

The escape problem it solves is real, though. A diff is author-written code, so a comment containing
``</UNTRUSTED>`` or ``</ untrusted >`` would end the block early and put whatever follows outside the
framing. Every case and whitespace variant of the opening and closing tag is neutralised, with the
``<`` escaped so the text is still readable and still shows what the author wrote.
"""

from __future__ import annotations

import re

# Matches any opening or closing untrusted tag, in any case, with any internal whitespace:
#   </untrusted>   </UNTRUSTED>   </untrusted >   </ untrusted>   <untrusted source="x">
_TAG = re.compile(
    r"<\s*(/?)\s*untrusted\b[^>]*>",
    re.IGNORECASE,
)

DEFAULT_MAX_CHARS = 60_000


def ci_annotations(log_tail: str, *, limit: int = 40) -> list[str]:
    """Test and file locations the CI log points at, as untrusted hints (C10).

    A CI log is author-influenced text: it is output from code the PR author wrote. So an annotation
    is used only to *direct attention* — "these files are implicated" — never as evidence that the PR
    is wrong, and never as an instruction. The frame in tech spec §7 covers the log itself; this is
    the same rule one level up, so the hint is labelled as such in the prompt.
    """
    import re

    pattern = re.compile(
        r"^\s*([\w./-]+\.(?:py|pyi|js|jsx|ts|tsx|go|rs|java|rb|php|cs|c|cc|cpp|h|hpp))"
        r"(?::(\d+))?(?::\d+)?"
    )
    found: list[str] = []
    for raw in (log_tail or "").splitlines():
        match = pattern.match(raw)
        if match:
            path = match.group(1)
            if path not in found:
                found.append(path)
        if len(found) >= limit:
            break
    return found


def hint_block(annotations: list[str]) -> str:
    """The prompt preamble for CI hints, if there are any."""
    if not annotations:
        return ""
    listed = "\n".join(f"- {path}" for path in annotations)
    return (
        "The CI log mentions these files. This is a hint about where to look, not a conclusion: the "
        "log is produced by the change itself, so it can be wrong or misleading.\n\n"
        + untrusted_block("ci_hints", listed)
    )


def neutralise(text: str) -> str:
    """Escape every untrusted tag variant so the text cannot close or open a block.

    The ``<`` is replaced with ``&lt;`` rather than deleted: the reader should still see exactly what
    the author wrote, including the attempted escape, instead of a silently altered diff.
    """
    return _TAG.sub(lambda match: "&lt;" + match.group(0)[1:], text)


def untrusted_block(
    source: str, text: str, *, limit: int = DEFAULT_MAX_CHARS, truncated_note: bool = False
) -> str:
    """Wrap ``text`` in an untrusted block tagged with ``source``.

    ``source`` is a short label the model can reason about ("diff", "pr_description", "ci_log"). It
    is not escaped because it is ours, not the author's — but it is validated so a caller cannot
    inject a quote or a tag through it.
    """
    if not re.fullmatch(r"[a-z_]{1,32}", source):
        raise ValueError(f"source label must be lowercase identifier-ish: {source!r}")

    body = text or ""
    if len(body) > limit:
        body = body[:limit] + f"\n[truncated at {limit} characters]"
    safe = neutralise(body)
    # A note about truncation is only useful when the caller knows truncation is meaningful here.
    if truncated_note and len(text or "") > limit:
        safe += "\n[the excerpt above was shortened]"
    return "\n".join(
        [
            f'<untrusted source="{source}">',
            safe,
            "</untrusted>",
        ]
    )


def count_real_closing_tags(text: str) -> int:
    """How many genuinely closing tags the text contains.

    Used by tests: after framing, a block must contain exactly one, so any attempt to close it early
    is visible as a count above one.
    """
    return len(re.findall(r"</untrusted>", text))


def count_any_closing_tags(text: str) -> int:
    """Closing tags in any case or spacing, which is what an escaping test must count."""
    return len(re.findall(r"<\s*/\s*untrusted\s*>", text, re.IGNORECASE))
