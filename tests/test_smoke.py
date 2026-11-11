"""End-to-end smoke test: python -m pytest tests/  (or just: python tests/test_smoke.py)"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kgraph.builder import build
from kgraph.query import query, explain, path

FILES = {
    "app/auth.py": '''
        """Authentication helpers."""
        from app.tokens import decode_token

        class Authenticator:
            """Checks credentials."""
            def verify(self, raw):
                return decode_token(raw)
    ''',
    "app/tokens.py": '''
        def decode_token(raw):
            """Decode a signed JWT token."""
            return raw
    ''',
    "web/client.ts": '''
        import { helper } from './util';
        export class Api extends Base {
          async #send(req: Request): Promise<Response> {
            return helper(req);
          }
        }
    ''',
    "web/util.ts": '''
        export function helper(x) { return x; }
    ''',
    "docs/design.md": '''
        # Design
        ## Token verification
        The `Authenticator` calls `decode_token` to verify each token. See [notes](notes.md).
    ''',
    "data/customers.csv": '''
        customer_id,plan,monthly_spend
        1,Basic,19
        2,Pro,49
    ''',
    "data/tickets.csv": '''
        ticket_id,customer_id,category
        10,1,billing
        11,2,bug
    ''',
    "docs/notes.md": '''
        # Notes
        ## Token verification ideas
        Token verification could cache decoded tokens.
    ''',
}


def make_repo(d):
    for rel, body in FILES.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(textwrap.dedent(body).lstrip())


def test_end_to_end():
    with tempfile.TemporaryDirectory() as d:
        make_repo(d)
        g = build(d, log=lambda *a: None)
        ids = set(g.nodes)
        assert "app/auth.py::Authenticator.verify" in ids
        assert "web/client.ts::Api.#send" in ids
        edges = {(e.src, e.dst, e.rel) for e in g.edges}
        assert ("file:app/auth.py", "app/tokens.py::decode_token", "imports") in edges
        assert ("app/auth.py::Authenticator.verify", "app/tokens.py::decode_token", "calls") in edges
        assert ("file:web/client.ts", "file:web/util.ts", "imports") in edges
        assert ("docs/design.md#token-verification", "file:docs/notes.md", "links_to") in edges
        assert ("docs/design.md#token-verification", "app/auth.py::Authenticator", "mentions") in edges
        assert "concept:token verification" in ids
        assert any(e.rel == "shares_column:customer_id" for e in g.edges)
        assert os.path.exists(os.path.join(d, "graph-out", "text", "data", "customers.csv.txt"))

        q = query(g, "decode jwt token", budget=400)
        assert "decode_token" in q and "Read next" in q
        assert "Authenticator" in explain(g, "Authenticator")
        assert "hops" in path(g, "Notes", "decode_token")

        out = os.path.join(d, "graph-out")
        for f in ("GRAPH_REPORT.md", "graph.json", "graph.html"):
            assert os.path.exists(os.path.join(out, f)), f

        # incremental: second build hits the cache for every file
        logs = []
        build(d, log=logs.append)
        assert any(f"({len(FILES)} from cache)" in l for l in logs)

        # MCP round trip
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "kgraph_query", "arguments": {"question": "decode token"}}},
        ]
        r = subprocess.run([sys.executable, "-m", "kgraph.cli", "serve", "--graph", os.path.join(out, "graph.json")],
                           input="\n".join(json.dumps(m) for m in msgs) + "\n", capture_output=True, text=True,
                           cwd=os.path.join(os.path.dirname(__file__), ".."))
        lines = [json.loads(l) for l in r.stdout.splitlines()]
        assert lines[0]["result"]["serverInfo"]["name"] == "kgraph"
        assert "decode_token" in lines[1]["result"]["content"][0]["text"]


if __name__ == "__main__":
    test_end_to_end()
    print("ok")
