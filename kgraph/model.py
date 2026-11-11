"""Graph data model. Plain dicts/lists so everything is JSON-serialisable."""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Optional

# Edge confidence levels:
#   EXTRACTED - read directly from source (AST, import statement, heading structure)
#   INFERRED  - resolved by name matching / text heuristics / LLM; may be wrong
EXTRACTED = "EXTRACTED"
INFERRED = "INFERRED"


@dataclass
class Node:
    id: str
    label: str
    kind: str                      # file, module, class, function, method, section, concept, external, entity
    file: Optional[str] = None
    line: Optional[int] = None
    end_line: Optional[int] = None
    summary: str = ""
    community: Optional[int] = None

    def to_dict(self):
        return {k: v for k, v in asdict(self).items() if v not in (None, "")}


@dataclass
class Edge:
    src: str
    dst: str
    rel: str                       # contains, defines, imports, calls, inherits, mentions, links_to, related_to ...
    conf: str = EXTRACTED

    def to_dict(self):
        return asdict(self)


class Graph:
    def __init__(self):
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self._edge_keys: set = set()
        self._adj = None
        self.meta: dict = {}

    # ---- building -------------------------------------------------------
    def add_node(self, node: Node) -> Node:
        cur = self.nodes.get(node.id)
        if cur is None:
            self.nodes[node.id] = node
            self._adj = None
            return node
        # merge: keep the richer fields
        for f in ("file", "line", "end_line"):
            if getattr(cur, f) is None and getattr(node, f) is not None:
                setattr(cur, f, getattr(node, f))
        if len(node.summary) > len(cur.summary):
            cur.summary = node.summary
        if cur.kind in ("external", "concept") and node.kind not in ("external", "concept"):
            cur.kind = node.kind
        return cur

    def add_edge(self, src: str, dst: str, rel: str, conf: str = EXTRACTED):
        if src == dst or src not in self.nodes or dst not in self.nodes:
            return
        key = (src, dst, rel)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self.edges.append(Edge(src, dst, rel, conf))
        self._adj = None

    # ---- reading --------------------------------------------------------
    @property
    def adj(self):
        """id -> list of (other_id, rel, direction 'out'|'in', conf)."""
        if self._adj is None:
            a = defaultdict(list)
            for e in self.edges:
                a[e.src].append((e.dst, e.rel, "out", e.conf))
                a[e.dst].append((e.src, e.rel, "in", e.conf))
            self._adj = a
        return self._adj

    def degree(self, nid: str) -> int:
        return len(self.adj.get(nid, ()))

    # ---- io -------------------------------------------------------------
    def to_json(self) -> dict:
        return {
            "meta": self.meta,
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges],
        }

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_json(), f, separators=(",", ":"))

    @classmethod
    def load(cls, path) -> "Graph":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        g = cls()
        g.meta = data.get("meta", {})
        for n in data["nodes"]:
            g.nodes[n["id"]] = Node(**n)
        for e in data["edges"]:
            g.add_edge(e["src"], e["dst"], e["rel"], e.get("conf", EXTRACTED))
        return g
