"""Secret scanning and redaction (T-UNIT-SECRETS-01..10, Phase 5 gate).

`/ship` publishes code, so this is the last thing standing between a committed key and a public
repository. Two design decisions follow from that:

**A finding blocks; it is never downgraded to a warning.** A scan that prints "possible key found" and
continues is worse than no scan, because it moves the responsibility to someone reading output they were
never going to read.

**Redaction is applied to everything we emit**, not only to the scan report. A secret that appears in a
diff summary, a finding body, a log tail or a comment has been leaked just as surely as one printed in
the scan.

The patterns err toward matching: a false positive costs a second look, a false negative costs a key.
Each finding names what matched and where, and the matched text is replaced before it is ever shown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# The replacement text. Deliberately not the length of the original, so a redaction cannot be used to
# infer a key's length.
REDACTED = "[REDACTED]"

# (name, pattern, note). Ordered most specific first so the first match wins and reports the most
# precise reason.
PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("private-key",
     re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP |DSA )?PRIVATE KEY-----"),
     "a private key block"),
    ("github-token",
     re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})\b"),
     "a GitHub token"),
    ("aws-access-key",
     re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
     "an AWS access key id"),
    ("aws-secret",
     re.compile(r"(?i)\baws_secret_access_key\b\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}"),
     "an AWS secret access key"),
    ("slack-token",
     re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
     "a Slack token"),
    ("anthropic-key",
     re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
     "an Anthropic API key"),
    ("openai-key",
     re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
     "an OpenAI-style API key"),
    ("google-api-key",
     re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
     "a Google API key"),
    ("stripe-key",
     re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"),
     "a Stripe key"),
    ("jwt",
     re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
     "a JSON web token"),
    ("generic-assignment",
     re.compile(
         r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|client[_-]?secret)\b"
         r"\s*[:=]\s*['\"]([^'\"\s]{12,})['\"]"
     ),
     "a hard-coded credential assignment"),
    ("url-with-credentials",
     re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]{6,}@"),
     "a URL containing a password"),
)

# Values that look like secrets but are placeholders used throughout this repository's own tests. Kept
# in one place so the exemption is visible and reviewable rather than a regex tweak somewhere.
ALLOWED = (
    "placeholder-not-a-credential",
    "synthetic-not-a-real-key",
    "ghp_fake",
    "[REDACTED]",
)


class SecretFound(Exception):
    """A secret was found where publication was requested. Never caught to continue a publish."""

    def __init__(self, findings: list["Finding_"]) -> None:
        super().__init__(f"{len(findings)} secret(s) found; refusing to publish")
        self.findings = findings


@dataclass
class Finding_:
    pattern: str
    note: str
    location: str = ""
    line: int = 0
    masked: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "note": self.note,
            "location": self.location,
            "line": self.line,
            # Only ever the mask. The matched text is never included in output.
            "masked": self.masked,
        }


def _mask(text: str) -> str:
    """A short, non-reversible hint: first two characters only, and never enough to identify."""
    text = text.strip()
    if len(text) <= 4:
        return REDACTED
    return f"{text[:2]}{REDACTED}"


def _allowed(match: str) -> bool:
    return any(marker in match for marker in ALLOWED)


def scan_text(text: str, *, location: str = "") -> list[Finding_]:
    """Every secret-looking string in this text."""
    if not text:
        return []
    found: list[Finding_] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for name, pattern, note in PATTERNS:
            for match in pattern.finditer(line):
                raw = match.group(0)
                if _allowed(raw):
                    continue
                found.append(Finding_(
                    pattern=name, note=note, location=location, line=number, masked=_mask(raw)
                ))
    return found


def scan_files(files: dict[str, str]) -> list[Finding_]:
    """Every secret-looking string across a set of path -> content."""
    found: list[Finding_] = []
    for path, content in files.items():
        found += scan_text(content, location=path)
    return found


def redact(text: str) -> str:
    """Replace every secret-looking string with a marker.

    Applied to everything this tool emits, not just to scan reports: a key in a diff summary or a
    finding body is leaked just as surely as one in a scan report.
    """
    if not text:
        return text
    out = text
    for _name, pattern, _note in PATTERNS:
        def _replace(match: re.Match[str]) -> str:
            return match.group(0) if _allowed(match.group(0)) else REDACTED

        out = pattern.sub(_replace, out)
    return out


def assert_clean(files: dict[str, str]) -> None:
    """Refuse to continue if any file contains a secret. Raises ``SecretFound``."""
    findings = scan_files(files)
    if findings:
        raise SecretFound(findings)


def report(findings: list[Finding_]) -> str:
    """A human-readable scan report. Contains no matched text, by construction."""
    if not findings:
        return "Secret scan: no secrets found."
    lines = [f"Secret scan: {len(findings)} secret(s) found. Publishing must not continue.", ""]
    for finding in findings:
        where = f"{finding.location}:{finding.line}" if finding.location else f"line {finding.line}"
        lines.append(f"  {where}  {finding.note} ({finding.pattern})  {finding.masked}")
    lines += ["", "Remove the secret, rotate it, and re-run. A key that reached a commit is a "
                  "compromised key even if the commit was never pushed."]
    return "\n".join(lines)


def scan_diff(patch: str, *, path: str = "") -> list[Finding_]:
    """Scan added lines of a diff.

    Only additions are reported for a blocking purpose: removing a secret in a later commit still
    leaves it in history, but flagging every removal would make an already-rotated key block forever.
    Callers that need history checked should scan the files themselves.
    """
    added = []
    for line in patch.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    return scan_text("\n".join(added), location=path)
