import os, re, subprocess, sys
from kgraph.model import Graph
from kgraph.query import query
from kgraph.textutil import est_tokens
Q = {
 "httpx": [("how are timeouts configured", ["timeout"]), ("where is digest auth implemented", ["digest"]),
           ("how do proxies get mounted per url", ["proxy", "mount"])],
 "gin":   [("how does the router tree add a route", ["addRoute"]), ("how is JSON rendered in a response", ["JSON", "render"]),
           ("binding form data to a struct", ["bind", "form"])],
 "ky":    [("retry logic with backoff delay", ["retry"]), ("how are hooks run before a request", ["beforeRequest"]),
           ("timeout handling", ["timeout"])],
}
root = "/home/claude/test"
print(f"{'repo':6} {'question':42} {'grep+read files':>15} {'kgraph':>8} {'saving':>7}")
for repo, qs in Q.items():
    g = Graph.load(f"{root}/{repo}/graph-out/graph.json")
    files = [n.file for n in g.nodes.values() if n.kind in ("file","doc")]
    for q, kws in qs:
        # baseline: agent greps keywords and reads every matching file in full
        base = 0
        for f in files:
            try: t = open(os.path.join(root, repo, f), encoding="utf-8", errors="ignore").read()
            except OSError: continue
            if all(re.search(k, t, re.I) for k in kws): base += est_tokens(t)
        out = query(g, q, budget=1500)
        cost = est_tokens(out)
        sec = out.split("## Read next (only these ranges)\n")
        if len(sec) > 1:
            for line in sec[1].splitlines():
                f, rs = line.split(": ", 1)
                path = os.path.join(root, repo, f) if not f.startswith("graph-out") else os.path.join(root, repo, f)
                lines = open(path, encoding="utf-8", errors="ignore").read().splitlines()
                for r in rs.split(", "):
                    a, b = map(int, r.split("-"))
                    cost += est_tokens("\n".join(lines[a-1:b]))
        print(f"{repo:6} {q[:42]:42} {base:>15,} {cost:>8,} {base/max(cost,1):>6.1f}x")
