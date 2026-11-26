"""The contract under review: the docstrings that say what the changed code promises.

The verifier's job is to refute a finding. As first built it saw only the diff, the findings and the PR
description — no docstrings — so "would a reviewer act on this?" was a guess, and both false confirms in
the 20-seed measurement survived it. `clamp` was reported for not handling NaN when its contract never
promised NaN handling; `is_palindrome` for not stripping punctuation when its docstring offered only case.
With the contract in the prompt, the verifier can read the promise instead of imagining one.

What is collected: for each changed file, the module docstring, and every function or class docstring
whose span overlaps a changed line. Nothing else — no bodies, no comments, no type hints. The point is to
show what the code *says* it does, not what it does.

Trust: a docstring is text the PR author wrote, so it is author-controlled and goes inside the same
untrusted framing as the diff. It is evidence of a claim, never instruction to the model.

Cap: 2,000 characters total, the same convention as the PR intent block. Over budget, the contract is
truncated at a line boundary and says so, because a silently shortened contract is a contract the
verifier cannot rely on.
"""

from __future__ import annotations

import ast
from pathlib import Path

from ..hosts.base import Diff

MAX_CONTRACT_CHARS = 2_000

# Guard for pathological files: a 500 KB Python file can be parsed but is never worth the time here.
MAX_FILE_BYTES = 400_000


def _docstring_of(node: ast.AST) -> str:
    if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return ""
    return (ast.get_docstring(node, clean=True) or "").strip()


def _spans_overlap(node: ast.AST, lines: set[int]) -> bool:
    """Whether any changed line falls inside this node's body."""
    start = getattr(node, "lineno", 0) or 0
    end = getattr(node, "end_lineno", start) or start
    return any(start <= line <= end for line in lines)


def changed_lines(patch: str) -> set[int]:
    """Line numbers in the new file that the patch adds or modifies.

    Reads the ``@@ -a,b +c,d @@`` hunk headers and counts forward, so this needs no third-party parser
    and works on a patch that was never applied.
    """
    lines: set[int] = set()
    current = 0
    for raw in patch.splitlines():
        if raw.startswith("@@"):
            # @@ -old_start,old_count +new_start,new_count @@
            header = raw.split("+", 1)[1] if "+" in raw else ""
            number = ""
            for char in header:
                if char.isdigit():
                    number += char
                else:
                    break
            current = int(number) if number else 0
            continue
        if current <= 0:
            continue
        if raw.startswith("+"):
            lines.add(current)
            current += 1
        elif raw.startswith("-"):
            continue  # a removal does not advance the new file
        elif raw.startswith("\\"):
            continue  # "\ No newline at end of file"
        else:
            current += 1
    return lines


def _source_for(root: Path, path: str) -> str | None:
    """The file's text as it stands in the workdir, or None if unreadable.

    The workdir holds the checked-out PR head, so this reads the post-change file — which is what the
    docstrings under review belong to.
    """
    if not path or path.startswith("/") or ".." in Path(path).parts:
        return None  # never read outside the workdir
    target = root / path
    try:
        if not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
            return None
        return target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def contract_for(diff: Diff, workdir: str | Path | None, *, limit: int = MAX_CONTRACT_CHARS) -> str:
    """Docstrings for the functions, classes and modules under review.

    Returns an empty string when nothing can be sourced — the caller then renders no contract block at
    all, which keeps a review with no workdir byte-identical to the version before this existed.
    """
    if not workdir:
        return ""
    root = Path(workdir)
    if not root.is_dir():
        return ""

    blocks: list[str] = []
    for file_patch in diff.files:
        source = _source_for(root, file_patch.path)
        if source is None:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue  # a file that does not parse has no contract to read

        lines = changed_lines(file_patch.patch)
        pieces: list[str] = []

        module_doc = _docstring_of(tree)
        if module_doc:
            pieces.append(f"module {file_patch.path}: {module_doc}")

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if not _spans_overlap(node, lines):
                continue
            doc = _docstring_of(node)
            if not doc:
                continue
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            pieces.append(f"{kind} {file_patch.path}:{node.lineno} {node.name}(): {doc}")

        if pieces:
            blocks.append("\n".join(pieces))

    if not blocks:
        return ""

    text = "\n\n".join(blocks)
    if len(text) <= limit:
        return text
    # Truncate at a line boundary and say so: a contract the verifier believes is complete but is not
    # would be worse than no contract at all.
    cut = text[:limit]
    if "\n" in cut:
        cut = cut.rsplit("\n", 1)[0]
    return cut + "\n[... contract truncated at the character cap]"


__all__ = ["MAX_CONTRACT_CHARS", "changed_lines", "contract_for"]
