"""GRAPH_REPORT.md: the one file an LLM reads first instead of the raw repo."""
import os
from collections import Counter, defaultdict

from .model import Graph, INFERRED
from .textutil import est_tokens

STRUCT = {"class", "function", "method", "interface", "type", "table", "file", "doc", "section"}


def loc(n):
    if not n.file:
        return ""
    if n.line and n.kind not in ("file", "doc"):
        return f"{n.file}:{n.line}" + (f"-{n.end_line}" if n.end_line and n.end_line != n.line else "")
    return n.file


def community_info(g: Graph):
    members = defaultdict(list)
    for n in g.nodes.values():
        if n.community is not None and n.community >= 0:
            members[n.community].append(n)
    info = {}
    for c, ms in members.items():
        ms.sort(key=lambda n: -g.degree(n.id))
        dirs = Counter((n.file.rsplit("/", 1)[0] if "/" in n.file else ".") for n in ms if n.file)
        top_dir = dirs.most_common(1)[0][0] if dirs else ""
        hub = next((n for n in ms if n.kind in STRUCT - {"file", "doc"}), ms[0])
        label = hub.label
        if hub.kind == "section" and hub.file:   # "Abstract" says nothing; use the document's name
            files = Counter(n.file for n in ms if n.file)
            label = files.most_common(1)[0][0].rsplit("/", 1)[-1]
        name = f"{top_dir}/ · {label}" if top_dir and top_dir != "." else label
        info[c] = {"name": name[:70], "members": ms, "dirs": dirs}
    return info


def write_report(g: Graph, out_dir):
    m = g.meta
    lines = []
    A = lines.append
    kinds = Counter(n.kind for n in g.nodes.values())
    inferred = sum(1 for e in g.edges if e.conf == INFERRED)
    A(f"# Knowledge graph: {os.path.basename(m.get('root', '')) or 'project'}")
    A("")
    A(f"Built {m.get('built')} · {m.get('files')} files · {len(g.nodes)} nodes · {len(g.edges)} edges "
      f"({inferred} inferred) · raw corpus ≈ {m.get('corpus_tokens', 0):,} tokens")
    A("Node kinds: " + ", ".join(f"{k} {v}" for k, v in kinds.most_common()))
    A("")
    A("## How to use this (for the assistant)")
    A("- Read this report first. Do not grep or open whole files until you have checked the graph.")
    A('- `python -m kgraph query "<question or keywords>"` → the relevant subgraph with file:line ranges (token-budgeted).')
    A("- `python -m kgraph explain <name>` → one node with all its neighbours. `python -m kgraph path <a> <b>` → how two things connect.")
    A("- Then open only the line ranges shown. Edges marked `~` are INFERRED (name/text matching) and may be wrong.")
    A("")

    # god nodes
    gods = sorted((n for n in g.nodes.values() if n.kind not in ("external", "concept", "file", "doc")),
                  key=lambda n: -g.degree(n.id))[:15]
    if gods:
        A("## Core nodes (most connected)")
        for n in gods:
            s = f" — {n.summary[:90]}" if n.summary else ""
            A(f"- **{n.label}** ({n.kind}, {loc(n)}, deg {g.degree(n.id)}){s}")
        A("")

    # communities
    info = community_info(g)
    cross = Counter()
    for e in g.edges:
        a, b = g.nodes[e.src].community, g.nodes[e.dst].community
        if a is not None and b is not None and a != b and a >= 0 and b >= 0:
            cross[(min(a, b), max(a, b))] += 1
    ranked = sorted(info, key=lambda c: -len(info[c]["members"]))
    A(f"## Communities ({len(info)})")
    for c in ranked[:30]:
        d = info[c]
        ms = d["members"]
        key = [n for n in ms if n.kind not in ("external", "concept")][:8]
        exts = [n.label for n in ms if n.kind == "external"][:6]
        cons = [n.label for n in ms if n.kind == "concept"][:6]
        links = sorted(((k, v) for k, v in cross.items() if c in k), key=lambda kv: -kv[1])[:4]
        A(f"### C{c}: {d['name']} ({len(ms)} nodes)")
        if d["dirs"]:
            A("dirs: " + ", ".join(f"{k}/ ({v})" for k, v in d["dirs"].most_common(4)))
        A("key: " + ", ".join(f"{n.label} [{loc(n)}]" for n in key))
        if cons:
            A("concepts: " + ", ".join(cons))
        if exts:
            A("uses: " + ", ".join(exts))
        if links:
            A("links: " + ", ".join(f"C{(k[0] if k[1] == c else k[1])} ({v})" for k, v in links))
        A("")
    if len(ranked) > 30:
        A(f"(+{len(ranked) - 30} smaller communities — use `python -m kgraph query`)")
        A("")

    # concepts
    concepts = sorted((n for n in g.nodes.values() if n.kind in ("concept", "entity")),
                      key=lambda n: -g.degree(n.id))[:25]
    if concepts:
        A("## Key concepts (from docs/papers)")
        A(", ".join(f"{n.label} ({g.degree(n.id)})" for n in concepts))
        A("")

    # surprising links: inferred edges across communities between docs and code
    sur = []
    for e in g.edges:
        if e.conf != INFERRED or e.rel not in ("mentions", "calls", "about"):
            continue
        a, b = g.nodes[e.src], g.nodes[e.dst]
        if a.community != b.community and a.kind != "concept" and b.kind != "concept" \
                and b.kind != "external":
            sur.append((g.degree(a.id) * g.degree(b.id), a, e.rel, b))
    if sur:
        A("## Cross-cutting links (inferred, verify before trusting)")
        for _, a, r, b in sorted(sur, key=lambda x: -x[0])[:10]:
            A(f"- {a.label} [{loc(a)}] ~{r}~> {b.label} [{loc(b)}]")
        A("")

    # directory map
    by_dir = defaultdict(list)
    for n in g.nodes.values():
        if n.kind in ("file", "doc"):
            by_dir[n.file.rsplit("/", 1)[0] if "/" in n.file else "."].append(n)
    A("## Directory map")
    for d in sorted(by_dir)[:60]:
        fs = sorted(by_dir[d], key=lambda n: -g.degree(n.id))
        parts = []
        for f in fs[:6]:
            s = f.file.rsplit("/", 1)[-1]
            if f.summary:
                s += f" ({f.summary[:50]})"
            parts.append(s)
        more = f" +{len(fs) - 6}" if len(fs) > 6 else ""
        A(f"- {d}/: " + "; ".join(parts) + more)
    if len(by_dir) > 60:
        A(f"- … {len(by_dir) - 60} more directories")
    A("")

    text = "\n".join(lines)
    A(f"_Report ≈ {est_tokens(text):,} tokens vs ≈ {m.get('corpus_tokens', 0):,} for the raw files._")
    text = "\n".join(lines) + "\n"
    with open(os.path.join(out_dir, "GRAPH_REPORT.md"), "w", encoding="utf-8") as f:
        f.write(text)
    return text
