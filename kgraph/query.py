"""Token-budgeted retrieval over the graph: query / explain / path."""
import heapq
import math
from collections import Counter, defaultdict, deque

from .extract import SIDECAR_EXT
from .model import Graph, INFERRED
from .report import loc
from .textutil import split_ident, STOP, est_tokens, stem

KIND_TAG = {"function": "fn", "method": "meth", "class": "class", "interface": "iface", "type": "type",
            "file": "file", "doc": "doc", "section": "sec", "concept": "concept", "external": "ext",
            "entity": "ent", "table": "table"}


class Index:
    def __init__(self, g: Graph):
        self.g = g
        self.docs = {}
        df = Counter()
        for n in g.nodes.values():
            tf = Counter()
            for w in split_ident(n.label):
                tf[stem(w)] += 3
            if n.file and n.kind not in ("concept", "external"):
                for w in split_ident(n.file):
                    tf[stem(w)] += 1
            for w in split_ident(n.summary):
                if w not in STOP:
                    tf[stem(w)] += 1
            self.docs[n.id] = tf
            df.update(tf.keys())
        N = max(1, len(self.docs))
        self.idf = {w: math.log(1 + N / c) for w, c in df.items()}
        self.vocab = sorted(df)

    def search(self, q, k=8):
        g = self.g
        qt = [stem(w) for w in split_ident(q) if w not in STOP and len(w) > 1] or [stem(w) for w in split_ident(q)]
        ql = q.strip().lower()
        expanded = {}   # vocab word -> (weight, original query term)
        for w in qt:
            expanded[w] = (1.0, w)
            if len(w) >= 4:  # prefix match: "token" ~ "tokenize", "config" ~ "configuration"
                for v in self.vocab:
                    if v != w and (v.startswith(w) or (len(v) >= 4 and w.startswith(v))):
                        expanded.setdefault(v, (0.5, w))
        nq = len(set(qt))
        scores = {}
        for nid, tf in self.docs.items():
            s = 0.0
            matched = set()
            for w, (wt, orig) in expanded.items():
                if w in tf:
                    s += wt * self.idf.get(w, 0) * (1 + math.log(tf[w]))
                    matched.add(orig)
            if not s:
                continue
            s *= (len(matched) / nq) ** 1.5      # reward covering more of the question
            n = g.nodes[nid]
            if is_test(n):
                s *= 0.7
            lab = n.label.lower()
            if lab == ql or lab.rsplit(".", 1)[-1] == ql:
                s *= 3
            if n.kind in ("external",):
                s *= 0.4
            s += 0.15 * math.log(1 + g.degree(nid))
            scores[nid] = s
        return sorted(scores.items(), key=lambda kv: -kv[1])[:k]


def is_test(n):
    f = (n.file or "").lower()
    return "test" in f.rsplit("/", 1)[-1] or "/tests/" in "/" + f or "/test/" in "/" + f or "spec." in f


def resolve(g: Graph, name, idx=None):
    """Find a node by id, label, file path, or fuzzy search."""
    if name in g.nodes:
        return name
    low = name.lower().strip()
    exact = [n for n in g.nodes.values() if n.label.lower() == low or (n.file or "").lower() == low]
    if not exact:
        exact = [n for n in g.nodes.values() if n.label.lower().rsplit(".", 1)[-1] == low]
    if not exact:
        exact = [n for n in g.nodes.values() if n.label.lower().endswith(low) or (n.file or "").lower().endswith(low)]
    if exact:
        return max(exact, key=lambda n: g.degree(n.id)).id
    idx = idx or Index(g)
    hits = idx.search(name, k=1)
    return hits[0][0] if hits else None


def _line(n, tag):
    s = f"{tag} {KIND_TAG.get(n.kind, n.kind)} {n.label}"
    l = loc(n)
    if l and l != n.label:
        s += f"  [{l}]"
    if n.summary:
        s += f" — {n.summary[:120]}"
    return s


