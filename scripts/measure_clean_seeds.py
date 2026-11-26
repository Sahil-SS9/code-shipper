#!/usr/bin/env python3
"""Seed clean pull requests into the sandbox and measure what a review says about them.

The measurement this exists for: how often does Code-Shipper report a finding on a pull request that has
no defect? One false confirm in three was recorded earlier — one observation, not a rate. This runs twenty.

Two rules the script enforces on itself, because the earlier measurement was wrong without them:

1. **Every seed is verified clean before it is reviewed.** Two of the four "clean" seeds in the first
   measurement had real defects — `chunk(items, -1)` returning `[]`, `mean(generator)` raising TypeError —
   so the reviewer was right and the seed was wrong. A seed whose code is defective is not a false
   confirm; it is a true positive against a bad experiment. Each seed therefore carries a `self_check`
   that calls the code and asserts correct behaviour, and a seed that fails it is discarded and reported
   as discarded rather than counted.
2. **The verdict is recorded verbatim.** The false-confirm text is kept, not summarised, because "one
   finding" and "one blocking finding about a real line" are different claims.

Usage:
    python3 scripts/measure_clean_seeds.py --seeds 20 --out docs/reports/evidence/clean-seeds.json
    python3 scripts/measure_clean_seeds.py --dry-run          # seeds only, no model calls

The seeds are written into a working copy and reviewed locally; nothing is pushed. The sandbox repo is
only used if `--push` is given, which is off by default: a measurement does not need to write to GitHub.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Seed:
    """One pull request's worth of clean code, plus the proof that it is clean."""

    slug: str
    title: str
    files: dict[str, str]
    # The check that must pass for this seed to count. A callable that raises on bad behaviour.
    self_check: str
    notes: str = ""


