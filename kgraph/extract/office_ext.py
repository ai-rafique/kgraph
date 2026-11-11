"""Office and tabular files: .docx, .pptx, .xlsx/.xlsm, .csv/.tsv.

Standard library only (zipfile + xml), so it works where pip packages are blocked.
Each file is converted to markdown text, which then goes through the normal
docs extractor (sections, summaries, concepts, links to code). That text is also
saved to graph-out/text/<file>.txt so the assistant can read exact line ranges.

Spreadsheets and CSVs are summarised, not copied: row/column counts, column
names and types, value ranges and a few sample rows per sheet.
"""
import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"

SAMPLE_ROWS = 5        # rows shown per table
PROFILE_ROWS = 500     # rows used to infer column types


def to_markdown(rel, data: bytes, lang):
    """Return (markdown_text, raw_chars) or raise."""
    stem = rel.rsplit("/", 1)[-1]
    if lang == "docx":
        return _docx(data, stem)
    if lang == "pptx":
        return _pptx(data, stem)
    if lang == "xlsx":
        return _xlsx(data, stem)
    if lang in ("csv", "tsv"):
        return _csv(data, stem, lang)
    raise ValueError(lang)


# ----------------------------------------------------------------------------- docx
def _docx(data, stem):
    z = zipfile.ZipFile(io.BytesIO(data))
    body = ET.fromstring(z.read("word/document.xml")).find(W + "body")
    out = []
    raw = 0

    def para(p):
        nonlocal raw
        text = "".join(t.text or "" for t in p.iter(W + "t")).strip()
        if not text:
            return
        raw += len(text)
        ppr = p.find(W + "pPr")
        level = None
        if ppr is not None:
            st = ppr.find(W + "pStyle")
            sid = (st.get(W + "val") if st is not None else "") or ""
            m = re.search(r"(?i)(?:heading|berschrift|titre|titulo|kop)\s*(\d)", sid)
            if m:
                level = int(m.group(1))
            elif sid.lower() in ("title",):
                level = 0
            ol = ppr.find(W + "outlineLvl")
            if level is None and ol is not None:
                level = int(ol.get(W + "val", "9"))
                level = level if level < 6 else None
            if level is None and ppr.find(W + "numPr") is not None:
                out.append(f"- {text}")
                return
        if level is not None:
            out.append("")
            out.append("#" * min(6, level + 2) + " " + text)   # Title -> ##, Heading 1 -> ###
            out.append("")
        else:
            out.append(text)
            out.append("")

    def walk(el):
        nonlocal raw
        for ch in el:
            if ch.tag == W + "p":
                para(ch)
            elif ch.tag == W + "tbl":
                rows = []
                for tr in ch.iter(W + "tr"):
                    cells = [" ".join("".join(t.text or "" for t in tc.iter(W + "t")).split())
                             for tc in tr.findall(W + "tc")]
                    rows.append(cells)
                if rows:
                    out.extend(_md_table(rows[0], rows[1:1 + 30]))
                    if len(rows) > 31:
                        out.append(f"(+{len(rows) - 31} more rows)")
                    out.append("")
                    raw += sum(len(c) for r in rows for c in r)
            elif ch.tag == W + "sdt":
                c = ch.find(W + "sdtContent")
                if c is not None:
                    walk(c)
    walk(body)
    return "\n".join(out), raw


# ----------------------------------------------------------------------------- pptx
def _rels(z, path):
    try:
        root = ET.fromstring(z.read(path))
    except KeyError:
        return {}
    return {r.get("Id"): r.get("Target") for r in root.iter(PR + "Relationship")}


