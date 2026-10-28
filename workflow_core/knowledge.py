"""Memory extensions (roadmap M4): ranked semantic recall and a Basic Memory shadow copy.

Both read only fresh verified quotes through recall(); stale or revoked records never appear.
"""
from collections import Counter
import math
from pathlib import Path
import re

from .feedback import recall
from .project import Rejected, digest

WORD = re.compile(r"\w+", re.UNICODE)


def vector(text, idf):
    counts = Counter(w for w in WORD.findall(text.casefold()) if len(w) > 1)
    return {w: c * idf.get(w, 0.0) for w, c in counts.items()}


def cosine(a, b):
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return dot / norm if norm else 0.0


def semantic(store, query, limit=5):
    """TF-IDF cosine ranking: lexical-semantic, offline, no embedding model or provider key."""
    if not query.strip():
        raise Rejected("Semantic search needs a query")
    memories = recall(store, "", limit=10000)["hits"]
    documents = [set(WORD.findall(m["quote"].casefold())) for m in memories]
    total = len(documents)
    idf = {}
    for words in documents:
        for word in words:
            idf[word] = idf.get(word, 0) + 1
    idf = {w: math.log((1 + total) / (1 + n)) + 1 for w, n in idf.items()}
    wanted = vector(query, idf)
    ranked = sorted(((cosine(wanted, vector(m["quote"], idf)), m) for m in memories), key=lambda x: -x[0])
    hits = [dict(m, score=round(score, 4)) for score, m in ranked if score > 0][:limit]
    return {"hits": hits, "method": "tfidf_cosine_offline", "searched": total,
            "scope": store.project.config["project_id"]}


def shadow_export(store, target):
    """Write verified quotes as Basic Memory notes into a new folder; never touches a live Basic Memory home."""
    target = Path(target).resolve()
    if target.exists() and any(target.iterdir()):
        raise Rejected("Shadow folder must be new or empty")
    if target.is_relative_to(store.project.home):
        raise Rejected("Shadow copy must live outside workflow state")
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for memory in recall(store, "", limit=10000)["hits"]:
        title = "quote-" + memory["id"][:8]
        body = ("---\n" f"title: {title}\n" "type: note\n" f"permalink: agent-workflow/{title}\n"
                "tags:\n- agent-workflow\n- verified-quote\n---\n\n" f"# {title}\n\n> {memory['quote']}\n\n"
                "## Observations\n" f"- [source] {memory['source']} sha256={memory['source_sha256']}\n"
                "- [authority] data_only, exact source quote, not a generalized fact\n")
        path = target / (title + ".md")
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
        written.append({"path": str(path), "sha256": digest(body.encode("utf-8"))})
    return {"status": "shadow_exported", "folder": str(target), "notes": len(written), "files": written,
            "format": "basic-memory markdown (frontmatter + observations)", "live_basic_memory_changed": False}
