"""Optional LLM pass (--llm): extract entities + relations from doc sections.

Providers (stdlib HTTP only, results cached by content hash so you pay once):
  anthropic  ANTHROPIC_API_KEY            model: KGRAPH_MODEL (default claude-haiku-5-5)
  openai     OPENAI_API_KEY, OPENAI_BASE_URL (any OpenAI-compatible server: LM Studio, llama.cpp, vLLM, Groq...)
  ollama     OLLAMA_HOST (default http://localhost:11434)  model default llama3.1
Code is never sent: only doc / paper sections.
"""
import hashlib
import json
import os
import re
import urllib.request

from .extract import SIDECAR_EXT
from .model import Node, INFERRED

PROMPT = """Extract a small knowledge graph from the text below.
Return ONLY JSON: {"entities":[{"name":str,"type":str}], "relations":[[subject, relation, object]]}
- 3-10 entities: key concepts, methods, systems, datasets, people, components. Short canonical names.
- relations use snake_case verbs (uses, extends, depends_on, improves, part_of, evaluates_on, contrasts_with).
- Only relations stated or clearly implied by the text.

TEXT:
"""

DEFAULT_MODEL = {"anthropic": "claude-haiku-5-5", "openai": "gpt-4o-mini", "ollama": "llama3.1"}


def _post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def call(provider, prompt, model=None):
    model = model or os.environ.get("KGRAPH_MODEL") or DEFAULT_MODEL[provider]
    if provider == "anthropic":
        r = _post("https://api.anthropic.com/v1/messages",
                  {"model": model, "max_tokens": 800, "messages": [{"role": "user", "content": prompt}]},
                  {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
        return "".join(b.get("text", "") for b in r["content"])
    if provider == "openai":
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        r = _post(f"{base}/chat/completions",
                  {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0},
                  {"authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', 'local')}"})
        return r["choices"][0]["message"]["content"]
    if provider == "ollama":
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        r = _post(f"{host}/api/chat", {"model": model, "stream": False, "format": "json",
                                       "messages": [{"role": "user", "content": prompt}]}, {})
        return r["message"]["content"]
    raise ValueError(f"unknown provider {provider}")


def _parse(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def enrich(g, root, out_dir, provider, log=print, max_sections=400):
    cache = os.path.join(out_dir, "cache", "llm")
    os.makedirs(cache, exist_ok=True)
    def has_sections(n):
        return any(r == "contains" and d == "out" for _, r, d, _ in g.adj.get(n.id, ()))
    secs = [n for n in g.nodes.values() if n.file and n.line and
            (n.kind == "section" or (n.kind == "doc" and not has_sections(n)))][:max_sections]
    file_cache, done, calls = {}, 0, 0
    for n in secs:
        if n.file.lower().endswith((".xlsx", ".xlsm", ".csv", ".tsv")):
            continue  # tables are already summarised column by column
        if n.file not in file_cache:
            src = os.path.join(root, n.file)
            if n.file.lower().endswith(SIDECAR_EXT):
                src = os.path.join(out_dir, "text", n.file + ".txt")
            try:
                with open(src, encoding="utf-8", errors="replace") as f:
                    file_cache[n.file] = f.read().splitlines()
            except OSError:
                continue
        lines = file_cache[n.file]
        text = "\n".join(lines[n.line - 1:(n.end_line or n.line + 80)])[:6000]
        if len(text.strip()) < 200:
            continue
        key = hashlib.sha256((provider + text).encode()).hexdigest()
        cp = os.path.join(cache, key + ".json")
        if os.path.exists(cp):
            with open(cp) as f:
                data = json.load(f)
        else:
            try:
                data = _parse(call(provider, PROMPT + text)) or {}
                calls += 1
            except Exception as e:
                log(f"  ! llm failed on {n.id}: {e}")
                continue
            with open(cp, "w") as f:
                json.dump(data, f)
        names = {}
        for ent in (data.get("entities") or [])[:12]:
            nm = str(ent.get("name", "")).strip()
            if not nm or len(nm) > 80:
                continue
            eid = "concept:" + nm.lower()
            g.add_node(Node(eid, nm.lower(), "concept", summary=str(ent.get("type", ""))))
            g.add_edge(n.id, eid, "mentions", INFERRED)
            names[nm.lower()] = eid
        for rel in (data.get("relations") or [])[:20]:
            if isinstance(rel, list) and len(rel) == 3:
                a, r, b = (str(x).strip().lower() for x in rel)
                ia = names.get(a) or ("concept:" + a if "concept:" + a in g.nodes else None)
                ib = names.get(b) or ("concept:" + b if "concept:" + b in g.nodes else None)
                if ia and ib:
                    g.add_edge(ia, ib, re.sub(r"\W+", "_", r)[:30] or "related_to", INFERRED)
        done += 1
    log(f"  llm: enriched {done} sections ({calls} new calls, rest cached)")