def _pptx(data, stem):
    z = zipfile.ZipFile(io.BytesIO(data))
    slides = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                    key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
    out = []
    raw = 0
    for i, sp in enumerate(slides, 1):
        root = ET.fromstring(z.read(sp))
        title, lines = "", []
        for shape in root.iter(P + "sp"):
            ph = shape.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
            kind = ph.get("type", "body") if ph is not None else "body"
            if kind in ("sldNum", "dt", "ftr"):
                continue
            paras = []
            for p in shape.iter(A + "p"):
                t = "".join(r.text or "" for r in p.iter(A + "t")).strip()
                if t:
                    lvl = p.find(A + "pPr")
                    lvl = int(lvl.get("lvl", "0")) if lvl is not None else 0
                    paras.append(("  " * lvl) + t)
            if not paras:
                continue
            if kind in ("title", "ctrTitle") and not title:
                title = " ".join(x.strip() for x in paras)
            else:
                lines.extend(paras)
        for tbl in root.iter(A + "tbl"):
            rows = [["".join(t.text or "" for t in tc.iter(A + "t")).strip() for tc in tr.iter(A + "tc")]
                    for tr in tbl.iter(A + "tr")]
            if rows:
                lines.extend(_md_table(rows[0], rows[1:16]))
        # speaker notes
        notes = []
        rels = _rels(z, sp.replace("slides/", "slides/_rels/") + ".rels")
        for tgt in rels.values():
            if tgt and "notesSlide" in tgt:
                npath = "ppt/" + tgt.replace("../", "")
                try:
                    nroot = ET.fromstring(z.read(npath))
                except KeyError:
                    continue
                for shape in nroot.iter(P + "sp"):
                    ph = shape.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
                    if ph is not None and ph.get("type") == "body":
                        for p in shape.iter(A + "p"):
                            t = "".join(r.text or "" for r in p.iter(A + "t")).strip()
                            if t:
                                notes.append(t)
        out.append(f"## Slide {i}: {title or (lines[0][:60] if lines else 'untitled')}")
        out.extend(f"- {l}" if not l.startswith(("|", " ")) else l for l in lines)
        if notes:
            out.append("")
            out.append("Speaker notes: " + " ".join(notes))
        out.append("")
        raw += len(title) + sum(len(l) for l in lines) + sum(len(n) for n in notes)
    return "\n".join(out), raw


# ----------------------------------------------------------------------------- xlsx
def _col_index(ref):
    n = 0
    for ch in ref:
        if ch.isalpha():
            n = n * 26 + (ord(ch.upper()) - 64)
        else:
            break
    return n - 1


def _xlsx(data, stem):
    z = zipfile.ZipFile(io.BytesIO(data))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(S + "si"):
            shared.append("".join(t.text or "" for t in si.iter(S + "t")))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = _rels(z, "xl/_rels/workbook.xml.rels")
    out = []
    raw = 0
    for sh in wb.iter(S + "sheet"):
        name = sh.get("name")
        tgt = rels.get(sh.get(R + "id"), "")
        path = tgt.lstrip("/") if tgt.startswith("/") else "xl/" + tgt
        if path not in z.namelist():
            continue
        rows, total, ncols = [], 0, 0
        with z.open(path) as f:
            for ev, el in ET.iterparse(f):
                if el.tag != S + "row":
                    continue
                cells = {}
                for c in el.iter(S + "c"):
                    t = c.get("t")
                    v = c.find(S + "v")
                    if t == "s" and v is not None:
                        val = shared[int(v.text)] if v.text and int(v.text) < len(shared) else ""
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(S + "t"))
                    else:
                        val = v.text if v is not None and v.text is not None else ""
                    if val != "":
                        cells[_col_index(c.get("r", "A"))] = val
                        raw += len(str(val))
                if cells:
                    total += 1
                    ncols = max(ncols, max(cells) + 1)
                    if len(rows) < PROFILE_ROWS + 1:
                        rows.append(cells)
                el.clear()
        rows = [[r.get(i, "") for i in range(ncols)] for r in rows]
        out.extend(_describe_table(f"Sheet: {name}", rows, total))
    return "\n".join(out), raw


