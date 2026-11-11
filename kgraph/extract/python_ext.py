"""Python extractor using the standard-library AST (exact, no guessing)."""
import ast

from ..model import EXTRACTED


def _first_line(doc):
    if not doc:
        return ""
    line = doc.strip().splitlines()[0].strip()
    return line[:160]


def _call_name(node):
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _base_name(b):
    if isinstance(b, ast.Name):
        return b.id
    if isinstance(b, ast.Attribute):
        return b.attr
    if isinstance(b, ast.Subscript):
        return _base_name(b.value)
    return None


def extract(rel: str, text: str):
    out = {"nodes": [], "edges": [], "pending": [], "text": {}}
    fid = f"file:{rel}"
    try:
        tree = ast.parse(text)
    except SyntaxError:
        out["nodes"].append({"id": fid, "label": rel, "kind": "file", "file": rel,
                             "summary": "(python, failed to parse)"})
        return out

    out["nodes"].append({"id": fid, "label": rel, "kind": "file", "file": rel, "line": 1,
                         "end_line": len(text.splitlines()),
                         "summary": _first_line(ast.get_docstring(tree))})

    def add_sym(nid, label, kind, node, parent):
        out["nodes"].append({"id": nid, "label": label, "kind": kind, "file": rel,
                             "line": node.lineno, "end_line": getattr(node, "end_lineno", None),
                             "summary": _first_line(ast.get_docstring(node))})
        out["edges"].append([parent, nid, "defines", EXTRACTED])

    def collect_calls(owner_id, body_node):
        seen = set()
        for sub in ast.walk(body_node):
            if isinstance(sub, ast.Call):
                name = _call_name(sub)
                if name and name not in seen and not name.startswith("__"):
                    seen.add(name)
                    out["pending"].append({"src": owner_id, "rel": "calls", "target": name,
                                           "how": "name", "file": rel})

    # imports (anywhere in the module)
    pkg_parts = rel[:-3].replace("\\", "/").split("/")
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                out["pending"].append({"src": fid, "rel": "imports", "target": a.name,
                                       "how": "pymodule", "file": rel})
        elif isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            if n.level:  # relative import -> make absolute-ish from file location
                base = pkg_parts[:-n.level] if n.level <= len(pkg_parts) else []
                mod = ".".join(base + ([mod] if mod else []))
            names = [a.name for a in n.names if a.name != "*"]
            out["pending"].append({"src": fid, "rel": "imports", "target": mod,
                                   "how": "pymodule", "names": names, "file": rel})

    # top-level definitions
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nid = f"{rel}::{node.name}"
            add_sym(nid, node.name, "function", node, fid)
            collect_calls(nid, node)
        elif isinstance(node, ast.ClassDef):
            cid = f"{rel}::{node.name}"
            add_sym(cid, node.name, "class", node, fid)
            for b in node.bases:
                bn = _base_name(b)
                if bn and bn not in ("object",):
                    out["pending"].append({"src": cid, "rel": "inherits", "target": bn,
                                           "how": "name", "file": rel})
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    mid = f"{rel}::{node.name}.{item.name}"
                    add_sym(mid, f"{node.name}.{item.name}", "method", item, cid)
                    collect_calls(mid, item)
    # module-level calls (scripts)
    for node in tree.body:
        if isinstance(node, (ast.Expr, ast.Assign, ast.If)):
            collect_calls(fid, node)
    return out
