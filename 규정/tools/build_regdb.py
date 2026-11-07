#!/usr/bin/env python3
"""원문/규정/ 의 PDF 규정 → 규정/ 구조화 DB.

  python3 규정/tools/build_regdb.py            # 전체 다시 만들기 (data/ · regulations/ · markdown/ · docs/SOURCE_INDEX.md)

입력:  원문/규정/**.pdf  (원본은 읽기만 한다)
설정:  규정/config/registry.json (규정 ID·약칭·계위) · 규정/config/keyword-dictionary.json (키워드 사전)
출력:  규정/data/*.json · 규정/regulations/<규정>/{metadata,articles,annexes}.json,regulation.md · 규정/markdown/*.md
필요:  python3, PyMuPDF(pymupdf), poppler-utils(pdftotext). 자세한 내용은 규정/README.md
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pymupdf  # noqa: E402
from parse_regulation import HAN_NO, PARA_NO, Parser, TAG_RE  # noqa: E402
from pdf_lines import Spacer, clean_main, dedupe_chars, join, nfc, page_lines, page_lines_dedup  # noqa: E402
import parse_outline  # noqa: E402
import parse_opinion  # noqa: E402
from refs import Resolver, ns  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "규정")
SCHEMA_VERSION = "1.0"
TODAY = os.environ.get("REGDB_DATE") or datetime.date.today().isoformat()
HEADER_RE = re.compile(r"\[시행\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?\]\s*\[\s*([^\[\]]*?)\s*제\s*([\d\-]+)\s*호\s*,\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?\s*,?\s*([^\]]*)\]")
NAME_RE = re.compile(r"^(.*?)\s*\(\s*약칭\s*:\s*(.*?)\s*\)\s*$")
FN_RE = re.compile(r"\(제?\s*([\d\-]+)\s*호?\)\s*\((\d{8})\)\.pdf$")
ANNEX_FN = re.compile(r"^\[(별표|별지|별첨)\s*(?:제\s*)?(?:(\d+)\s*(?:호\s*)?(?:의\s*(\d+))?)?\s*(?:호\s*)?(?:서식)?\s*\]\s*(.*)\.pdf$")
KIND_ABBR = {"별표": "ANNEX", "별지": "FORM", "별첨": "ATTACH"}


def d2s(y, m, d):
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def jdump(path, obj, indent=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent, separators=(",", ":") if indent is None else None)
        f.write("\n")


def find_src():
    if os.environ.get("REGDB_SRC"):                       # 시험용: 다른 원본 폴더
        return os.environ["REGDB_SRC"]
    base = os.path.join(ROOT, "원문")
    for x in os.listdir(base):
        if nfc(x) == "규정":
            return os.path.join(base, x)
    sys.exit("원문/규정/ 폴더를 찾지 못했어요")


def scan(src):
    """[(abs_path, nfc_rel_path)] — 하위 폴더 포함 모든 PDF. 다른 형식 파일은 skipped 로 보고"""
    pdfs, skipped, seen = [], [], set()
    for r, _d, fs in os.walk(src):
        for f in sorted(fs):
            p = os.path.join(r, f)
            rel = nfc(os.path.relpath(p, src))
            if rel in seen:                 # 같은 파일을 한글 자모 풀어쓰기(NFD) 정도만 달리해 두 번 올린 경우: 하나만 쓴다
                continue
            seen.add(rel)
            (pdfs if f.lower().endswith(".pdf") else skipped).append((p, rel))
    # 같은 폴더 안에서 내용이 똑같은 PDF 가 이름만 다르게(예: 긴 이름이 '…' 로 잘린 사본) 또 있으면 이름이 온전한 쪽만 쓴다
    groups = {}
    for p_, rel_ in pdfs:
        with open(p_, "rb") as fh:
            h_ = hashlib.sha1(fh.read()).hexdigest()
        groups.setdefault((os.path.dirname(rel_), h_), []).append((p_, rel_))
    keep = set()
    for g in groups.values():
        g.sort(key=lambda x: ("…" in os.path.basename(x[1]), -len(x[1])))      # 잘린 이름('…')은 뒤로, 그다음엔 긴 이름 먼저
        keep.add(g[0][1])
    pdfs = [x for x in pdfs if x[1] in keep]
    pdfs.sort(key=lambda x: x[1])
    return pdfs, skipped


def pdftotext_layout(path):
    out = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True, check=True).stdout
    pages = out.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return pages


BULLET_START = re.compile(r"^(?:[ㆍ·•▪▫■□○●◦※\-–*]|\d+[.)]|[가-하][.)]|\(\d+\)|[①-⑳])")


def cell_text(raw, spacer):
    """표 칸 안의 줄들을 이음 — 목록 표지(ㆍ, 1., 가. …)로 시작하는 줄은 줄바꿈, 나머지는 어절 통계로 공백 여부를 정함"""
    lines = [x.strip() for x in (raw or "").split("\n") if x.strip()]
    out = ""
    for ln in lines:
        if not out:
            out = ln
        elif BULLET_START.match(ln):
            out += "\n" + ln
        else:
            out += spacer.decide(out.split("\n")[-1], ln, 1.2) + ln
    return out


def extract_annex(path, spacer, pages=None, dedupe=False):
    """부속 PDF → (blocks, plain_text, pages)
    blocks: [{"type":"text","page":n,"text":…} | {"type":"table","page":n,"rows":[[칸…]…]}] — 쪽·위쪽부터 읽는 순서.
    표는 PyMuPDF 표 인식, 표 밖 글은 줄 단위. 둘 다 원문 글자를 그대로 옮긴다(칸 안 줄 이음만 복원)."""
    doc = pymupdf.open(path)
    blocks = []
    for pno, page in enumerate(doc, 1):
        if pages and not (pages[0] <= pno <= pages[1]):
            continue
        try:
            tabs = page.find_tables().tables
        except Exception:
            tabs = []
        # 다른 표의 칸 안에 들어 있는 표(중첩 표)는 바깥 표 칸 글에 이미 들어 있으므로 따로 싣지 않는다(글 중복 방지)
        tabs = [t for i, t in enumerate(tabs)
                if not any(j != i and pymupdf.Rect(o.bbox).contains(pymupdf.Rect(t.bbox) + (1, 1, -1, -1)) and pymupdf.Rect(o.bbox).get_area() > pymupdf.Rect(t.bbox).get_area()
                           for j, o in enumerate(tabs))]
        boxes = [pymupdf.Rect(t.bbox) for t in tabs]
        items = []
        for t, bx in zip(tabs, boxes):
            rows = [[cell_text(c, spacer) for c in r] for r in t.extract()]
            rows = [r for r in rows if any(c for c in r)]
            if rows:
                items.append((bx.y0, bx.x0, {"type": "table", "page": pno, "rows": rows}))
        # 표 밖 글자: 글자 단위로 모아 같은 줄(세로 위치)끼리 이은 뒤, 글자 사이가 벌어진 곳은 공백으로 (PDF 가 낱말마다 따로 배치해 공백 글자가 없는 경우가 있음)
        chars = []
        for b in page.get_text("rawdict")["blocks"]:
            if b.get("type") != 0:
                continue
            for ln in b["lines"]:
                for sp in ln["spans"]:
                    for ch in sp["chars"]:
                        c = ch["c"]
                        x0, y0, x1, y1 = ch["bbox"]
                        if any(bx.contains(pymupdf.Point((x0 + x1) / 2, (y0 + y1) / 2)) for bx in boxes):
                            continue
                        chars.append((((y0 + y1) / 2), x0, x1, c, sp["size"]))
        if dedupe:
            chars = dedupe_chars(chars, 3, 1, 0)
        chars.sort(key=lambda c: c[0])
        rows_, cur = [], []
        for c in chars:
            if cur and abs(c[0] - cur[-1][0]) > 3.0:
                rows_.append(cur); cur = []
            cur.append(c)
        if cur:
            rows_.append(cur)
        text_lines = []
        for r in rows_:
            r.sort(key=lambda c: c[1])
            t, last = "", None
            for c in r:
                if last is not None and c[3] != " " and last[3] != " " and c[1] - last[2] > 0.18 * max(c[4], 1):
                    t += " "
                t += c[3]
                last = c
            t = re.sub(r"[ \t]+", " ", t).strip()
            if t:
                text_lines.append((sum(c[0] for c in r) / len(r), r[0][1], t))
        # 표와 표 사이의 글 덩어리: 세로로 이어진 줄을 한 항목으로 (표가 사이에 끼면 나눔)
        tb_ys = sorted((bx.y0, bx.y1) for bx in boxes)
        group, prev_y = [], None
        def flush():
            nonlocal group
            if group:
                items.append((group[0][0], group[0][1], {"type": "text", "page": pno, "text": "\n".join(g[2] for g in group)}))
            group = []
        for y, x, t in text_lines:
            if group and any(prev_y < y0 < y or prev_y < y1 < y or (y0 <= prev_y and y1 >= y) for y0, y1 in tb_ys):
                flush()
            group.append((y, x, t))
            prev_y = y
        flush()
        items.sort(key=lambda x: (round(x[0] / 4), x[1]))
        blocks.extend(i[2] for i in items)
    n = len(doc) if not pages else (pages[1] - pages[0] + 1)
    doc.close()
    for b in blocks:                                  # 기호 글꼴로 그린 점선(\uf000 연속)은 장식이라 뺀다
        if b["type"] == "text":
            b["text"] = re.sub("\uf000{6,}", "", b["text"])
        else:
            b["rows"] = [[re.sub("\uf000{6,}", "", c) for c in r] for r in b["rows"]]
    blocks = [b for b in blocks if (b["text"].strip() if b["type"] == "text" else any(c for r in b["rows"] for c in r))]
    plain = []
    for b in blocks:
        if b["type"] == "text":
            plain.append(b["text"])
        else:
            plain.append("\n".join(" | ".join(c.replace("\n", " / ") for c in r) for r in b["rows"]))
    return blocks, "\n\n".join(plain), n


def clean_layout(page):
    lines = [re.sub(r"[ \t]+$", "", ln) for ln in page.split("\n")]
    s = "\n".join(lines).strip("\n")
    s = re.sub(r"\n{3,}", "\n\n", s)
    # 공통 들여쓰기 제거
    ls = s.split("\n")
    ind = min((len(l) - len(l.lstrip()) for l in ls if l.strip()), default=0)
    return "\n".join(l[ind:] for l in ls)


# ── 머리말 ──
def parse_header(front):
    h = {"name": "", "abbr": "", "effective_date": "", "revision_date": "", "form": "", "number": "", "revision_type": "", "agencies": [], "raw": list(front[:30])}
    if not front:
        return h
    # 첫 줄이 규정명이 아닐 수 있다(예: '※ 본 법령은 공포 후 시행전 개정된 사항을 모두 포함한…' 안내문 2줄이 앞에 붙는 PDF) → '(약칭: …)' 줄을 우선 찾고, 없으면 안내문이 아닌 첫 줄
    cand = next((ln for ln in front[:8] if NAME_RE.match(ln)), None)
    if cand is None:
        cand = next((ln for ln in front[:8] if not ln.startswith("※") and "연혁" not in ln and not ln.startswith("[")), front[0])
    m = NAME_RE.match(cand)
    h["name"], h["abbr"] = (m.group(1), m.group(2)) if m else (cand.strip(), "")
    for ln in front[:30]:
        if ln is cand:
            continue
        mm = HEADER_RE.search(ln)
        if mm:
            h["effective_date"] = d2s(*mm.group(1, 2, 3))
            h["form"], h["number"] = mm.group(4).strip(), mm.group(5)
            h["revision_date"] = d2s(*mm.group(6, 7, 8))
            h["revision_type"] = mm.group(9).strip().rstrip(",")
        else:
            am = re.match(r"^(\S+)\s*\(([^)]+)\)\s*[\d\-, ]+$", ln) or re.match(r"^(\S+)\s*\(([^)]+)\)", ln)
            if am and not ln.startswith("["):
                h["agencies"].append({"agency": am.group(1), "department": am.group(2)})
    return h


# ── 텍스트 도구 ──
def node_text(n):
    return n.text()


def art_plain(a):
    """조문 전체 평문(검색·관계 추출용): 항·호·목 표지를 그대로 붙인 것"""
    parts = []
    for p in a["paragraphs"]:
        parts.append(((p["paragraph_number"] + " ") if p["paragraph_number"] else "") + p["content"])
        for it in p["items"]:
            parts.append(f"{it['item_number']}. {it['content']}")
            for sb in it["subitems"]:
                parts.append(f"{sb['subitem_number']}. {sb['content']}")
    return "\n".join(x for x in parts if x.strip())


def parse_dates(s):
    out = []
    for m in re.finditer(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", s):
        out.append(d2s(*m.groups()))
    return out


def R_pdf(reg):
    """원문 PDF 의 실제 경로. 맥에서 올린 파일은 이름이 자모로 풀린 형태(NFD)일 수 있어 NFC 로 견주어 찾는다."""
    cur = find_src()
    for part in reg['source_path'][len('원문/규정/'):].split('/'):
        nxt = os.path.join(cur, part)
        if not os.path.exists(nxt):
            hit = next((e for e in os.listdir(cur) if nfc(e) == nfc(part)), None)
            nxt = os.path.join(cur, hit) if hit else nxt
        cur = nxt
    return cur


def outline_heads(res):
    """개요형 문서의 장·절·관 제목 목록 [(kind, 제목)] — 나온 순서"""
    out, seen = [], set()
    for sc in res["scopes"]:
        c, _x, sec, sub = sc.key
        if not (sc.items or sc.intro):
            continue
        cl = f"제{c}장" + (f"의{_x}" if _x else "")
        for kind, key, title in (("장", (c, _x), f"{cl} {sc.title['chapter']}"), ("절", (c, _x, sec), f"제{sec}절 {sc.title['section']}" if sec else ""), ("관", (c, _x, sec, sub), f"제{sub}관 {sc.title['subsection']}" if sub else "")):
            if title and key not in seen:
                seen.add(key)
                out.append((kind, title.strip()))
    return out


def _node_text(parts):
    return join(parts)


def fix_pua(obj, rec_id=""):
    """한글(HWP) 기호 글꼴의 사용자 영역 글자를 원문 모양(쪽 그림으로 확인)의 일반 글자로 바꾼다.
    \uf000 은 쓰임새가 여러 가지(·, □□, 『』, { }, ㉑~㉚, ↓, 점선)라 앞뒤 글자로 판별하고, 식 글꼴(HyhwpEQ)의 \ue046=−, \ue047= '=', \ue06d(분수 틀)는 뺀다."""
    if isinstance(obj, str):
        t = obj
        if "\uf000" not in t and not re.search("[\ue046\ue047\ue06d]", t):
            return t
        t = re.sub("\uf000{6,}", "", t)                                   # 점선(장식)
        t = t.replace("\ue046", "−").replace("\ue047", "=").replace("\ue06d", "")
        if "_C4_SEC3_N2" in rec_id:                                   # ㉑~㉚ 번호
            it = iter("㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚")
            t = re.sub("\uf000", lambda m: next(it, "\uf000"), t)
        t = re.sub("\uf000\uf000(?=[가-힣])", "□□", t)
        t = re.sub("(?<![가-힣])\uf000([가-힣]{2,20})\uf000", r"『\1』", t)
        t = re.sub("\uf000(?=\()", "{", t)
        t = re.sub("\uf000(?=÷)", "}", t)
        t = re.sub("(?<=[가-힣])\uf000(?=[가-힣])", "ㆍ", t)
        t = re.sub("(?<=[→)] )\uf000|^\uf000$", "↓", t)
        t = re.sub("^\uf000$", "↓", t, flags=re.M)
        return t
    if isinstance(obj, dict):
        return {k: fix_pua(v, obj.get("id", rec_id) if isinstance(obj.get("id", None), str) else rec_id) for k, v in obj.items()}
    if isinstance(obj, list):
        return [fix_pua(v, rec_id) for v in obj]
    return obj


def outline_records(reg, res):
    """parse_outline 결과 → 조문 레코드. 한 범위(장·절·관)의 '1.' 항목 = 조문, '가.' = 항, '1)' = 호, '가)' = 목."""
    rid = reg["id"]
    recs = []
    for sc in res["scopes"]:
        c, _x, sec, sub = sc.key
        if not (sc.items or sc.intro):
            continue
        cl = f"제{c}장" + (f"의{_x}" if _x else "")          # '제2장의1' 처럼 가지 장도 구분
        scope_id = f"{rid}_C{c}" + (f"S{_x}" if _x else "") + (f"_SEC{sec}" if sec else "") + (f"_SUB{sub}" if sub else "")
        scope_disp = f"{reg['display']}-{cl}" + (f"-제{sec}절" if sec else "") + (f"-제{sub}관" if sub else "")
        scope_lab = cl + (f" 제{sec}절" if sec else "") + (f" 제{sub}관" if sub else "")
        ctxf = {"part": "", "chapter": f"{cl} {sc.title['chapter']}".strip(), "section": (f"제{sec}절 {sc.title['section']}".strip() if sec else ""),
                "subsection": (f"제{sub}관 {sc.title['subsection']}".strip() if sub else "")}
        units = []
        if sc.intro:
            parts = [(t, "\n" if k == "\n" or not k else k) for t, k, _pg in sc.intro]
            units.append({"no": 0, "first": "", "paras": [], "parts": parts, "page0": sc.intro[0][2], "page1": sc.intro[-1][2]})
        units += sc.items
        for it in units:
            n = it["no"]
            aid = scope_id + (f"_N{n}" if n else "_INTRO")
            disp = scope_disp + (f"-항목{n}" if n else "-머리말")
            lab = scope_lab + (f" {n}." if n else " (머리말)")
            text0 = (_node_text([(it["first"], "")] + it["parts"]) if n else _node_text(it["parts"])).strip()
            heading_like = bool(n) and 0 < len(text0) <= 50 and "\n" not in text0 and not re.search(r"(다|함|음|임)[.]?$", text0)
            title = text0 if heading_like else ""
            rec = {
                "id": aid, "display_id": disp, "regulation_id": rid, "regulation_name": reg["name"], "short_name": reg["short"], "document_type": reg["type"],
                **ctxf, "article_number": f"{c}{('의' + _x) if _x else ''}-{sec}-{sub}-{n}", "article_label": lab, "article_title": title,
                "is_deleted": False, "is_supplementary": False, "supplement_no": None, "is_scheduled": False, "scheduled_effective_date": "", "variant_of": None, "version_note": "",
                "paragraphs": [], "notes": [],
            }
            paras = []
            if it["paras"] and it["paras"][0]["no"] == 0 and text0 and not heading_like:   # '가.' 없이 바로 '1)' 이 나온 항목: 첫 줄을 그 항 본문으로 합친다
                it["paras"][0]["parts"] = [(text0, "\n" if it["paras"][0]["parts"] else "")] + it["paras"][0]["parts"]
                text0 = ""
            if text0 and (not heading_like or not it["paras"]):         # 제목뿐인 항목도 본문이 비지 않게 그 줄을 본문 첫 항으로 둔다
                paras.append({"id": aid + "_P0", "display_id": disp, "paragraph_number": "", "paragraph_index": None, "content": text0, "items": []})
            for p_ in it["paras"]:
                pno = p_["no"]
                pid = aid + f"_P{pno}"
                pdisp = disp + (f"-{p_['mark'].rstrip('.')}" if p_["mark"] else "")
                pd = {"id": pid, "display_id": pdisp, "paragraph_number": p_["mark"], "paragraph_index": pno or None, "content": _node_text(p_["parts"]), "items": []}
                for i3 in p_["items"]:
                    iid = pid + f"_I{i3['no']}"
                    idisp = pdisp + f"-{i3['mark']}"
                    itd = {"id": iid, "display_id": idisp, "item_number": i3["mark"], "content": _node_text(i3["parts"]), "subitems": []}
                    for s4 in i3["subs"]:
                        itd["subitems"].append({"id": iid + f"_S{s4['no']}", "display_id": idisp + f"-{s4['mark']}", "subitem_number": s4["mark"], "content": _node_text(s4["parts"])})
                    pd["items"].append(itd)
                paras.append(pd)
            rec["paragraphs"] = paras
            pg = f"{it['page0']}" if it["page0"] == it["page1"] else f"{it['page0']}-{it['page1']}"
            txt = art_plain(rec)
            tags = TAG_RE.findall(txt)
            rec.update({"amendments": sorted(set(tags), key=tags.index), "last_amended": (max((d for t in tags for d in parse_dates(t)), default="")),
                        "effective_date": reg["effective_date"], "revision_date": reg["revision_date"], "source_file": reg["source_file"], "source_path": reg["source_path"],
                        "pdf_path": reg["pdf_path"], "source_page": pg})
            recs.append(rec)
    # 부칙: 개정마다 붙은 부칙 덩어리
    for b in res["supps"]:
        aid = f"{rid}_SUPP{b['idx']}_PRE"
        disp = f"{reg['display']}-부칙{b['idx']}"
        rec = {
            "id": aid, "display_id": disp, "regulation_id": rid, "regulation_name": reg["name"], "short_name": reg["short"], "document_type": reg["type"],
            "part": "", "chapter": "", "section": "", "subsection": "", "article_number": "0", "article_label": "부칙", "article_title": "",
            "is_deleted": False, "is_supplementary": True, "supplement_no": b["idx"], "is_scheduled": False, "scheduled_effective_date": "", "variant_of": None, "version_note": "",
            "paragraphs": [], "notes": [], "supplement_header": "부 칙",
        }
        for k, p_ in enumerate(b["paras"], 1):
            mk_ = p_["mark"]
            rec["paragraphs"].append({"id": aid + f"_P{k}", "display_id": disp + (f"-{mk_.rstrip('.')}" if mk_ else ""), "paragraph_number": mk_, "paragraph_index": k if mk_ else None,
                                      "content": _node_text(p_["parts"]), "items": []})
        pg = f"{b['page0']}" if b["page0"] == b["page1"] else f"{b['page0']}-{b['page1']}"
        tags = TAG_RE.findall(art_plain(rec))
        rec.update({"amendments": sorted(set(tags), key=tags.index), "last_amended": "", "effective_date": reg["effective_date"], "revision_date": reg["revision_date"],
                    "source_file": reg["source_file"], "source_path": reg["source_path"], "pdf_path": reg["pdf_path"], "source_page": pg})
        recs.append(rec)
    for r in recs:
        r["related_articles"], r["related_regulations"], r["external_references"] = [], [], []
    return recs


def opinion_records(reg, res):
    """parse_opinion 결과 → 조문 레코드. 해석례의 한 부분(질의요지·회답·이유·관계 법령·안내) = 조문 하나, 문단 = 항(번호 표지 없음)."""
    rid = reg["id"]
    recs = []
    for k, sec in enumerate(res["sections"], 1):
        aid = f"{rid}_PART{k}"
        disp = f"{reg['display']}-{sec['heading']}"
        paras = [{"id": f"{aid}_P{j}", "display_id": disp + f"-{j}문단", "paragraph_number": "", "paragraph_index": None, "content": t, "items": []} for j, t in enumerate(sec["paras"], 1)]
        rec = {
            "id": aid, "display_id": disp, "regulation_id": rid, "regulation_name": reg["name"], "short_name": reg["short"], "document_type": reg["type"],
            "part": "", "chapter": "", "section": "", "subsection": "", "article_number": str(k), "article_label": sec["heading"], "article_title": "",
            "is_deleted": False, "is_supplementary": False, "supplement_no": None, "is_scheduled": False, "scheduled_effective_date": "", "variant_of": None, "version_note": "",
            "paragraphs": paras, "notes": [], "amendments": [], "last_amended": "",
            "effective_date": reg["effective_date"], "revision_date": reg["revision_date"], "source_file": reg["source_file"], "source_path": reg["source_path"],
            "pdf_path": reg["pdf_path"], "source_page": f"{sec['page0']}" if sec["page0"] == sec["page1"] else f"{sec['page0']}-{sec['page1']}",
            "related_articles": [], "related_regulations": [], "external_references": [],
        }
        recs.append(rec)
    return recs


def outline_annexes(reg, res, pdf_path, spacer):
    """개요형 문서 안에 들어 있는 '■ … [별표|별지|별첨 …]' 쪽들 → 부속 레코드"""
    rid = reg["id"]
    out = []
    for sp in res["annex_spans"]:
        kind = {"붙임": "별첨"}.get(sp["kind"], sp["kind"])
        abbr = KIND_ABBR[kind]
        blocks, full, npages = extract_annex(pdf_path, spacer, (sp["from"], sp["to"]), dedupe=True)
        title = ""
        for b_ in blocks:                         # 제목: 표지 줄(■ …) 다음의 첫 글 줄, 글이 없으면 첫 표의 첫 칸
            if b_["type"] == "text":
                title = next((x.strip() for x in b_["text"].split("\n") if x.strip() and not x.startswith("■") and len(x.strip()) >= 2), "")
            else:
                title = next((c_.replace("\n", " ").strip() for c_ in b_["rows"][0] if c_.strip()), "")
            if title:
                break
        if title and all(len(w) == 1 for w in title.split()) and len(title.split()) >= 3:      # '일 반 관 리 비 율' 처럼 글자 사이를 띄워 쓴 제목
            title = "".join(title.split())
        csuf = sp.get("ch_suf", "")
        aid = f"{rid}_C{sp['chapter']}" + (f"S{csuf}" if csuf else "") + f"_{abbr}" + (f"_{sp['number']}" if sp["number"] else "")
        out.append({
            "id": aid, "display_id": f"{reg['display']}-제{sp['chapter']}장" + (f"의{csuf}" if csuf else "") + f"-{kind}{sp['number']}", "regulation_id": rid, "regulation_name": reg["name"], "short_name": reg["short"],
            "document_type": reg["type"], "annex_kind": kind, "annex_number": sp["number"], "annex_title": title, "title_source": "text", "is_deleted": False, "pages": npages,
            "content": full, "content_blocks": blocks, "content_format": "blocks", "related_articles": [], "related_regulations": [],
            "effective_date": reg["effective_date"], "revision_date": reg["revision_date"], "source_file": reg["source_file"], "source_path": reg["source_path"], "pdf_path": reg["pdf_path"],
            "source_page": f"{sp['from']}" if sp["from"] == sp["to"] else f"{sp['from']}-{sp['to']}", "keywords": [],
        })
    return out


def sites_guard():
    """Sites 내보내기로 API 본문이 된 규정(config/sites-managed.json)이 있으면, 이 도구가 그 규정들을 PDF 본문으로 되돌리므로 멈춘다.
    꼭 다시 만들어야 하면 --allow-sites-overwrite 로 실행한 뒤 merge_api_regs.py --refresh-existing → import_sites_export.py reapply 순서로 되돌린다(규정/docs/SITES_IMPORT.md)."""
    p = os.path.join(OUT, "config", "sites-managed.json")
    if "--allow-sites-overwrite" in sys.argv or not os.path.exists(p):
        return
    managed = json.load(open(p, encoding="utf-8")).get("regulations") or []
    if managed:
        sys.exit(f"⚠ Sites 내보내기로 관리하는 규정 {len(managed)}개가 있어 멈춥니다(이 도구는 PDF 본문으로 되돌림). 규정/docs/SITES_IMPORT.md 의 순서를 따르세요:\n"
                 "  python3 규정/tools/build_regdb.py --allow-sites-overwrite && python3 규정/tools/merge_api_regs.py --refresh-existing && python3 규정/tools/import_sites_export.py reapply")


def main():
    sites_guard()
    src = find_src()
    pdfs, skipped = scan(src)
    reg_cfg = json.load(open(os.path.join(OUT, "config", "registry.json"), encoding="utf-8"))["regulations"]
    kw_cfg = json.load(open(os.path.join(OUT, "config", "keyword-dictionary.json"), encoding="utf-8"))["terms"]
    by_title = {r["title"]: r for r in reg_cfg}

    # 본문 문서 / 부속(별표·별지·별첨) 분류: 폴더 안의 '[' 로 시작하는 파일은 같은 폴더 본문 규정의 부속
    mains, attach = [], []
    for p, rel in pdfs:
        parts = rel.split("/")
        (attach if len(parts) > 1 and parts[-1].startswith("[") else mains).append((p, rel))
    _names = {rel for _, rel in attach}
    _dup = {rel for _, rel in attach if re.sub(r"(?:-\d+| \(\d+\))\.pdf$", ".pdf", rel) != rel and re.sub(r"(?:-\d+| \(\d+\))\.pdf$", ".pdf", rel) in _names}   # '이름-2.pdf'·'이름 (1).pdf' 중복본은 반영·복사하지 않음
    pdfs = [(p, rel) for p, rel in pdfs if rel not in _dup]
    pdf_out = os.path.join(OUT, "pdf")                      # 원문 PDF 사본(사이트에서 열 수 있게). 원본은 그대로 두고 NFC 경로로 복사
    shutil.rmtree(pdf_out, ignore_errors=True)
    for p_, rel_ in pdfs:
        os.makedirs(os.path.dirname(os.path.join(pdf_out, rel_)), exist_ok=True)
        shutil.copy2(p_, os.path.join(pdf_out, rel_))
    print(f"PDF {len(pdfs)}개 (본문 {len(mains)}, 부속 {len(attach)}), 그 밖의 형식 {len(skipped)}개")

    # 1) 본문 줄 추출 + 줄 이음 통계
    raw_lines = {}
    spacer = Spacer()
    outline_docs = {}          # rel → (cfg, 파싱 결과)  — 조(제N조) 없이 장·절·번호 목록으로 쓰인 예규
    outline_corpus = []
    opinion_docs = {}          # rel → (cfg, 파싱 결과)  — 법제처 법령해석례(질의요지·회답·이유)
    for p, rel in mains:
        pcfg = next((c for c in reg_cfg if c.get("format") == "opinion" and c.get("source_match") and nfc(c["source_match"]) in nfc(rel)), None)
        if pcfg:
            opinion_docs[rel] = (pcfg, None)
            raw_lines[rel] = page_lines(p)
            continue
        ocfg = next((c for c in reg_cfg if c.get("format") == "outline" and c.get("source_match") and nfc(c["source_match"]) in nfc(rel)), None)
        if ocfg:
            lines_o = page_lines_dedup(p)
            outline_corpus += [r["text"] for r in lines_o]
            outline_docs[rel] = (ocfg, None)
            raw_lines[rel] = lines_o
            continue
        L = page_lines(p)
        raw_lines[rel] = L
    for rel, L in raw_lines.items():
        pass
    # 머리·바닥글 제거를 위한 제목은 첫 줄에서 얻는다 (페이지 첫 줄이 문서 제목)
    def doc_title(L):
        first = [r for r in L if r["page"] == 1][:2]
        return first[0]["text"].strip() if first else ""
    corpus = []
    for rel, L in raw_lines.items():
        if rel not in outline_docs and rel not in opinion_docs:
            corpus += [r["text"] for r in clean_main(L, doc_title(L))]
    corpus += outline_corpus
    spacer.fit(corpus)
    for p, rel in mains:
        if rel in outline_docs:
            outline_docs[rel] = (outline_docs[rel][0], parse_outline.parse(p, spacer, outline_docs[rel][0].get("outline_style")))
        if rel in opinion_docs:
            opinion_docs[rel] = (opinion_docs[rel][0], parse_opinion.parse(p, spacer))

    regs, all_articles, annex_by_reg = {}, {}, {}
    problems = []   # 검토 필요 목록
    for p, rel in mains:
        L = raw_lines[rel]
        if rel in opinion_docs:
            cfg, res_p = opinion_docs[rel]
            hdr = {"name": cfg["title"], "abbr": "", "effective_date": res_p["date"], "revision_date": res_p["date"], "form": "법령해석례", "number": res_p["number"],
                   "revision_type": "", "agencies": [{"agency": "법제처", "department": ""}], "raw": list(res_p["front"])}
            ps = type("OpinionPS", (), {"front": res_p["front"], "rejected": [], "rejected_heads": [], "outline": [], "opinion": res_p})()
        elif rel in outline_docs:
            cfg, res_o = outline_docs[rel]
            hdr = parse_header(res_o["front"])
            hdr["name"] = cfg["title"]
            hdr["agencies"] = [{"agency": cfg["agency"], "department": ""}] if cfg.get("agency") else ([{"agency": "행정안전부", "department": ""}] if any("행" in x and "안" in x and "부" in x for x in res_o["front"]) else hdr["agencies"])
            ps = type("OutlinePS", (), {"front": res_o["front"], "rejected": [], "rejected_heads": [], "outline": [], "res": res_o})()
        else:
            title_line = doc_title(L)
            ps = Parser(L, title_line, spacer).run()
            hdr = parse_header(ps.front)
            cfg = by_title.get(hdr["name"]) or by_title.get(title_line)
        if not cfg:
            problems.append(f"레지스트리에 없는 규정: {hdr['name']!r} ({rel}) — config/registry.json 에 추가하세요")
            continue
        fname = os.path.basename(rel)
        fm = FN_RE.search(fname)
        reasons = []
        if fm:
            if int(fm.group(1).split("-")[0] if False else re.sub(r"\D", "", fm.group(1)) or 0) != int(re.sub(r"\D", "", hdr["number"]) or 0):
                reasons.append(f"파일명 번호({fm.group(1)})와 본문 머리({hdr['number']})가 다름")
            if fm.group(2) != hdr["effective_date"].replace("-", ""):
                reasons.append(f"파일명 시행일({fm.group(2)})와 본문 머리({hdr['effective_date']})가 다름")
        elif cfg.get("format") not in ("outline", "opinion"):
            reasons.append("파일명이 잘려 있어 파일명의 번호·시행일을 본문 머리와 대조하지 못함 (본문 머리 값 사용)")
        eff = datetime.date.fromisoformat(hdr["effective_date"]) if hdr["effective_date"] else None
        if eff and not cfg.get("confirmed_latest") and (datetime.date.fromisoformat(TODAY) - eff).days > 730:
            reasons.append(f"시행일({hdr['effective_date']})이 기준일({TODAY})로부터 2년 넘게 지남 — 이후 개정본이 있는지 확인 필요")
        if eff and eff > datetime.date.fromisoformat(TODAY):
            reasons.append(f"시행 예정(시행일 {hdr['effective_date']} > 기준일 {TODAY})")
        reg = dict(cfg)
        reg.update({
            "name": cfg.get("full_title", hdr["name"]), "abbr": hdr["abbr"], "issuer_form": hdr["form"], "number": f"제{hdr['number']}호" if hdr["number"] else "",
            "agencies": hdr["agencies"], "effective_date": hdr["effective_date"], "revision_date": hdr["revision_date"], "revision_type": hdr["revision_type"],
            "source_file": fname, "source_path": "원문/규정/" + rel, "pdf_path": "pdf/" + rel, "header_raw": hdr["raw"],
            "pages": max(r["page"] for r in L), "format": cfg.get("format", "article"), "needs_review": bool(reasons and any("2년" in x or "다름" in x or "예정" in x for x in reasons)),
            "review_reasons": reasons,
        })
        reg["version"] = reg["effective_date"] + ("-" + re.sub(r"\D", "", reg["number"]) if reg["number"] else "")
        reg["outline"] = ps.outline
        reg["rejected_lines"] = ps.rejected
        reg["rejected_heads"] = ps.rejected_heads
        reg["upcoming"] = bool(eff and eff > datetime.date.fromisoformat(TODAY))
        if reg["id"] in regs:
            # 같은 규정의 PDF 가 둘 이상: 시행일이 기준일 이전인(현재 시행 중인) 것 중 가장 늦은 것을 쓰고, 시행일이 아직 안 된 것은 '(예정)' 으로만 기록해 반영하지 않음.
            # 시행 중인 것끼리 둘 이상이면 최신본 여부를 임의로 판단하지 않고 '검토 필요'로 표시
            prev = regs[reg["id"]]
            rank = lambda x: (not x["upcoming"], x["effective_date"], x["revision_date"])
            keep, drop = (reg, prev) if rank(reg) > rank(prev) else (prev, reg)
            info = lambda x: {k: x[k] for k in ("version", "effective_date", "revision_date", "number", "source_file", "source_path")}
            keep["upcoming_versions"] = prev.get("upcoming_versions", []) + reg.get("upcoming_versions", [])
            keep["duplicates"] = prev.get("duplicates", []) + reg.get("duplicates", [])
            if drop["upcoming"] and not keep["upcoming"]:
                keep["upcoming_versions"].append(info(drop))
                keep["review_reasons"] = [x for x in keep["review_reasons"] if "시행 예정" not in x]
                keep["review_reasons"].append(f"시행 예정 판({drop['effective_date']} 시행, {drop['number']})은 아직 시행일 전이라 반영하지 않고 이전 시행본({keep['effective_date']})을 적용함")
                problems.append(f"시행 예정 PDF (미반영, 이전 시행본 적용): {drop['source_path']} / 적용: {keep['source_path']}")
            else:
                keep["duplicates"].append(info(drop))
                keep["review_reasons"].append(f"같은 규정의 PDF 가 {1 + len(keep['duplicates'])}개 있음 — 시행일이 가장 늦은 것({keep['effective_date']})만 반영했고 나머지는 반영하지 않음. 최신본 여부는 사람이 확인 필요")
                keep["needs_review"] = True
                problems.append(f"같은 규정 PDF 중복: {drop['source_path']} (미반영) / {keep['source_path']} (반영)")
            reg = keep
            if keep is not prev:
                all_articles[reg["id"]] = ps
        else:
            all_articles[reg["id"]] = ps
        regs[reg["id"]] = reg

    # 2) 정의된 약칭, 조문 레코드
    resolver = Resolver(list(regs.values()))
    records, supp_records = {}, {}
    for rid, ps in all_articles.items():
        reg = regs[rid]
        recs = []
        if hasattr(ps, "opinion"):                          # 법령해석례
            records[rid] = fix_pua(opinion_records(reg, ps.opinion))
            reg["outline"] = [{"kind": "부분", "title": sec["heading"], "raw": sec["heading"], "page": sec["page0"], "deleted": False} for sec in ps.opinion["sections"]]
            continue
        if hasattr(ps, "res"):                              # 개요형 문서
            records[rid] = fix_pua(outline_records(reg, ps.res))
            reg["outline"] = [{"kind": k_, "title": t_, "raw": t_, "page": 0, "deleted": False} for k_, t_ in outline_heads(ps.res)]
            continue

        def mk(a, supp_idx=0, multi_supp=False):
            num, br = a["num"], a["branch"]
            nb = f"{num}" + (f"의{br}" if br else "")
            v = a.get("variant", 0)
            lab = f"제{num}조" + (f"의{br}" if br else "")
            if num == 0 and supp_idx:        # 조 번호 없이 서술된 부칙
                sfx = f"{supp_idx}" if multi_supp else ""
                aid, disp = f"{rid}_SUPP{sfx}_PRE", f"{reg['display']}-부칙{sfx}"
            elif supp_idx:
                sid = f"_SUPP{supp_idx}" if multi_supp else "_SUPP"
                aid = f"{rid}{sid}_ART_{num}" + (f"_{br}" if br else "")
                disp = f"{reg['display']}-부칙" + (f"{supp_idx}" if multi_supp else "") + f"-{lab}"
            else:
                aid = f"{rid}_ART_{num}" + (f"_{br}" if br else "")
                disp = f"{reg['display']}-{lab}"
            if v:
                aid, disp = f"{aid}_NEXT{v}", f"{disp}@시행예정"
            rec = {
                "id": aid, "display_id": disp, "regulation_id": rid, "regulation_name": reg["name"], "short_name": reg["short"], "document_type": reg["type"],
                "part": a["ctx"]["part"], "chapter": a["ctx"]["chapter"], "section": a["ctx"]["section"], "subsection": a["ctx"]["subsection"],
                "article_number": nb, "article_label": ("부칙" if num == 0 else f"제{num}조" + (f"의{br}" if br else "")), "article_title": a["title"],
                "is_deleted": bool(a["deleted"]), "is_supplementary": bool(supp_idx), "supplement_no": supp_idx or None,
                "is_scheduled": bool(v), "scheduled_effective_date": "", "variant_of": None, "version_note": "",
                "paragraphs": [], "notes": list(a["notes"]),
            }
            for pn in a["paras"]:
                pno = pn.no
                pidx = PARA_NO.get(pno) if pno else None
                pid = aid + (f"_P{pidx}" if pidx else "_P0")
                pd = {"id": pid, "display_id": disp + (f"-제{pidx}항" if pidx else ""), "paragraph_number": pno, "paragraph_index": pidx, "content": node_text(pn), "items": []}
                for it in pn.children:
                    iid = pid + f"_I{it.no}" + (f"_{it.suffix}" if it.suffix else "")
                    idisp = pd["display_id"] + f"-제{it.no}호" + (f"의{it.suffix}" if it.suffix else "")
                    itd = {"id": iid, "display_id": idisp, "item_number": it.no + (f"의{it.suffix}" if it.suffix else ""), "content": node_text(it), "subitems": []}
                    for sb in it.children:
                        sid2 = iid + f"_S{HAN_NO[sb.no]}" + (f"_{sb.suffix}" if sb.suffix else "")
                        itd["subitems"].append({"id": sid2, "display_id": idisp + f"-{sb.no}목" + (f"의{sb.suffix}" if sb.suffix else ""), "subitem_number": sb.no + (f"의{sb.suffix}" if sb.suffix else ""), "content": node_text(sb)})
                    pd["items"].append(itd)
                rec["paragraphs"].append(pd)
            pg = f"{a['page0']}" if a["page0"] == a["page1"] else f"{a['page0']}-{a['page1']}"
            rec.update({"effective_date": reg["effective_date"], "revision_date": reg["revision_date"], "source_file": reg["source_file"], "source_path": reg["source_path"], "pdf_path": reg["pdf_path"], "source_page": pg})
            txt = art_plain(rec)
            tags = [t for t in TAG_RE.findall(txt)] + [n for n in rec["notes"]]
            if v:
                sd = next((parse_dates(n)[0] for n in rec["notes"] if "시행일" in n and parse_dates(n)), "")
                rec["scheduled_effective_date"] = sd
                rec["variant_of"] = aid.rsplit("_NEXT", 1)[0]
                rec["version_note"] = "현행 조문과 함께 원문 PDF 에 실린, 시행일이 다른 개정본"
                if sd:
                    rec["display_id"] = f"{disp.split('@')[0]}@{sd}"
            rec["amendments"] = sorted(set(tags), key=tags.index)
            ds = [d for t in tags for d in parse_dates(t)]
            rec["last_amended"] = max(ds) if ds else ""
            return rec

        for a in ps.articles:
            recs.append(mk(a))
        multi = len(ps.supps) > 1
        for s in ps.supps:
            for a in s["articles"]:
                r2 = mk(a, s["idx"], multi)
                r2["supplement_header"] = s["header"]
                recs.append(r2)
        records[rid] = recs

    # 3) 인용(관련 조문·관련 규정)
    index_of = {rid: {(r["article_number"]): r["id"] for r in recs if not r["is_supplementary"] and not r["is_scheduled"]} for rid, recs in records.items()}
    edges = {}
    unresolved = []
    for rid, recs in records.items():
        full_text = "\n".join(art_plain(r) for r in recs)
        local = resolver.local_defs(full_text)
        for rec in recs:
            rec["related_articles"], rec["related_regulations"], rec["external_references"] = [], [], []
            if rec["is_supplementary"] or rec["is_deleted"] or rec["is_scheduled"]:
                continue
            txt = art_plain(rec)
            last = {}
            seen_a, seen_r, seen_e = [], [], []
            for tgt, num, br, ext in resolver.extract(rid, txt, local, last):
                if tgt is None:
                    if ext and ext not in seen_e:
                        seen_e.append(ext)
                    continue
                nb = f"{num}" + (f"의{br}" if br else "")
                tid = index_of.get(tgt, {}).get(nb)
                if not tid:
                    if tgt in index_of:
                        unresolved.append({"from": rec["id"], "to_regulation": tgt, "article": nb})
                    continue
                if tid == rec["id"]:
                    continue
                if tid not in seen_a:
                    seen_a.append(tid)
                if tgt != rid and tgt not in seen_r:
                    seen_r.append(tgt)
            rec["related_articles"], rec["related_regulations"], rec["external_references"] = seen_a, seen_r, seen_e
            for t in seen_a:
                edges[(rec["id"], t)] = 1

    # 4) 키워드 — 제목에 있는 사전어는 항상, 본문에서는 2회 이상 나오고 너무 흔하지 않은 사전어(TF-IDF 비슷하게)
    import math
    terms = []
    for t in kw_cfg:
        names = [t["term"]] + t["aliases"]
        terms.append((t["term"], sorted({ns(n) for n in names}, key=len, reverse=True)))
    kw_inverted = {t["term"]: [] for t in kw_cfg}
    stats = {}                                   # id → [(term, in_title, count)]
    df = {t: 0 for t, _ in terms}

    short_re = {term: [re.compile(r"(?<![가-힣])" + re.escape(f)) for f in forms if len(f) <= 3] for term, forms in terms}

    def count_terms(title_text, body_text):
        tt, bb = ns(title_text), ns(body_text)
        t1, b1 = re.sub(r"\s+", " ", title_text), re.sub(r"\s+", " ", body_text)
        out = []
        for term, forms in terms:
            long_forms = [f for f in forms if len(f) > 3]
            in_t = any(f in tt for f in long_forms) or any(r.search(t1) for r in short_re[term])
            c = sum(bb.count(f) for f in long_forms) + sum(len(r.findall(b1)) for r in short_re[term])
            if in_t or c:
                out.append((term, in_t, c))
                df[term] += 1
        return out

    for rid, recs in records.items():
        for rec in recs:
            stats[rec["id"]] = count_terms(rec["article_title"], art_plain(rec))
    n_total = max(1, len(stats))

    def pick(rows):
        sc = []
        for term, in_t, c in rows:
            common = df[term] / n_total
            if in_t or (c >= 2 and common < 0.12):
                sc.append((term, 1000 * in_t + c * math.log(1 + 1 / max(common, 0.001))))
        sc.sort(key=lambda x: -x[1])
        return [t for t, _ in sc[:6]]

    for rid, recs in records.items():
        for rec in recs:
            rec["keywords"] = pick(stats[rec["id"]])
            for t in rec["keywords"]:
                kw_inverted[t].append(rec["id"])

    # 5) 부속(별표·별지·별첨)
    parent_dirs = {os.path.dirname(rel): rid for rid in regs for rel in [regs[rid]["source_path"][len("원문/규정/"):]] if os.path.dirname(rel)}
    for rid_ in regs:                                           # 본문과 다른 폴더에 있는 부속(registry 의 annex_dirs)
        for dn in regs[rid_].get("annex_dirs", []):
            parent_dirs[nfc(dn)] = rid_
    annexes = {rid: [] for rid in regs}
    for rid_, ps_ in all_articles.items():                    # 개요형 문서 안에 든 별표·별지
        if hasattr(ps_, "res"):
            annexes[rid_] += fix_pua(outline_annexes(regs[rid_], ps_.res, R_pdf(regs[rid_]), spacer))
    attach_names = {rel for _, rel in attach}
    for p, rel in attach:
        dup_base = re.sub(r"(?:-\d+| \(\d+\))\.pdf$", ".pdf", rel)           # '이름-2.pdf'·'이름 (1).pdf' 처럼 같은 부속이 두 벌이면 접미사 없는 쪽만 반영
        if dup_base != rel and dup_base in attach_names:
            problems.append(f"같은 부속 PDF 중복 — 접미사 없는 파일만 반영, 최신본 확인 필요: {rel}")
            continue
        d = os.path.dirname(rel)
        rid = parent_dirs.get(d)
        fname = os.path.basename(rel)
        if not rid:
            problems.append(f"부속 자료의 본문 규정을 찾지 못함: {rel}")
            try:
                os.remove(os.path.join(pdf_out, rel))            # 반영하지 않는 자료는 PDF 사본도 두지 않음
            except OSError:
                pass
            continue
        reg = regs[rid]
        m = ANNEX_FN.match(fname)
        if not m:
            problems.append(f"부속 파일명을 해석하지 못함: {rel}")
            continue
        kind, no, sub, rest = m.group(1), m.group(2), m.group(3), m.group(4)
        no = no or ""
        number = no + (f"의{sub}" if sub else "")
        # 파일명 뒤의 '(상위 규정명…)' 제거 → 남는 것이 부속 제목
        pt = reg["title"]
        cut = None
        for k in [i for i, ch in enumerate(rest) if ch == "("][::-1]:
            tail = re.sub(r"[…\.\)\s]+$", "", rest[k + 1:])
            if len(tail) >= 2 and (pt.startswith(tail) or tail.startswith(pt)):
                cut = k
                break
        title_fn = (rest[:cut] if cut is not None else rest).strip()
        title_trunc = cut is None and "…" in rest        # 파일명이 제목 중간에서 잘림
        blocks, full, npages = extract_annex(p, spacer)
        pages = range(npages)
        deleted = bool(re.fullmatch(r"\s*(?:\[[^\]]*\]\s*)?<?\s*삭제\s*>?\s*", full)) or title_fn.strip() == "삭제"
        title, title_src = title_fn, "filename"
        if title_trunc and not deleted:
            # 본문 첫 줄들에서 복원: 파일명 앞부분을 덮을 때까지, 줄이 쉼표로 끝나면 다음 줄까지 (최대 3줄)
            first = [re.sub(r"\s+", " ", ln).strip() for ln in full.split("\n") if ln.strip()]
            while first and re.match(r"^■.*\[(?:별표|별지|별첨)[^\]]*\]", first[0]):      # '■ 규정명 [별표 3] <개정 …>' 머리 줄은 제목이 아님
                first.pop(0)
            first = first[:4] or [""]
            first[0] = re.sub(r"^\[[^\]]*\]\s*", "", first[0])
            norm = lambda x: ns(x).replace("¸", ",")
            prefix = norm(title_fn.rstrip("…"))
            got, i = "", 0
            while i < len(first) and i < 3:
                got = (got + " " + first[i]).strip()
                i += 1
                if got.endswith("관련)") or not (got.endswith(",") or not norm(got).startswith(prefix) or len(norm(got)) < len(prefix)):
                    break
            if norm(got).startswith(prefix[:max(4, len(prefix) - 2)]) or (got.endswith("관련)") and norm(got).startswith(prefix[:max(8, len(prefix) * 3 // 5)])):   # 파일명과 본문 제목 표기가 조금 다른 경우(예: 아니하는 ↔ 않는)는 본문 제목을 씀
                title, title_src = got.rstrip(","), "text"
            else:
                title = title_fn.rstrip("…")
        rel_art = re.search(r"\((제\s*\d+\s*조[^)]*?)\s*관련\)", title)
        rel_ids = []
        if rel_art:
            for mm in re.finditer(r"제\s*(\d+)\s*조(?:의\s*(\d+))?", rel_art.group(1)):
                nb = mm.group(1) + (f"의{mm.group(2)}" if mm.group(2) else "")
                tid = index_of.get(rid, {}).get(nb)
                if tid and tid not in rel_ids:
                    rel_ids.append(tid)
        aid = f"{rid}_{KIND_ABBR[kind]}" + (f"_{no}" if no else "") + (f"_{sub}" if sub else "")
        annexes[rid].append({
            "id": aid, "display_id": f"{reg['display']}-{kind}{number}", "regulation_id": rid, "regulation_name": reg["name"], "short_name": reg["short"],
            "document_type": reg["type"], "annex_kind": kind, "annex_number": number, "annex_title": title, "title_source": title_src,
            "is_deleted": deleted, "pages": npages, "content": "" if deleted else full, "content_blocks": [] if deleted else blocks, "content_format": "blocks",
            "related_articles": rel_ids, "related_regulations": [],
            "effective_date": reg["effective_date"], "revision_date": reg["revision_date"],
            "source_file": fname, "source_path": "원문/규정/" + rel, "pdf_path": "pdf/" + rel, "source_page": "1" if npages == 1 else f"1-{npages}",
            "keywords": [],
        })
    for rid, L in annexes.items():
        order = {"별표": 0, "별지": 1, "별첨": 2}
        L.sort(key=lambda a: (int((re.search(r"_C(\d+)_", a["id"]) or re.match(r"(0?)", "0")).group(1) if re.search(r"_C(\d+)_", a["id"]) else 0), order[a["annex_kind"]], int((re.match(r"\d+", a["annex_number"]) or re.match(r"(0?)", "0")).group() or 0), a["annex_number"]))
        for a in L:
            a["keywords"] = pick(count_terms(a["annex_title"], a["annex_title"] + " " + a["content"]))
            for t in a["keywords"]:
                kw_inverted[t].append(a["id"])

    # 6) 파일 쓰기
    regs_sorted = sorted(regs.values(), key=lambda r: [x["id"] for x in reg_cfg].index(r["id"]))
    reg_idx = {r["id"]: i for i, r in enumerate(regs_sorted)}
    reg_list, index_rows, annex_rows = [], [], []
    for reg in regs_sorted:
        rid = reg["id"]
        recs, ann = records[rid], annexes[rid]
        meta = {
            "schema_version": SCHEMA_VERSION, "id": rid, "name": reg["name"], "short_name": reg["short"], "display_id_prefix": reg["display"], "slug": reg["slug"],
            "aliases": reg["aliases"], "document_type": reg["type"], "format": reg.get("format", "article"), "issuer_form": reg["issuer_form"], "number": reg["number"], "group": reg["group"],
            "agencies": reg["agencies"], "effective_date": reg["effective_date"], "revision_date": reg["revision_date"], "revision_type": reg["revision_type"],
            "version": reg["version"],
            "versions": [{"version": reg["version"], "effective_date": reg["effective_date"], "revision_date": reg["revision_date"], "number": reg["number"],
                          "source_file": reg["source_file"], "source_path": reg["source_path"], "status": "loaded", "is_latest": "unverified"}]
                        + [{**d_, "status": "not-loaded", "is_latest": "unverified"} for d_ in reg.get("duplicates", [])]
                        + [{**d_, "status": "upcoming-not-loaded", "is_latest": "unverified"} for d_ in reg.get("upcoming_versions", [])],
            "latest_status": "검토 필요" if reg["needs_review"] else ("최신본(사람이 확인함)" if reg.get("confirmed_latest") else "단일본(최신 여부는 원문 대조로만 확인 가능)"),
            "needs_review": reg["needs_review"], "review_reasons": reg["review_reasons"],
            "status": "예정" if reg["upcoming"] else "시행", "upcoming_versions": reg.get("upcoming_versions", []),
            "parent_regulation_id": reg.get("parent"), "source_file": reg["source_file"], "source_path": reg["source_path"], "pdf_path": reg["pdf_path"], "source_pages": reg["pages"],
            "header_raw": reg["header_raw"], "outline": reg["outline"],
            "counts": {"articles": sum(1 for r in recs if not r["is_supplementary"] and not r["is_scheduled"]), "scheduled_versions": sum(1 for r in recs if r["is_scheduled"]), "deleted_articles": sum(1 for r in recs if r["is_deleted"] and not r["is_supplementary"]),
                       "supplementary_articles": sum(1 for r in recs if r["is_supplementary"]), "annexes": len(ann)},
            "files": {"articles": f"regulations/{reg['slug']}/articles.json", "annexes": f"regulations/{reg['slug']}/annexes.json", "markdown": f"regulations/{reg['slug']}/regulation.md"},
        }
        d = os.path.join(OUT, "regulations", reg["slug"])
        archive_previous(d, meta)
        if os.path.exists(os.path.join(d, "explanations.json")):      # 사람이 관리하는 설명 데이터(AI 설명 등). 빌드가 만들거나 지우지 않는다
            meta["files"]["explanations"] = f"regulations/{reg['slug']}/explanations.json"
        jdump(os.path.join(d, "metadata.json"), meta, indent=1)
        jdump(os.path.join(d, "articles.json"), recs)
        jdump(os.path.join(d, "annexes.json"), ann)
        md = render_markdown(meta, recs, ann)
        with open(os.path.join(d, "regulation.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(md)
        with open(os.path.join(OUT, "markdown", reg["slug"] + ".md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(md)
        reg_list.append({k: meta[k] for k in ("id", "name", "short_name", "display_id_prefix", "slug", "aliases", "document_type", "format", "issuer_form", "number", "group", "agencies",
                                              "effective_date", "revision_date", "revision_type", "version", "latest_status", "needs_review", "review_reasons", "status", "upcoming_versions",
                                              "parent_regulation_id", "source_file", "source_path", "pdf_path", "source_pages", "counts", "files")})
        for r in recs:
            row = {"id": r["id"], "r": reg_idx[rid], "n": r["article_label"], "t": r["article_title"], "k": r["keywords"], "c": r["chapter"] or r["part"]}
            if r["related_regulations"]:
                row["rr"] = [reg_idx[x] for x in r["related_regulations"] if x in reg_idx]
            if r["is_deleted"]:
                row["x"] = 1
            if r["is_supplementary"]:
                row["s"] = r["supplement_no"]
            if r["is_scheduled"]:
                row["f"] = r["scheduled_effective_date"] or "?"
            index_rows.append(row)
        for a in ann:
            annex_rows.append({"id": a["id"], "r": reg_idx[rid], "kind": a["annex_kind"], "n": a["annex_number"], "t": a["annex_title"], "k": a["keywords"], **({"x": 1} if a["is_deleted"] else {})})

    build_id = hashlib.sha1(json.dumps([r["id"] for r in index_rows] + [r["version"] for r in reg_list]).encode()).hexdigest()[:10]
    jdump(os.path.join(OUT, "data", "regulations.json"), {"schema_version": SCHEMA_VERSION, "generated": TODAY, "build_id": build_id, "regulations": reg_list}, indent=1)
    jdump(os.path.join(OUT, "data", "regulation-index.json"), {
        "schema_version": SCHEMA_VERSION, "generated": TODAY, "build_id": build_id,
        "_comment": "검색용 경량 인덱스. 본문은 없음 → 필요할 때 regulations/<slug>/articles.json 을 불러온다. r 는 regulations 배열의 순번.",
        "regulations": [{"id": r["id"], "name": r["name"], "short_name": r["short_name"], "slug": r["slug"], "aliases": r["aliases"], "document_type": r["document_type"], "group": r["group"],
                         "effective_date": r["effective_date"], "revision_date": r["revision_date"], "number": r["number"], "display_id_prefix": r["display_id_prefix"], "status": r["status"], "needs_review": r["needs_review"], "review_reasons": r["review_reasons"], "latest_status": r["latest_status"], "source_file": r["source_file"], "source_path": r["source_path"],
                         "articles": r["counts"]["articles"], "annexes": r["counts"]["annexes"], **({"explanations": True} if "explanations" in r["files"] else {})} for r in reg_list],
        "articles": index_rows, "annexes": annex_rows,
    })
    jdump(os.path.join(OUT, "data", "effective-dates.json"), {      # 홈 배너의 '시행 안내'용 경량 목록(규정명, 시행일)
        "schema_version": SCHEMA_VERSION, "generated": TODAY,
        "items": [[r["short_name"], r["effective_date"], r["group"]] for r in reg_list if r["effective_date"]],
    })
    jdump(os.path.join(OUT, "data", "keywords.json"), {
        "schema_version": SCHEMA_VERSION, "generated": TODAY, "build_id": build_id,
        "_comment": "terms=키워드 사전(대표어·유사어·분류), index=키워드 → 조문/부속 ID 목록(제목에 있거나 본문에 2회 이상 나온 것).",
        "terms": [{"term": t["term"], "category": t["category"], "aliases": t["aliases"], "count": len(kw_inverted[t["term"]])} for t in kw_cfg],
        "index": {k: v for k, v in kw_inverted.items() if v},
    })
    ids = sorted({x for e in edges for x in e})
    pos = {x: i for i, x in enumerate(ids)}
    cross = {}
    for (a, b) in edges:
        ra, rb = a.split("_ART_")[0].split("_SUPP")[0], b.split("_ART_")[0].split("_SUPP")[0]
        cross[(ra, rb)] = cross.get((ra, rb), 0) + 1
    jdump(os.path.join(OUT, "data", "relations.json"), {
        "schema_version": SCHEMA_VERSION, "generated": TODAY, "build_id": build_id,
        "_comment": "hierarchy=법적 위계(config/registry.json 의 parent, 사람이 관리). article_links=조문 본문의 인용에서 자동 추출한 방향 링크(파생 데이터, 원문 아님): edges[i]=[from,to] 는 ids 의 순번.",
        "hierarchy": [{"id": r["id"], "parent": r["parent_regulation_id"], "group": r["group"], "document_type": r["document_type"]} for r in reg_list],
        "regulation_references": sorted([{"from": a, "to": b, "count": c} for (a, b), c in cross.items() if a != b], key=lambda x: (-x["count"], x["from"], x["to"])),
        "article_links": {"ids": ids, "edges": sorted([[pos[a], pos[b]] for (a, b) in edges])},
    })
    write_source_index(reg_list, records, annexes, attach, skipped, problems, unresolved)
    print(f"규정 {len(reg_list)}개 · 조문 {sum(len(v) for v in records.values())}개 · 부속 {sum(len(v) for v in annexes.values())}개 · 인용 링크 {len(edges)}개")
    for p_ in problems:
        print("⚠", p_)
    if unresolved:
        print(f"⚠ 대상 조문을 찾지 못한 인용 {len(unresolved)}건 (docs/QUALITY_REPORT.md 참고)")
    bdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".build")      # 검증용 중간 결과(커밋하지 않음)
    jdump(os.path.join(bdir, "unresolved.json"), unresolved)
    jdump(os.path.join(bdir, "rejected.json"), {r["short"]: {"articles": r.get("rejected_lines", []), "headings": r.get("rejected_heads", [])} for r in regs.values()})
    return 0


def archive_previous(d, meta):
    """같은 규정의 이전 버전이 이미 만들어져 있고 버전(시행일+번호)이 달라졌으면, 덮어쓰기 전에 history/<버전>/ 으로 보관한다 (개정 전후 비교용).
    meta["versions"] 에는 현재 버전 + 보관된 버전들이 들어간다."""
    mp = os.path.join(d, "metadata.json")
    if os.path.exists(mp):
        try:
            old = json.load(open(mp, encoding="utf-8"))
        except ValueError:
            old = None
        if old and old.get("source") == "api":              # Sites 로 API 본문이 된 규정을 --allow-sites-overwrite 로 다시 만들 때: API 판은 import_sites_export.py reapply 가 되살리므로 보관하지 않음
            old = None
        if old and old.get("version") and old["version"] != meta["version"]:
            dst = os.path.join(d, "history", old["version"])
            if not os.path.exists(dst):
                os.makedirs(dst)
                for f in ("metadata.json", "articles.json", "annexes.json"):
                    if os.path.exists(os.path.join(d, f)):
                        shutil.copy2(os.path.join(d, f), os.path.join(dst, f))
    hist = os.path.join(d, "history")
    if os.path.isdir(hist):
        for v in sorted(os.listdir(hist)):
            hm = os.path.join(hist, v, "metadata.json")
            if v != meta["version"] and os.path.exists(hm):
                o = json.load(open(hm, encoding="utf-8"))
                meta["versions"].append({"version": v, "effective_date": o.get("effective_date", ""), "revision_date": o.get("revision_date", ""), "number": o.get("number", ""),
                                         "source_file": o.get("source_file", ""), "source_path": o.get("source_path", ""), "status": "archived", "is_latest": False,
                                         "path": f"regulations/{os.path.basename(d)}/history/{v}/"})


def render_markdown(meta, recs, ann):
    L = [f"# {meta['name']}", "",
         "> 이 문서는 `규정/tools/build_regdb.py` 가 원문 PDF 에서 만든 열람용 사본입니다. 원문이 아니라 추출본이므로 중요한 판단은 원본 PDF 로 확인하세요.", "",
         f"- 규정 ID: `{meta['id']}`", f"- 종류: {meta['document_type']} ({meta['issuer_form']} {meta['number']})",
         (f"- 회신일: {meta['effective_date']}" if meta.get("format") == "opinion" else f"- 시행일: {meta['effective_date']} / 공포·개정일: {meta['revision_date']} ({meta['revision_type']})"),
         f"- 원본: `{meta['source_path']}` ({meta['source_pages']}쪽)"]
    if meta["needs_review"]:
        L.append("- **검토 필요**: " + " / ".join(meta["review_reasons"]))
    L.append("")
    ctx = ("", "", "", "")
    supp_seen = set()
    for r in recs:
        if r["is_supplementary"]:
            if r["supplement_no"] not in supp_seen:
                supp_seen.add(r["supplement_no"])
                L += ["", f"## {r.get('supplement_header', '부칙')}", ""]
        else:
            c = (r["part"], r["chapter"], r["section"], r["subsection"])
            for lvl, (old, new) in enumerate(zip(ctx, c)):
                if new and new != old:
                    L += ["", "#" * (2 + lvl) + " " + new, ""]
            ctx = c
        head = f"{r['article_label']}" + (f"({r['article_title']})" if r["article_title"] else "")
        L.append(f"##### {head}  `{r['id']}`")
        L.append("")
        for p in r["paragraphs"]:
            if p["content"].strip():
                L.append(((p["paragraph_number"] + " ") if p["paragraph_number"] else "") + p["content"].replace("\n", "  \n"))
                L.append("")
            for it in p["items"]:
                L.append(f"  {it['item_number']}. {it['content']}".replace("\n", "  \n"))
                for sb in it["subitems"]:
                    L.append(f"    {sb['subitem_number']}. {sb['content']}".replace("\n", "  \n"))
                L.append("")
        for n in r["notes"]:
            L.append(f"*{n}*")
            L.append("")
    if ann:
        L += ["", "## 별표·별지·별첨", "", "> 표는 PDF 에서 글자 배치대로 추출한 것이라 칸 구분이 어긋날 수 있어요. 정확한 표는 원본 PDF 를 보세요.", ""]
        for a in ann:
            L += [f"### [{a['annex_kind']} {a['annex_number']}] {a['annex_title']}", "", f"- ID `{a['id']}` · 원본 `{a['source_path']}`", ""]
            if a["is_deleted"]:
                L += ["삭제", ""]
            else:
                L += ["```text", a["content"], "```", ""]
    return "\n".join(L) + "\n"


def write_source_index(reg_list, records, annexes, attach, skipped, problems, unresolved):
    L = ["# 원본 자료 색인 (SOURCE_INDEX)", "", "`규정/tools/build_regdb.py` 가 만든 파일입니다. 직접 고치지 마세요.", "",
         f"- 원본 폴더: `원문/규정/` (읽기 전용, 수정하지 않음)",
         f"- 분석한 PDF: {len(reg_list) + len(attach)}개 (본문 {len(reg_list)} + 별표·별지·별첨 {len(attach)})",
         f"- PDF 가 아니라 분석하지 못한 파일: {len(skipped)}개" + ("" if not skipped else " — " + ", ".join(f"`{r}`" for _, r in skipped)),
         "- 파일명은 NFC 로 적었어요. 원본 폴더가 macOS 에서 올라온 NFD 이름이면 파일 시스템 이름과 바이트가 다를 수 있으니, 찾을 때는 NFC 로 맞춰 비교하세요.", "",
         "## 규정별 원본", "", "| 규정 | ID | 종류 | 시행일 | 개정일 | 원본 파일 | 쪽 | 조문 | 부속 | 상태 |", "|---|---|---|---|---|---|---:|---:|---:|---|"]
    for r in reg_list:
        L.append(f"| {r['name']} | `{r['id']}` | {r['document_type']} | {r['effective_date']} | {r['revision_date']} | `{r['source_path']}` | {r['source_pages']} | {r['counts']['articles']} (+부칙 {r['counts']['supplementary_articles']}) | {r['counts']['annexes']} | {r['latest_status']} |")
    L += ["", "## 검토 필요 항목", ""]
    any_ = False
    for r in reg_list:
        if r["review_reasons"]:
            any_ = True
            L.append(f"- **{r['name']}** (`{r['id']}`)" + (" — ⚠ 검토 필요" if r["needs_review"] else ""))
            for x in r["review_reasons"]:
                L.append(f"  - {x}")
    if problems:
        any_ = True
        L += ["", "### 처리 중 문제"] + [f"- {x}" for x in problems]
    if not any_:
        L.append("- 없음")
    L += ["", "## 부속 자료(별표·별지·별첨)", "", "| 규정 | 종류 | 번호 | 제목 | 원본 파일 | 쪽 |", "|---|---|---|---|---|---:|"]
    for r in reg_list:
        for a in annexes[r["id"]]:
            L.append(f"| {r['short_name']} | {a['annex_kind']} | {a['annex_number']} | {'(삭제)' if a['is_deleted'] else a['annex_title']} | `{a['source_file']}` | {a['pages']} |")
    with open(os.path.join(OUT, "docs", "SOURCE_INDEX.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())