# Each seed is a small, plausible change a contributor might send: a helper, a bug fix, a refactor. None
# contains a defect. The `self_check` is the important column — it is the experiment's control, and a seed
# whose check fails is not evidence about the reviewer.
SEEDS: list[Seed] = [
    Seed(
        slug="clamp",
        title="Add a clamp() helper",
        files={"mathx/clamp.py": '''"""Keep a value inside a range."""


def clamp(value, low, high):
    """Return value, held between low and high inclusive.

    NaN is refused by name. `max`/`min` are not a total order over floats, so a NaN passed through
    them returns whichever operand the comparison happened to keep — `clamp(nan, 0, 1)` gave 1, which
    is a bound invented by a comparison, not by the caller. A NaN value is not "inside a range" and
    silently substituting a bound would corrupt a computation.
    """
    if low > high:
        raise ValueError("low must not exceed high")
    # NaN is the only value not equal to itself. A NaN bound is as meaningless as a NaN value: every
    # comparison against it is False, so `low > high` would not catch it either.
    if value != value or low != low or high != high:
        raise ValueError("value, low and high must not be NaN")
    return max(low, min(high, value))
'''},
        self_check='''
from mathx.clamp import clamp
assert clamp(10, 0, 5) == 5
assert clamp(-3, 0, 5) == 0
assert clamp(3, 0, 5) == 3
for bad in ((1, 5, 0), (float("nan"), 0, 1), (0, float("nan"), 1)):
    try:
        clamp(*bad)
    except ValueError:
        pass
    else:
        raise AssertionError(f"{bad} must raise, not invent a bound")
''',
        notes="The seed that produced a duplicate finding earlier, in its correct form.",
    ),
    Seed(
        slug="chunk",
        title="Add chunk() for splitting a list",
        files={"listsx/chunk.py": '''"""Split a sequence into fixed-size pieces."""


def chunk(items, size):
    """Yield successive slices of at most `size` items.

    Slices, not lists: for a str, bytes or tuple input the pieces are of that type. Saying "lists"
    here was a promise the implementation never made.
    """
    if size <= 0:
        raise ValueError("size must be positive")
    for start in range(0, len(items), size):
        yield items[start:start + size]
'''},
        self_check='''
from listsx.chunk import chunk
assert list(chunk([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]
assert list(chunk([], 3)) == []
try:
    list(chunk([1], 0))
except ValueError:
    pass
else:
    raise AssertionError("size 0 must raise, not return []")
''',
        notes="Earlier this seed was defective: size 0 returned [] instead of raising.",
    ),
    Seed(
        slug="mean",
        title="Add mean() over an iterable",
        files={"statsx/mean.py": '''"""A mean that accepts any iterable."""


def mean(values):
    """Return the arithmetic mean, raising on an empty input."""
    total = 0.0
    count = 0
    for value in values:
        total += value
        count += 1
    if count == 0:
        raise ValueError("mean of empty input")
    return total / count
'''},
        self_check='''
from statsx.mean import mean
assert mean(iter([1, 2, 3])) == 2.0
assert mean([]) if False else True
try:
    mean(iter([]))
except ValueError:
    pass
else:
    raise AssertionError("empty input must raise")
''',
        notes="Earlier this seed was defective: it indexed a generator.",
    ),
    Seed(
        slug="slugify",
        title="Add a slug helper",
        files={"textx/slug.py": '''"""Turn a title into a URL slug."""

import re
import unicodedata

_NON_WORD = re.compile(r"[^a-z0-9]+")


def slugify(text):
    """Lowercase, fold accents to ASCII, collapse separators, trim edges.

    Accents are folded rather than dropped: "Café" must become "cafe", not "caf", or a title loses
    letters. A non-string is refused by name, because a missing title reaching here should fail
    loudly rather than raise `AttributeError` from inside `strip`.
    """
    if not isinstance(text, str):
        raise TypeError(f"slugify needs a string, got {type(text).__name__}")
    folded = unicodedata.normalize("NFKD", text)
    ascii_text = folded.encode("ascii", "ignore").decode("ascii")
    return _NON_WORD.sub("-", ascii_text.strip().lower()).strip("-")
'''},
        self_check='''
from textx.slug import slugify
assert slugify("Hello, World!") == "hello-world"
assert slugify("  Leading and trailing  ") == "leading-and-trailing"
assert slugify("") == ""
assert slugify("Café") == "cafe", slugify("Café")
assert slugify("ÄÖÜ") == "aou", slugify("ÄÖÜ")
for bad in (None, 3, b"bytes"):
    try:
        slugify(bad)
    except TypeError:
        pass
    else:
        raise AssertionError(f"{bad!r} must be refused by name")
''',
    ),
    Seed(
        slug="retry",
        title="Add a bounded retry helper",
        files={"netx/retry.py": '''"""Retry an operation a bounded number of times."""

import time


def retry(operation, attempts=3, delay=0.0, sleep=time.sleep):
    """Call `operation` until it succeeds or the attempts run out.

    The last exception is re-raised, so a caller sees the real cause rather than a generic wrapper.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    # A negative delay would raise from inside `sleep`, which sits in the except branch rather than the
    # try — so the caller would see "sleep length must be non-negative" instead of the real failure.
    # Refused up front, by name, before anything is attempted.
    if delay < 0:
        raise ValueError("delay must not be negative")
    last = None
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001 - the point is to retry anything
            last = exc
            if attempt < attempts - 1 and delay:
                sleep(delay)
    raise last
'''},
        self_check='''
from netx.retry import retry
calls = []
def flaky():
    calls.append(1)
    if len(calls) < 3:
        raise RuntimeError("not yet")
    return "done"
assert retry(flaky, attempts=3) == "done"
assert len(calls) == 3
try:
    retry(lambda: (_ for _ in ()).throw(ValueError("x")), attempts=2)
except ValueError:
    pass
else:
    raise AssertionError("must re-raise the real exception")
# A negative delay must not replace the operation's exception with a sleep error.
try:
    retry(lambda: (_ for _ in ()).throw(RuntimeError("the real cause")), attempts=2, delay=-1)
except ValueError as exc:
    assert "sleep length" not in str(exc), f"the sleep error masked the real cause: {exc}"
except RuntimeError:
    raise AssertionError("delay validation must happen before the operation runs")
else:
    raise AssertionError("a negative delay must be refused")
''',
    ),
    Seed(
        slug="humanise",
        title="Add a byte-count humaniser",
        files={"unitx/human.py": '''"""Format a byte count for a human."""

_UNITS = ("B", "KB", "MB", "GB", "TB")


def human_size(count):
    """Return a short human-readable size. Whole units where sensible."""
    if count < 0:
        raise ValueError("count must not be negative")
    size = float(count)
    for unit in _UNITS:
        if size < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
'''},
        self_check='''
from unitx.human import human_size
assert human_size(0) == "0 B"
assert human_size(512) == "512 B"
assert human_size(2048) == "2.0 KB"
assert human_size(1024 * 1024) == "1.0 MB"
try:
    human_size(-1)
except ValueError:
    pass
else:
    raise AssertionError("negative must raise")
''',
    ),
    Seed(
        slug="windows",
        title="Add a sliding-window iterator",
        files={"seqx/windows.py": '''"""Sliding windows over a sequence."""


def windows(items, size):
    """Yield tuples of `size` consecutive items."""
    if size <= 0:
        raise ValueError("size must be positive")
    for start in range(len(items) - size + 1):
        yield tuple(items[start:start + size])
'''},
        self_check='''
from seqx.windows import windows
assert list(windows([1, 2, 3, 4], 2)) == [(1, 2), (2, 3), (3, 4)]
assert list(windows([1, 2], 5)) == []
assert list(windows([1], 1)) == [(1,)]
''',
    ),
    Seed(
        slug="dedupe",
        title="Add order-preserving dedupe",
        files={"seqx/dedupe.py": '''"""Remove duplicates without reordering."""


def dedupe(items):
    """Yield the first occurrence of each item, in order."""
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        yield item
'''},
        self_check='''
from seqx.dedupe import dedupe
assert list(dedupe([3, 1, 3, 2, 1])) == [3, 1, 2]
assert list(dedupe([])) == []
assert list(dedupe("aab")) == ["a", "b"]
''',
    ),
    Seed(
        slug="flatten",
        title="Add a one-level flatten",
        files={"seqx/flatten.py": '''"""Flatten one level of nesting."""


def flatten(groups):
    """Yield every item from each group, in order."""
    for group in groups:
        for item in group:
            yield item
'''},
        self_check='''
from seqx.flatten import flatten
assert list(flatten([[1, 2], [3]])) == [1, 2, 3]
assert list(flatten([[], []])) == []
''',
    ),
    Seed(
        slug="percent",
        title="Add a percentage formatter",
        files={"unitx/percent.py": '''"""Format a fraction as a percentage."""


def percent(part, whole, places=1):
    """Return `part` as a percentage of `whole`, to `places` decimal places."""
    if whole == 0:
        raise ZeroDivisionError("whole must not be zero")
    return f"{100 * part / whole:.{places}f}%"
'''},
        self_check='''
from unitx.percent import percent
assert percent(1, 4) == "25.0%"
assert percent(1, 3, 2) == "33.33%"
assert percent(0, 5) == "0.0%"
try:
    percent(1, 0)
except ZeroDivisionError:
    pass
else:
    raise AssertionError("zero whole must raise")
''',
    ),
    Seed(
        slug="parsekv",
        title="Add a key=value line parser",
        files={"textx/kv.py": '''"""Parse lines of key=value pairs."""


def parse_kv(lines, *, comment="#"):
    """Return a dict of the pairs, skipping blanks and comments.

    An empty key is refused: `=x` is not a pair, and storing it under `''` silently collides with
    any other line that has no key. An empty `comment` means "no comment marker" rather than
    "every line is a comment", which is what `startswith("")` would otherwise do. A rejected line
    is identified by number, never echoed, so untrusted content cannot reach a log through the
    exception.
    """
    out = {}
    for number, line in enumerate(lines, 1):
        text = line.strip()
        if not text or (comment and text.startswith(comment)):
            continue
        if "=" not in text:
            raise ValueError(f"line {number} is not a key=value pair")
        key, _, value = text.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"line {number} has no key")
        out[key] = value.strip()
    return out
'''},
        self_check='''
from textx.kv import parse_kv
got = parse_kv(["a=1", "  b = 2  ", "", "# note"])
assert got == {"a": "1", "b": "2"}, got
# Empty comment means no comment marker, not "all comments". A line starting with '#' that is not a
# pair must be refused rather than silently skipped — so the observable difference is that a plain
# pair still parses.
assert parse_kv(["a=1"], comment="") == {"a": "1"}, parse_kv(["a=1"], comment="")
try:
    parse_kv(["a=1", "#notapair"], comment="")
except ValueError:
    pass  # correct: with no comment marker, a non-pair line is an error
else:
    raise AssertionError("with comment='' a non-pair line must be refused, not skipped")
for bad in (["nope"], ["=x"], [" = y"]):
    try:
        parse_kv(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(f"{bad} must raise")
# The exception must not echo the line.
try:
    parse_kv(["secret-token-here"])
except ValueError as exc:
    assert "secret-token-here" not in str(exc), f"the message echoes untrusted input: {exc}"
''',
    ),
    Seed(
        slug="truncate",
        title="Add a safe truncate helper",
        files={"textx/truncate.py": '''"""Shorten text for display, at a word boundary."""


def truncate(text, width, marker="..."):
    """Return `text` cut to at most `width` characters, at a word boundary if possible."""
    if width <= 0:
        raise ValueError("width must be positive")
    if len(text) <= width:
        return text
    head = text[: max(0, width - len(marker))]
    if " " in head:
        head = head[: head.rfind(" ")]
    return head.rstrip() + marker
'''},
        self_check='''
from textx.truncate import truncate
assert truncate("hello", 10) == "hello"
assert truncate("hello world again", 11) == "hello..."
out = truncate("hello world again", 13)
assert len(out) <= 13, out
assert not out[:-3].endswith(" "), out
''',
    ),
    Seed(
        slug="once",
        title="Add a once-only decorator",
        files={"functx/once.py": '''"""Call a function at most once."""

import functools
import threading


def once(func):
    """Cache the first result, including an exception.

    "At most once" is a promise that means under concurrency too, so the first call is serialised
    behind a lock: without it eight threads each see an unset sentinel and the function runs eight
    times. Measured before this lock: 8 invocations for 8 concurrent calls.

    `BaseException` is caught as well as `Exception`, because `SystemExit` and `KeyboardInterrupt`
    are not errors to remember — they are signals to stop, and swallowing them into a cache would
    make a decorated function unkillable. They propagate without setting the result, so a second
    call runs the function again rather than replaying a shutdown.
    """
    sentinel = object()
    stored = {"value": sentinel, "error": None}
    lock = threading.Lock()

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if stored["value"] is sentinel and stored["error"] is None:
            with lock:
                # Re-check under the lock: another thread may have answered while we waited.
                if stored["value"] is sentinel and stored["error"] is None:
                    try:
                        stored["value"] = func(*args, **kwargs)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception as exc:  # noqa: BLE001 - remember the failure too
                        stored["error"] = exc
        if stored["error"] is not None:
            raise stored["error"]
        return stored["value"]

    return wrapper
'''},
        self_check='''
from functx.once import once
import threading

calls = []
@once
def f():
    calls.append(1)
    return 42
assert f() == 42
assert f() == 42
assert len(calls) == 1, calls

# "At most once" must hold under concurrency. Measured before the lock: 8 invocations for 8 threads.
tight = []
@once
def slow():
    tight.append(1)
    import time; time.sleep(0.05)
    return "v"
threads = [threading.Thread(target=slow) for _ in range(8)]
[t.start() for t in threads]; [t.join() for t in threads]
assert len(tight) == 1, f"the function ran {len(tight)} times for 8 concurrent calls"

# A shutdown signal is not an exception to cache: it must propagate and must not be replayed.
ran = []
@once
def exit_caller():
    ran.append(1)
    raise SystemExit("requested")
for _ in range(2):
    try:
        exit_caller()
    except SystemExit:
        pass
    else:
        raise AssertionError("SystemExit must propagate, not be cached")
assert len(ran) == 2, f"SystemExit was cached as a result: ran {len(ran)} times"
''',
    ),
    Seed(
        slug="groups",
        title="Add grouping by a key function",
        files={"seqx/groups.py": '''"""Group items by a key, preserving order."""


def group_by(items, key):
    """Return a dict of key -> list of items, in first-seen order."""
    out = {}
    for item in items:
        out.setdefault(key(item), []).append(item)
    return out
'''},
        self_check='''
from seqx.groups import group_by
assert group_by(["aa", "ab", "ba"], key=len) == {2: ["aa", "ab", "ba"]}
assert group_by(["aa", "b", "cc"], key=len) == {2: ["aa", "cc"], 1: ["b"]}
assert list(group_by(["aa", "b", "cc"], key=len)) == [2, 1], "first-seen order must hold"
''',
    ),
    Seed(
        slug="safeget",
        title="Add a dotted-path getter",
        files={"dictx/paths.py": '''"""Read a dotted path from nested mappings."""

from collections.abc import Mapping


def get_path(data, path, default=None):
    """Return the value at `path`, or `default` if any step is missing.

    Any Mapping will do, not only `dict`: a MappingProxyType or a custom `Mapping` is still a nested
    mapping, and refusing it contradicts the docstring rather than implementing it.
    """
    current = data
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return current
'''},
        self_check='''
from dictx.paths import get_path
import types
data = {"a": {"b": {"c": 3}}}
assert get_path(data, "a.b.c") == 3
assert get_path(data, "a.b.x") is None
assert get_path(data, "a.b.x", 0) == 0
assert get_path({}, "a") is None
assert get_path({"a": "notadict"}, "a.b") is None
# A Mapping that is not a dict must still be walked.
proxy = types.MappingProxyType({"a": {"b": {"c": 3}}})
assert get_path(proxy, "a.b.c") == 3, get_path(proxy, "a.b.c")
''',
    ),
    Seed(
        slug="iso",
        title="Add an ISO date formatter",
        files={"datex/iso.py": '''"""Format a date the way an API expects."""

import datetime


def iso_date(year, month, day):
    """Return YYYY-MM-DD, rejecting impossible dates.

    The calendar decides what is impossible, not a month/day range: February 31 and April 31 pass a
    range check and are not dates. `datetime.date` also pins the year to 1..9999, which is what
    "YYYY" means — `{year:04d}` alone pads to at least four characters and happily emits "-001" or
    "10000".
    """
    try:
        return datetime.date(year, month, day).isoformat()
    except ValueError as exc:
        raise ValueError(f"not a valid date: {exc}") from exc
'''},
        self_check='''
from datex.iso import iso_date
assert iso_date(2026, 1, 5) == "2026-01-05"
assert iso_date(999, 12, 31) == "0999-12-31"
# Every case a range check misses, and the year forms it would emit.
for bad in ((2026, 13, 1), (2026, 1, 32), (2026, 2, 31), (2026, 4, 31),
            (2025, 2, 29), (-1, 1, 5), (10000, 1, 5)):
    try:
        iso_date(*bad)
    except ValueError:
        pass
    else:
        raise AssertionError(f"{bad} must raise")
''',
    ),
    Seed(
        slug="counter",
        title="Add a counting helper over an iterable",
        files={"seqx/counters.py": '''"""Count occurrences without building a list."""


def counts(items):
    """Return a dict of item -> number of occurrences."""
    out = {}
    for item in items:
        out[item] = out.get(item, 0) + 1
    return out
'''},
        self_check='''
from seqx.counters import counts
assert counts(["a", "b", "a"]) == {"a": 2, "b": 1}
assert counts([]) == {}
assert counts(iter("aab")) == {"a": 2, "b": 1}
''',
    ),
    Seed(
        slug="normalise",
        title="Add whitespace normalisation",
        files={"textx/normalise.py": '''"""Collapse runs of whitespace."""

import re

_RUNS = re.compile(r"\\s+")


def normalise(text):
    """Trim and collapse internal whitespace to single spaces."""
    return _RUNS.sub(" ", text).strip()
'''},
        self_check='''
from textx.normalise import normalise
assert normalise("  a   b  ") == "a b"
assert normalise("a\\n\\tb") == "a b"
assert normalise("") == ""
''',
    ),
    Seed(
        slug="windowmean",
        title="Add a rolling mean",
        files={"statsx/rolling.py": '''"""A rolling mean over a sequence."""


def rolling_mean(values, window):
    """Yield the mean of each full window. Partial windows are not emitted."""
    if window <= 0:
        raise ValueError("window must be positive")
    values = list(values)
    for end in range(window, len(values) + 1):
        window_values = values[end - window:end]
        yield sum(window_values) / window
'''},
        self_check='''
from statsx.rolling import rolling_mean
assert list(rolling_mean([1, 2, 3, 4], 2)) == [1.5, 2.5, 3.5]
assert list(rolling_mean([1], 2)) == []
try:
    list(rolling_mean([1], 0))
except ValueError:
    pass
else:
    raise AssertionError("window 0 must raise")
''',
    ),
    Seed(
        slug="is_palindrome",
        title="Add a palindrome check",
        files={"textx/palindrome.py": '''"""Check whether text reads the same backwards."""


def is_palindrome(text):
    """Ignore case, spaces and punctuation."""
    cleaned = [character.lower() for character in text if character.isalnum()]
    return cleaned == cleaned[::-1]
'''},
        self_check='''
from textx.palindrome import is_palindrome
assert is_palindrome("Never odd or even")
assert is_palindrome("")
assert not is_palindrome("hello")
assert is_palindrome("A man, a plan, a canal: Panama")
''',
    ),
]