# ----------------------------------------------------------------------------- csv
def _csv(data, stem, lang):
    text = data.decode("utf-8-sig", errors="replace")
    delim = "\t" if lang == "tsv" else ","
    if lang == "csv":
        try:
            delim = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|").delimiter
        except csv.Error:
            pass
    rows, total = [], 0
    for r in csv.reader(io.StringIO(text), delimiter=delim):
        if not any(c.strip() for c in r):
            continue
        total += 1
        if len(rows) < PROFILE_ROWS + 1:
            rows.append(r)
    width = max((len(r) for r in rows), default=0)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = [] + _describe_table("Table", rows, total)
    return "\n".join(out), len(text)


# ----------------------------------------------------------------------------- shared
def _kind(vals):
    vals = [v.strip() for v in vals if str(v).strip()]
    if not vals:
        return "empty", ""
    nums = []
    for v in vals:
        try:
            nums.append(float(v.replace(",", "")) if re.fullmatch(r"-?[\d,]*\.?\d+(e-?\d+)?", v, re.I) else None)
        except ValueError:
            nums.append(None)
    if all(n is not None for n in nums):
        lo, hi = min(nums), max(nums)
        fmt = (lambda x: f"{x:,.0f}") if all(float(n).is_integer() for n in nums) else (lambda x: f"{x:,.2f}")
        return "number", f"{fmt(lo)} to {fmt(hi)}"
    dates = 0
    for v in vals[:50]:
        for f in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                datetime.strptime(v[:19], f)
                dates += 1
                break
            except ValueError:
                pass
    if dates >= 0.8 * min(50, len(vals)):
        s = sorted(vals)
        return "date", f"{s[0]} to {s[-1]}"
    distinct = list(dict.fromkeys(vals))
    if len(distinct) <= 12:
        return "category", ", ".join(distinct[:8]) + (" …" if len(distinct) > 8 else "")
    return "text", "e.g. " + ", ".join(d[:30] for d in distinct[:3])


def _looks_like_header(row, rest):
    if not row or not any(row):
        return False
    numeric = sum(1 for c in row if re.fullmatch(r"-?[\d,.]+", str(c).strip() or "x"))
    return numeric == 0 and len(set(row)) == len(row)


def _describe_table(title, rows, total):
    out = [f"## {title}"]
    if not rows:
        out += ["Empty.", ""]
        return out
    if _looks_like_header(rows[0], rows[1:]):
        header, body = [str(c).strip() or f"col{i + 1}" for i, c in enumerate(rows[0])], rows[1:]
        n_data = total - 1
    else:
        header, body = [f"col{i + 1}" for i in range(len(rows[0]))], rows
        n_data = total
    out.append(f"{n_data:,} rows × {len(header)} columns: " + ", ".join(header[:40]) +
               (" …" if len(header) > 40 else "") + ".")
    out.append("")
    out.append(f"Columns (types from the first {min(len(body), PROFILE_ROWS):,} rows):")
    for i, h in enumerate(header[:60]):
        kind, info = _kind([r[i] for r in body if i < len(r)])
        out.append(f"- `{h}`: {kind}" + (f" ({info})" if info else ""))
    if len(header) > 60:
        out.append(f"- … {len(header) - 60} more columns")
    if body:
        out.append("")
        out.append("Sample rows:")
        out.extend(_md_table(header[:12], [r[:12] for r in body[:SAMPLE_ROWS]]))
    out.append("")
    return out


def _md_table(header, rows):
    def cell(x):
        x = str(x).replace("|", "/").replace("\n", " ").strip()
        return x[:40]
    width = len(header)
    lines = ["| " + " | ".join(cell(h) for h in header) + " |", "|" + "---|" * width]
    for r in rows:
        r = list(r) + [""] * (width - len(r))
        lines.append("| " + " | ".join(cell(c) for c in r[:width]) + " |")
    return lines
