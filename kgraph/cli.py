"""kgraph command line."""
import argparse
import os
import sys

from . import __version__


def find_graph(explicit=None):
    if explicit:
        p = explicit if explicit.endswith(".json") else os.path.join(explicit, "graph.json")
        if os.path.exists(p):
            return p
        sys.exit(f"kgraph: no graph at {p}")
    d = os.getcwd()
    while True:
        p = os.path.join(d, "graph-out", "graph.json")
        if os.path.exists(p):
            return p
        nd = os.path.dirname(d)
        if nd == d:
            sys.exit("kgraph: no graph-out/graph.json found here or in parent folders. Run `python -m kgraph build .` first.")
        d = nd


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m kgraph", description="Folder -> knowledge graph -> fewer LLM tokens.")
    ap.add_argument("--version", action="version", version=f"kgraph {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build/refresh graph-out/ for a folder (incremental)")
    b.add_argument("path", nargs="?", default=".")
    b.add_argument("--out", help="output dir (default <path>/graph-out)")
    b.add_argument("--llm", choices=["anthropic", "openai", "ollama"], help="extra entity/relation extraction for docs")
    b.add_argument("--no-html", action="store_true")

    for name, h in (("query", "relevant subgraph for a question"), ("explain", "one node and its neighbours")):
        p = sub.add_parser(name, help=h)
        p.add_argument("text", nargs="+")
        p.add_argument("--graph")
        if name == "query":
            p.add_argument("--budget", type=int, default=1500, help="approx tokens of output (default 1500)")
            p.add_argument("--depth", type=int, default=2)

    p = sub.add_parser("path", help="shortest connection between two nodes")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--graph")

    p = sub.add_parser("stats", help="size and token-savings summary")
    p.add_argument("--graph")

    p = sub.add_parser("serve", help="run as an MCP server over stdio")
    p.add_argument("--graph")

    p = sub.add_parser("init", help="one-step project setup: build graph + tell your assistant to use it")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--for", dest="platform", default="claude",
                   choices=["claude", "codex", "agents", "cursor", "gemini", "copilot"])

    p = sub.add_parser("install", help="install the /kgraph skill for Claude Code (~/.claude/skills)")
    p = sub.add_parser("hook", help="add/remove 'use the graph' instructions in this project")
    p.add_argument("platform", choices=["claude", "codex", "agents", "cursor", "gemini", "copilot"])
    p.add_argument("action", nargs="?", default="install", choices=["install", "uninstall"])

    a = ap.parse_args(argv)

    if a.cmd == "build":
        from .builder import build
        print(f"kgraph: building graph for {os.path.abspath(a.path)}")
        g = build(a.path, a.out, llm=a.llm, html=not a.no_html)
        m = g.meta
        out = a.out or os.path.join(os.path.abspath(a.path), "graph-out")
        ratio = m["corpus_tokens"] / max(1, m["report_tokens"])
        print(f"  {len(g.nodes)} nodes, {len(g.edges)} edges, "
              f"{len({n.community for n in g.nodes.values()})} communities in {m['seconds']}s")
        print(f"  report ≈ {m['report_tokens']:,} tokens vs raw ≈ {m['corpus_tokens']:,} ({ratio:.0f}x smaller)")
        print(f"  wrote {out}/GRAPH_REPORT.md, graph.json" + ("" if a.no_html else ", graph.html"))
        return

    if a.cmd in ("query", "explain", "path", "stats", "serve"):
        gp = find_graph(a.graph)
        if a.cmd == "serve":
            from .mcp_server import serve
            serve(gp)
            return
        from .model import Graph
        g = Graph.load(gp)
        if a.cmd == "query":
            from .query import query
            print(query(g, " ".join(a.text), budget=a.budget, depth=a.depth))
        elif a.cmd == "explain":
            from .query import explain
            print(explain(g, " ".join(a.text)))
        elif a.cmd == "path":
            from .query import path
            print(path(g, a.a, a.b))
        else:
            from collections import Counter
            m = g.meta
            print(f"root: {m.get('root')}  built: {m.get('built')}")
            print(f"files {m.get('files')} · nodes {len(g.nodes)} · edges {len(g.edges)}")
            print("kinds: " + ", ".join(f"{k} {v}" for k, v in Counter(n.kind for n in g.nodes.values()).most_common()))
            print(f"raw corpus ≈ {m.get('corpus_tokens', 0):,} tokens · report ≈ {m.get('report_tokens', 0):,} tokens")
        return

    if a.cmd == "init":
        import subprocess
        from .builder import build
        from .install import install_project, install_skill
        root = os.path.abspath(a.path)
        print(f"kgraph: setting up {root}")
        g = build(root)
        print(f"  graph: {len(g.nodes)} nodes, report ≈ {g.meta['report_tokens']:,} tokens "
              f"(raw files ≈ {g.meta['corpus_tokens']:,})")
        print(f"  instructions: {install_project(a.platform, root)}")
        if a.platform == "claude":
            print(f"  skill: {install_skill()}")
        gi = os.path.join(root, ".gitignore")
        if os.path.isdir(os.path.join(root, ".git")):
            text = open(gi, encoding="utf-8").read() if os.path.exists(gi) else ""
            if "graph-out" not in text:
                with open(gi, "a", encoding="utf-8") as f:
                    f.write(("" if not text or text.endswith("\n") else "\n") + "graph-out/\n")
                print("  added graph-out/ to .gitignore")
        print("Done. Open graph-out/graph.html to look around, or just ask your assistant questions.")
        print("Optional MCP tools for Claude Code:  claude mcp add kgraph -- python -m kgraph serve")
        return

    if a.cmd == "install":
        from .install import install_skill
        p = install_skill()
        print(f"skill installed -> {p}\nIn Claude Code type: /kgraph .")
        print("Optional: `python -m kgraph hook claude` in a project so Claude always checks the graph first,")
        print("          `claude mcp add kgraph -- python -m kgraph serve` to expose it as MCP tools.")
        return

    if a.cmd == "hook":
        from .install import install_project, remove_section, TARGETS
        if a.action == "install":
            print(f"updated {install_project(a.platform)}")
        else:
            print("removed" if remove_section(TARGETS[a.platform]) else "nothing to remove")


if __name__ == "__main__":
    main()
