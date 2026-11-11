"""Scan a folder, extract per file (cached by SHA256), resolve cross-file links,
mine shared concepts, cluster, and write graph-out/."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import os
import posixpath
import re
import time
from collections import Counter, defaultdict

from . import EXTRACTOR_VERSION
from .extract import extract_file, language_of
from .model import Graph, Node, EXTRACTED, INFERRED
from .textutil import split_ident, est_tokens

DEFAULT_IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env", ".env", ".tox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", "target", ".next", ".nuxt",
    "out", "coverage", ".idea", ".vscode", "vendor", "graph-out", ".gradle", "bin", "obj",
    "site-packages", ".cache", ".terraform", "Pods", "DerivedData", ".ipynb_checkpoints",
}
DEFAULT_IGNORE_FILES = ["*.min.js", "*.min.css", "*.map", "*.lock", "package-lock.json", "*.pb.go",
                        "*_pb2.py", "*.generated.*", "*.d.ts", "CHANGELOG*", "CHANGES*", "HISTORY*",
                        "NEWS*", "LICENSE*", "LICENCE*", "COPYING*", "NOTICE*", "AUTHORS*", "CONTRIBUTORS*",
                        "requirements*.txt", "CMakeLists.txt", "robots.txt", "*.egg-info"]
MAX_CODE_BYTES = 1_500_000
MAX_DOC_BYTES = 60_000_000


# ----------------------------------------------------------------------------- scan
def _load_ignore(root):
    pats = list(DEFAULT_IGNORE_FILES)
    for name in (".gitignore", ".kgraphignore"):
        p = os.path.join(root, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8", errors="ignore") as f:
                for l in f:
                    l = l.strip()
                    if l and not l.startswith(("#", "!")):
                        pats.append(l)
    return pats


def _ignored(rel, is_dir, pats):
    name = rel.rsplit("/", 1)[-1]
    for p in pats:
        dir_only = p.endswith("/")
        p2 = p.strip("/")
        if dir_only and not is_dir:
            continue
        if "/" in p2:
            if fnmatch.fnmatch(rel, p2) or fnmatch.fnmatch(rel, p2 + "/*"):
                return True
        elif fnmatch.fnmatch(name, p2):
            return True
    return False


def scan(root, legacy=None):
    from .extract import LEGACY_EXT
    pats = _load_ignore(root)
    files = []
    for d, dirs, fnames in os.walk(root):
        rel_d = os.path.relpath(d, root).replace(os.sep, "/")
        rel_d = "" if rel_d == "." else rel_d + "/"
        dirs[:] = sorted(x for x in dirs if x not in DEFAULT_IGNORE_DIRS and not x.startswith(".")
                         and not _ignored(rel_d + x, True, pats))
        for fn in sorted(fnames):
            rel = rel_d + fn
            kind, _ = language_of(fn)
            if not kind and legacy is not None and os.path.splitext(fn)[1].lower() in LEGACY_EXT \
                    and not _ignored(rel, False, pats):
                legacy.append(rel)
            if not kind or _ignored(rel, False, pats):
                continue
            ap = os.path.join(d, fn)
            try:
                size = os.path.getsize(ap)
            except OSError:
                continue
            if size == 0 or size > (MAX_DOC_BYTES if kind == "doc" else MAX_CODE_BYTES):
                continue
            files.append((ap, rel))
    return files


# ----------------------------------------------------------------------------- build
def build(root, out_dir=None, llm=None, log=print, html=True):
    root = os.path.abspath(root)
    out_dir = out_dir or os.path.join(root, "graph-out")
    cache_dir = os.path.join(out_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)
    t0 = time.time()

    legacy = []
    files = scan(root, legacy)
    if legacy:
        from .extract import LEGACY_EXT
        ex_ = ", ".join(legacy[:3]) + (" …" if len(legacy) > 3 else "")
        log(f"  ! skipped {len(legacy)} old-format Office files ({ex_}). "
            f"Re-save them as {'/'.join(sorted(set(LEGACY_EXT.values())))} to include them.")
    if any(rel.lower().endswith(".pdf") for _, rel in files):
        try:
            import pypdf  # noqa: F401
        except ImportError:
            log("  ! PDFs found but pypdf is not installed: pip install pypdf (or kgraph[pdf]) to index them")
    g = Graph()
    pending, text_feats, columns = [], {}, {}
    corpus_chars = 0
    hits = 0
    live_cache = set()
    for ap, rel in files:
        with open(ap, "rb") as f:
            data = f.read()
        sha = hashlib.sha256(data + rel.encode() + EXTRACTOR_VERSION.encode()).hexdigest()
        cpath = os.path.join(cache_dir, sha[:2], sha + ".json")
        live_cache.add(cpath)
        ex = None
        if os.path.exists(cpath):
            try:
                with open(cpath, encoding="utf-8") as f:
                    ex = json.load(f)
                hits += 1
            except Exception:
                ex = None
        if ex is None:
            try:
                ex = extract_file(ap, rel, data)
            except Exception as e:  # never let one bad file kill the build
                log(f"  ! {rel}: {e}")
                ex = None
            if ex is None:
                continue
            os.makedirs(os.path.dirname(cpath), exist_ok=True)
            with open(cpath, "w", encoding="utf-8") as f:
                json.dump(ex, f, separators=(",", ":"))
        # what an assistant would have to read without the graph
        corpus_chars += ex.get("raw_chars") if ex.get("raw_chars") is not None else len(data)
        for n in ex["nodes"]:
            g.add_node(Node(**n))
        for s, d, r, c in ex["edges"]:
            g.add_edge(s, d, r, c)
        pending.extend(ex["pending"])
        if ex.get("fulltext"):
            tp = os.path.join(out_dir, "text", rel + ".txt")
            os.makedirs(os.path.dirname(tp), exist_ok=True)
            with open(tp, "w", encoding="utf-8") as f:
                f.write(ex["fulltext"])
        text_feats.update(ex.get("text", {}))
        columns.update(ex.get("columns", {}))
    _prune_cache(cache_dir, live_cache)
    log(f"  scanned {len(files)} files ({hits} from cache)")

    _resolve(g, pending)
    _concepts(g, text_feats, columns)
    _link_tables(g, columns)
    if llm:
        from .llm import enrich
        enrich(g, root, out_dir, llm, log=log)
    _cluster(g)

    g.meta = {
        "root": root, "built": time.strftime("%Y-%m-%d %H:%M:%S"), "files": len(files),
        "corpus_tokens": est_tokens(corpus_chars), "seconds": round(time.time() - t0, 2),
    }
    g.save(os.path.join(out_dir, "graph.json"))
    from .report import write_report
    rep = write_report(g, out_dir)
    g.meta["report_tokens"] = est_tokens(rep)
    g.save(os.path.join(out_dir, "graph.json"))
    if html:
        from .html import write_html
        write_html(g, os.path.join(out_dir, "graph.html"))
    _write_gitignore(out_dir)
    return g


def _prune_cache(cache_dir, live):
    for d, _, fns in os.walk(cache_dir):
        if os.path.basename(d) == "llm":
            continue
        for fn in fns:
            p = os.path.join(d, fn)
            if p not in live:
                try:
                    os.remove(p)
                except OSError:
                    pass


def _write_gitignore(out_dir):
    p = os.path.join(out_dir, ".gitignore")
    if not os.path.exists(p):
        with open(p, "w") as f:
            f.write("cache/\n")


# ----------------------------------------------------------------------------- resolution
CODE_KINDS = {"class", "function", "method", "interface", "type", "table"}


def _resolve(g: Graph, pending):
    by_name = defaultdict(list)          # short name -> symbol ids
    for n in g.nodes.values():
        if n.kind in CODE_KINDS:
            short = n.label.rsplit(".", 1)[-1]
            by_name[short].append(n.id)
            if "." in n.label:
                by_name[n.label].append(n.id)
    file_ids = {n.file: n.id for n in g.nodes.values() if n.kind in ("file", "doc")}
    py_mod = {}
    for path, fid in file_ids.items():
        if path.endswith(".py"):
            parts = path[:-3].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            for k in range(len(parts)):
                py_mod.setdefault(".".join(parts[k:]), fid)
    suffix_idx = defaultdict(list)       # basename without ext -> file paths
    for path in file_ids:
        base = path.rsplit("/", 1)[-1]
        suffix_idx[base.rsplit(".", 1)[0].lower()].append(path)
        suffix_idx[base.lower()].append(path)

    imports_of = defaultdict(set)        # file id -> imported file ids (to disambiguate calls)

    def ext_node(name):
        name = name.strip()
        if not name:
            return None
        nid = f"ext:{name}"
        g.add_node(Node(nid, name, "external"))
        return nid

    # pass 1: imports & links (needed to disambiguate calls)
    for p in pending:
        how, src, t = p["how"], p["src"], p["target"]
        if how == "pymodule":
            fid = py_mod.get(t)
            names = p.get("names") or []
            hit = False
            for nm in names:
                sub = py_mod.get(f"{t}.{nm}" if t else nm)
                if sub:
                    g.add_edge(src, sub, "imports", EXTRACTED)
                    imports_of[src].add(sub)
                    hit = True
                elif fid:
                    sym = f"{g.nodes[fid].file}::{nm}"
                    if sym in g.nodes:
                        g.add_edge(src, sym, "imports", EXTRACTED)
                        imports_of[src].add(fid)
                        hit = True
            if fid and not hit:
                g.add_edge(src, fid, "imports", EXTRACTED)
                imports_of[src].add(fid)
            elif not fid and not hit and t:
                e = ext_node(t.split(".")[0])
                if e:
                    g.add_edge(src, e, "imports", EXTRACTED)
        elif how == "path":
            path = _resolve_path(p, file_ids, suffix_idx)
            if path:
                g.add_edge(src, file_ids[path], "imports", EXTRACTED)
                imports_of[src].add(file_ids[path])
            elif not t.startswith("."):
                top = t
                if p.get("lang") in ("js", "ts"):
                    top = "/".join(t.split("/")[:2]) if t.startswith("@") else t.split("/")[0]
                elif p.get("lang") in ("java", "kotlin", "csharp", "scala"):
                    top = ".".join(t.split(".")[:2])
                elif p.get("lang") == "rust":
                    top = t.split("::")[0]
                    if top in ("crate", "self", "super"):
                        continue
                elif p.get("lang") == "go":
                    top = "/".join(t.split("/")[:3])
                e = ext_node(top)
                if e:
                    g.add_edge(src, e, "imports", EXTRACTED)
        elif how == "relpath":
            base = posixpath.dirname(p["file"])
            cand = posixpath.normpath(posixpath.join(base, t)).lstrip("./")
            for c in (cand, cand + ".md", cand.rstrip("/") + "/README.md", cand + "/index.md"):
                if c in file_ids:
                    g.add_edge(src, file_ids[c], "links_to", EXTRACTED)
                    break
        elif how == "wiki":
            for c in suffix_idx.get(t.lower(), []):
                g.add_edge(src, file_ids[c], "links_to", EXTRACTED)
                break

    # pass 2: calls / inheritance by name
    for p in pending:
        if p["how"] != "name":
            continue
        src, t, rel = p["src"], p["target"], p["rel"]
        if src not in g.nodes:
            continue
        cands = [c for c in by_name.get(t, []) if c != src]
        if rel in ("inherits", "implements"):
            cands = [c for c in cands if g.nodes[c].kind in ("class", "interface", "type")]
        if not cands:
            continue
        src_file = g.nodes[src].file
        same = [c for c in cands if g.nodes[c].file == src_file]
        if same:
            # prefer same class for self.method() calls
            cls = src.rsplit(".", 1)[0] if g.nodes[src].kind == "method" else None
            pick = [c for c in same if cls and c.startswith(cls + ".")] or same
            g.add_edge(src, pick[0], rel, EXTRACTED if len(pick) == 1 else INFERRED)
            continue
        fid = f"file:{src_file}"
        imported = [c for c in cands if f"file:{g.nodes[c].file}" in imports_of.get(fid, ()) or c in imports_of.get(fid, ())]
        if imported:
            for c in imported[:2]:
                g.add_edge(src, c, rel, INFERRED)
        elif len(cands) == 1:
            g.add_edge(src, cands[0], rel, INFERRED)


def _resolve_path(p, file_ids, suffix_idx):
    t, lang, src_file = p["target"], p.get("lang"), p["file"]
    base = posixpath.dirname(src_file)
    if t.startswith("."):
        cand = posixpath.normpath(posixpath.join(base, t))
        exts = ["", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".rb", ".php", ".lua", ".sh",
                "/index.ts", "/index.tsx", "/index.js", ".h", ".hpp"]
        for e in exts:
            if cand + e in file_ids:
                return cand + e
        return None
    if lang in ("c", "cpp"):
        for c in (posixpath.normpath(posixpath.join(base, t)), t):
            if c in file_ids:
                return c
        for c in suffix_idx.get(t.rsplit("/", 1)[-1].lower(), []):
            if c.endswith(t):
                return c
        return None
    # dotted / :: / slash module paths -> path suffix match
    parts = re.split(r"[./\\]|::", t)
    parts = [x for x in parts if x and x not in ("crate", "self", "super")]
    for k in range(len(parts), 0, -1):
        stem = parts[k - 1].lower()
        tail = "/".join(parts[:k]).lower()
        for c in suffix_idx.get(stem, []):
            if c.lower().rsplit(".", 1)[0].endswith(tail) or (k == 1 and lang == "rust"):
                return c
        if lang not in ("rust", "go"):
            break
    return None


# ----------------------------------------------------------------------------- concepts
def _link_tables(g: Graph, columns):
    """Tables/sheets that share a column name (e.g. customer_id) are probably joinable."""
    by_col = defaultdict(list)
    for sid, cols in columns.items():
        for c in cols:
            if not re.fullmatch(r"col\d+", c) and len(c) >= 3:
                by_col[c.lower()].append(sid)
    for c, sids in by_col.items():
        if 2 <= len(sids) <= 8:
            for i, a in enumerate(sids):
                for b in sids[i + 1:]:
                    g.add_edge(a, b, f"shares_column:{c}", INFERRED)


def _concepts(g: Graph, feats, columns=None):
    if not feats:
        return
    n_docs = len(feats)
    df = Counter()
    for f in feats.values():
        df.update(f["terms"].keys())
    max_df = max(3, int(0.3 * n_docs))
    cand = {t: c for t, c in df.items() if 2 <= c <= max_df
            and (" " in t or (len(t) >= 5 and not t.endswith(("ly", "ed", "ing"))))}
    # rank: bigrams are more informative; long unigrams next
    score = {t: c * (1.8 if " " in t else 1.0) for t, c in cand.items()}
    keep = set(sorted(score, key=lambda t: (-score[t], t))[:min(400, 20 + n_docs * 2)])
    # merge chained bigrams that always co-occur: "multi hop" + "hop question" -> "multi hop question"
    where = defaultdict(set)
    for nid, f in feats.items():
        for t in f["terms"]:
            if t in keep:
                where[t].add(nid)
    merged = {}
    bigrams = [t for t in keep if t.count(" ") == 1]
    by_first = defaultdict(list)
    for t in bigrams:
        by_first[t.split()[0]].append(t)
    for a in bigrams:
        for b in by_first.get(a.split()[1], []):
            if a in keep and b in keep and a != b and where[a] == where[b]:
                tri = f"{a} {b.split()[1]}"
                keep.discard(a); keep.discard(b); keep.add(tri)
                merged[a] = merged[b] = tri
                where[tri] = where[a]
                df[tri] = df[a]
    if merged:
        for f in feats.values():
            for t in list(f["terms"]):
                if t in merged:
                    m = merged[t]
                    f["terms"][m] = max(f["terms"].get(m, 0), f["terms"][t])
    # drop unigrams wholly covered by a kept bigram with the same df
    for t in list(keep):
        if " " in t and t in keep:
            for w in t.split():
                if w in keep and df[w] == df[t]:
                    keep.discard(w)

    code_by_words = defaultdict(list)
    for n in g.nodes.values():
        if n.kind in CODE_KINDS:
            words = " ".join(split_ident(n.label.rsplit(".", 1)[-1]))
            if words:
                code_by_words[words].append(n.id)

    names = defaultdict(list)
    for n in g.nodes.values():
        if n.kind in CODE_KINDS:
            names[n.label.rsplit(".", 1)[-1]].append(n.id)
            names[n.label].append(n.id)
    files_by_name = defaultdict(list)
    for n in g.nodes.values():
        if n.kind in ("file", "doc"):
            base = n.file.rsplit("/", 1)[-1]
            files_by_name[base].append(n.id)
            if "_" in base or "-" in base:      # "churn_model.py" also matches "churn_model"
                files_by_name[base.rsplit(".", 1)[0]].append(n.id)
    for sid, cols in (columns or {}).items():   # column names point at their tables
        for c in cols:
            if "_" in c or re.search(r"[a-z][A-Z]", c):
                names[c].append(sid)

    for nid, f in feats.items():
        if nid not in g.nodes:
            continue
        ranked = sorted(((tf * math.log(1 + n_docs / df[t]), t) for t, tf in f["terms"].items() if t in keep),
                        reverse=True)[:8]
        for _, t in ranked:
            cid = f"concept:{t}"
            g.add_node(Node(cid, t, "concept"))
            g.add_edge(nid, cid, "mentions", INFERRED)
        # doc -> code: identifiers in `backticks` / snake_case / camelCase that exist in code
        for ident in f.get("idents", []):
            if columns and nid in columns:
                break               # table <-> table links come from shared columns instead
            hits = names.get(ident) or files_by_name.get(ident) or []
            if 0 < len(hits) <= 3:
                for h in hits:
                    g.add_edge(nid, h, "mentions", INFERRED)
    for t in keep:
        cid = f"concept:{t}"
        if cid in g.nodes:
            for sym in code_by_words.get(t, [])[:3]:
                g.add_edge(cid, sym, "about", INFERRED)


# ----------------------------------------------------------------------------- clustering
W = {"defines": 3.0, "contains": 3.0, "imports": 1.5, "calls": 1.0, "inherits": 2.0,
     "implements": 2.0, "links_to": 1.5, "mentions": 0.6, "about": 0.8}


def _cluster(g: Graph):
    ids = [n for n in g.nodes if g.nodes[n].kind != "external"]
    idset = set(ids)
    try:
        import networkx as nx
        G = nx.Graph()
        G.add_nodes_from(ids)
        for e in g.edges:
            if e.src in idset and e.dst in idset:
                w = W.get(e.rel, 1.0) * (1.0 if e.conf == EXTRACTED else 0.6)
                G.add_edge(e.src, e.dst, weight=G.get_edge_data(e.src, e.dst, {}).get("weight", 0) + w)
        comms = nx.community.louvain_communities(G, weight="weight", seed=42, resolution=1.0)
        label = {}
        for i, c in enumerate(sorted(comms, key=len, reverse=True)):
            for n in c:
                label[n] = i
    except Exception:
        label = _label_propagation(g, ids, idset)
    # externals join the community that imports them most
    for n in g.nodes.values():
        if n.kind == "external":
            votes = Counter(label.get(o) for o, *_ in g.adj.get(n.id, ()) if o in label)
            label[n.id] = votes.most_common(1)[0][0] if votes else -1
    for nid, c in label.items():
        g.nodes[nid].community = c


def _label_propagation(g, ids, idset):
    nbrs = defaultdict(list)
    for e in g.edges:
        if e.src in idset and e.dst in idset:
            w = W.get(e.rel, 1.0) * (1.0 if e.conf == EXTRACTED else 0.6)
            nbrs[e.src].append((e.dst, w))
            nbrs[e.dst].append((e.src, w))
    label = {n: i for i, n in enumerate(sorted(ids))}
    order = sorted(ids, key=lambda n: (-len(nbrs[n]), n))
    for _ in range(25):
        changed = 0
        for n in order:
            if not nbrs[n]:
                continue
            votes = defaultdict(float)
            for o, w in nbrs[n]:
                votes[label[o]] += w
            best = max(votes.items(), key=lambda kv: (kv[1], -kv[0]))[0]
            if best != label[n]:
                label[n] = best
                changed += 1
        if not changed:
            break
    # merge tiny communities into their strongest neighbour community
    sizes = Counter(label.values())
    for n in order:
        if sizes[label[n]] < 3 and nbrs[n]:
            votes = defaultdict(float)
            for o, w in nbrs[n]:
                if label[o] != label[n]:
                    votes[label[o]] += w
            if votes:
                sizes[label[n]] -= 1
                label[n] = max(votes.items(), key=lambda kv: kv[1])[0]
                sizes[label[n]] += 1
    remap = {c: i for i, (c, _) in enumerate(Counter(label.values()).most_common())}
    return {n: remap[c] for n, c in label.items()}