def query(g: Graph, q, budget=1500, seeds=6, depth=2):
    idx = Index(g)
    hits = idx.search(q, k=seeds * 2)
    if not hits:
        return f'kgraph: no match for "{q}". Try other keywords or read GRAPH_REPORT.md.'
    top = hits[0][1]
    hits = [h for h in hits if h[1] >= 0.3 * top][:seeds]

    # best-first expansion
    picked, order = {}, []
    heap = [(-s, nid, 0) for nid, s in hits]
    heapq.heapify(heap)
    used = 0
    while heap:
        neg, nid, d = heapq.heappop(heap)
        if nid in picked:
            continue
        n = g.nodes[nid]
        links = sum(1 for o, *_ in g.adj.get(nid, ()) if o in picked)
        cost = est_tokens(_line(n, "N00")) + 2 + 3 * links
        if used + cost > budget and order:
            continue
        if d > 0 and -neg < 0.12 * top:
            continue   # too weakly related to be worth tokens
        picked[nid] = -neg
        order.append(nid)
        used += cost
        if d >= depth:
            continue
        nbrs = g.adj.get(nid, ())
        # hubs (e.g. a package __init__ imported by every test) spread little relevance per neighbour
        hub = min(1.0, 3.0 / math.sqrt(max(1, len(nbrs))))
        for o, rel, direction, conf in nbrs:
            if o in picked:
                continue
            on = g.nodes[o]
            w = (0.6 if conf != INFERRED else 0.35) * hub
            if on.kind == "external":
                w *= 0.3
            elif on.kind == "concept":
                w *= 0.5
            elif on.kind in ("file", "doc") and not on.summary:
                w *= 0.6
            if is_test(on) and not is_test(n):
                w *= 0.4
            if rel in ("defines", "contains"):
                w *= 1.3
            heapq.heappush(heap, (neg * w, o, d + 1))

    tag = {nid: f"N{i + 1}" for i, nid in enumerate(order)}
    out = [f'# kgraph query: "{q}" — {len(order)} nodes (~{budget} token budget)', "## Nodes"]
    for nid in order:
        out.append(_line(g.nodes[nid], tag[nid]))
    rels = defaultdict(list)
    for e in g.edges:
        if e.src in tag and e.dst in tag:
            mark = "~" if e.conf == INFERRED else ""
            rels[e.src].append(f"{mark}{e.rel} {tag[e.dst]}")
    if rels:
        out.append("## Edges  (~ = inferred)")
        for nid in order:
            if nid in rels:
                out.append(f"{tag[nid]}: " + ", ".join(rels[nid]))
    # suggest minimal reads: merge ranges per file
    ranges = defaultdict(list)
    for nid in order[:12]:
        n = g.nodes[nid]
        if n.file and n.line and n.kind not in ("file", "doc"):
            end = n.end_line or n.line + 30
            if end - n.line > 120:          # huge class/function: header only, members are listed separately
                end = n.line + 15
            ranges[n.file].append((n.line, end))
    if ranges:
        out.append("## Read next (only these ranges)")
        for f, rs in ranges.items():
            rs.sort()
            merged = [list(rs[0])]
            for a, b in rs[1:]:
                if a <= merged[-1][1] + 5:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            shown = f"graph-out/text/{f}.txt" if f.lower().endswith(SIDECAR_EXT) else f
            out.append(f"{shown}: " + ", ".join(f"{a}-{b}" for a, b in merged))
    return "\n".join(out)


def explain(g: Graph, name, limit=60):
    nid = resolve(g, name)
    if not nid:
        return f"kgraph: no node matching '{name}'"
    n = g.nodes[nid]
    out = [_line(n, "*"), f"community C{n.community} · degree {g.degree(nid)}"]
    groups = defaultdict(list)
    for o, rel, direction, conf in g.adj.get(nid, ()):
        key = f"{rel} →" if direction == "out" else f"← {rel}"
        on = g.nodes[o]
        mark = "~" if conf == INFERRED else ""
        groups[key].append((not is_test(on), g.degree(o), f"{mark}{on.label}" + (f" [{loc(on)}]" if loc(on) and loc(on) != on.label else "")))
    shown = 0
    def real(k):
        return sum(1 for t in groups[k] if t[0])
    for key in sorted(groups, key=lambda k: (-real(k), -len(groups[k]))):
        ranked = sorted(groups[key], reverse=True)
        items = [s for flag, _, s in ranked if flag]
        tests = [s for flag, _, s in ranked if not flag]
        if len(tests) <= 3:
            items += tests
            tests = []
        take = items[:max(3, limit - shown)]
        shown += len(take)
        extra = []
        if len(items) > len(take):
            extra.append(f"+{len(items) - len(take)} more")
        if tests:
            files = sorted({t.rsplit("[", 1)[-1].split(":")[0] for t in tests})
            extra.append(f"+{len(tests)} tests in " + ", ".join(files[:3]) + (" …" if len(files) > 3 else ""))
        out.append(f"{key}: " + "; ".join(take) + (" (" + "; ".join(extra) + ")" if extra else ""))
        if shown >= limit:
            break
    return "\n".join(out)


def path(g: Graph, a, b, max_len=8):
    idx = Index(g)
    sa, sb = resolve(g, a, idx), resolve(g, b, idx)
    if not sa or not sb:
        return f"kgraph: could not find {'%r' % a if not sa else '%r' % b}"
    prev = {sa: None}
    dq = deque([sa])
    while dq:
        x = dq.popleft()
        if x == sb:
            break
        for o, rel, direction, conf in g.adj.get(x, ()):
            if o not in prev and g.nodes[o].kind != "external":
                prev[o] = (x, rel, direction, conf)
                dq.append(o)
    if sb not in prev:
        return f"kgraph: no path between {g.nodes[sa].label} and {g.nodes[sb].label}"
    chain = []
    x = sb
    while prev[x]:
        p, rel, direction, conf = prev[x]
        chain.append((p, rel, direction, conf, x))
        x = p
    chain.reverse()
    out = [f"path ({len(chain)} hops):", _line(g.nodes[sa], " ")]
    for p, rel, direction, conf, x in chain:
        mark = "~" if conf == INFERRED else ""
        arrow = f"--{mark}{rel}-->" if direction == "out" else f"<--{mark}{rel}--"
        out.append(f"  {arrow} " + _line(g.nodes[x], "").strip())
    return "\n".join(out)
