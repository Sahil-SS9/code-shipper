"""One sentence, on every surface, about who merges.

D16a: Code-Shipper never merges, and every surface has to say so in the same words. Three surfaces had
three different phrasings — "It never approves and never merges", "You merge on GitHub", "Merge on GitHub
when you are ready" — so a user reading two of them could reasonably think they said different things.

This is the product's central safety promise. It gets one constant and one test per surface.
"""

from __future__ import annotations

# The exact sentence D16a specifies. Do not paraphrase it: the whole point is that it reads identically
# wherever a user encounters it.
NEVER_MERGES = "Code-Shipper never merges. Merge on GitHub."

__all__ = ["NEVER_MERGES"]
