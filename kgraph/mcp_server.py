"""Minimal stdio MCP server (JSON-RPC 2.0, newline-delimited). No SDK needed.

Register with Claude Code:   claude mcp add kgraph -- kgraph serve
Other clients:               command "kgraph", args ["serve", "--graph", "/abs/path/graph-out/graph.json"]
"""
import json
import os
import sys

from . import __version__
from .model import Graph
from .query import query, explain, path

TOOLS = [
    {"name": "kgraph_query",
     "description": "Search the project knowledge graph. Returns the most relevant nodes (functions, classes, doc sections, concepts) "
                    "with file:line ranges and their relations, within a token budget. Use BEFORE grep/reading files.",
     "inputSchema": {"type": "object", "properties": {
         "question": {"type": "string", "description": "Natural language question or keywords"},
         "budget": {"type": "integer", "description": "Approx token budget for the answer (default 1500)"}},
         "required": ["question"]}},
    {"name": "kgraph_explain",
     "description": "Show one node (function/class/file/section/concept) and everything connected to it.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "kgraph_path",
     "description": "Shortest connection between two nodes, e.g. how a doc concept reaches a function.",
     "inputSchema": {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
                     "required": ["a", "b"]}},
    {"name": "kgraph_report",
     "description": "Return GRAPH_REPORT.md: project overview, core nodes, communities, directory map.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def serve(graph_path):
    state = {"g": None, "mtime": 0}

    def graph():
        m = os.path.getmtime(graph_path)
        if state["g"] is None or m != state["mtime"]:   # hot reload after `kgraph build`
            state["g"], state["mtime"] = Graph.load(graph_path), m
        return state["g"]

    def reply(id_, result=None, error=None):
        msg = {"jsonrpc": "2.0", "id": id_}
        if error:
            msg["error"] = error
        else:
            msg["result"] = result
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, id_, params = req.get("method"), req.get("id"), req.get("params") or {}
        if id_ is None:          # notification
            continue
        try:
            if method == "initialize":
                reply(id_, {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "kgraph", "version": __version__}})
            elif method == "ping":
                reply(id_, {})
            elif method == "tools/list":
                reply(id_, {"tools": TOOLS})
            elif method == "tools/call":
                name, a = params.get("name"), params.get("arguments") or {}
                g = graph()
                if name == "kgraph_query":
                    text = query(g, a["question"], budget=int(a.get("budget") or 1500))
                elif name == "kgraph_explain":
                    text = explain(g, a["name"])
                elif name == "kgraph_path":
                    text = path(g, a["a"], a["b"])
                elif name == "kgraph_report":
                    with open(os.path.join(os.path.dirname(graph_path), "GRAPH_REPORT.md"), encoding="utf-8") as f:
                        text = f.read()
                else:
                    reply(id_, error={"code": -32602, "message": f"unknown tool {name}"})
                    continue
                reply(id_, {"content": [{"type": "text", "text": text}]})
            else:
                reply(id_, error={"code": -32601, "message": f"method not found: {method}"})
        except Exception as e:
            reply(id_, {"content": [{"type": "text", "text": f"kgraph error: {e}"}], "isError": True})