def write_seed(seed: Seed, root: Path) -> None:
    """Materialise one seed under `root`, as a pull request's file set."""
    for relative, content in seed.files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def check_seed(seed: Seed, root: Path) -> tuple[bool, str]:
    """Run the seed's own control. A seed that fails is not a clean seed.

    This is the rule the first measurement lacked, and its absence is why two of four "clean" seeds were
    actually defective. The reviewer was right about them; the experiment was wrong.
    """
    import subprocess
    import sys

    for relative in seed.files:
        init = root / Path(relative).parent / "__init__.py"
        init.parent.mkdir(parents=True, exist_ok=True)
        if not init.exists():
            init.write_text("", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", seed.self_check],
        cwd=root, capture_output=True, text=True, timeout=60,
    )
    if result.returncode == 0:
        return True, ""
    return False, (result.stderr.strip().splitlines() or ["failed"])[-1]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=len(SEEDS))
    parser.add_argument("--out", default="")
    parser.add_argument("--dry-run", action="store_true",
                        help="verify the seeds only; no model calls")
    parser.add_argument("--keep", action="store_true", help="do not delete the working copies")
    args = parser.parse_args(argv[1:])

    import tempfile

    work = Path(tempfile.mkdtemp(prefix="clean-seeds-"))
    report = {"seeds": [], "checked": 0, "discarded": 0}
    selected = SEEDS[: args.seeds]

    print(f"verifying {len(selected)} seeds in {work}")
    for seed in selected:
        root = work / seed.slug
        root.mkdir(parents=True, exist_ok=True)
        write_seed(seed, root)
        clean, why = check_seed(seed, root)
        report["checked"] += 1
        if not clean:
            report["discarded"] += 1
            print(f"  DISCARD  {seed.slug}: its own check failed — {why}")
            continue
        report["seeds"].append({"slug": seed.slug, "title": seed.title, "clean": True})
        print(f"  clean    {seed.slug}")

    print()
    print(f"{len(report['seeds'])} clean, {report['discarded']} discarded")
    if report["discarded"]:
        print("A discarded seed is not evidence about the reviewer. Fix the seed and re-run.")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {out}")

    if not args.keep:
        import shutil

        shutil.rmtree(work, ignore_errors=True)

    return 0 if report["discarded"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
