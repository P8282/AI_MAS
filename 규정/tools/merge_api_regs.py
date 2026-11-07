#!/usr/bin/env python3
"""법제처 API 로 받은 법령(원문/법제처API/*.json)을 규정 DB 에 이어 붙인다.

  python3 규정/tools/merge_api_regs.py            # 붙이기 (다시 실행해도 같은 결과: 앞서 붙인 API 규정은 지우고 다시 붙임)
  python3 규정/tools/merge_api_regs.py --check    # 파일을 쓰지 않고, 기존(PDF 기반) 규정에 같은 인용·키워드 계산을 적용해 저장된 값과 같은지 확인

순서: build_regdb.py(PDF 기반 규정) → merge_api_regs.py(API 기반 규정). build_regdb.py 는 이 단계를 모르므로, 다시 실행하면 API 규정이 data/ 에서 빠지니 이어서 이 도구를 실행한다.
입력: 규정/config/registry-api.json (사람이 관리) + 원문/법제처API/<법령명>.json
출력: 규정/regulations/<slug>/{metadata,articles,annexes}.json·regulation.md, 규정/markdown/<slug>.md, 규정/data/*.json 에 항목 추가(맨 뒤에 붙으므로 기존 규정의 순번·ID 는 그대로)
기존 규정의 조문 파일은 바꾸지 않는다(--refresh-existing 을 줄 때만 기존 조문의 인용 파생값을 다시 계산).
"""
import argparse
import datetime
import hashlib
import html
import json
import math
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.modules.setdefault("pymupdf", types.ModuleType("pymupdf"))   # build_regdb 를 불러오기 위한 자리표시(이 도구는 PDF 를 읽지 않음)
sys.path.insert(0, HERE)
import warnings  # noqa: E402
warnings.filterwarnings("ignore", category=SyntaxWarning)
from build_regdb import art_plain, jdump, parse_dates, render_markdown  # noqa: E402
from parse_regulation import HAN_NO, PARA_NO, TAG_RE  # noqa: E402
from refs import Resolver, ns  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(ROOT, "규정")
SRC_DIR = os.path.join(ROOT, "원문", "법제처API")
TODAY = datetime.date.today().isoformat()
SCHEMA_VERSION = "1.0"
PARA_CH = {v: k for k, v in PARA_NO.items()}


def jload(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def d8(s):
    s = re.sub(r"\D", "", s or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else ""


def listify(x):
    return x if isinstance(x, list) else ([] if x in (None, "") else [x])


def clean(t):
    """줄마다 앞뒤·겹친 공백을 정리(글자는 그대로)."""
    lines = [re.sub(r"[ \t　 ]+", " ", ln).strip() for ln in html.unescape(str(t or "")).replace("\r", "").split("\n")]
    return "\n".join(x for x in lines if x)


# ---------------------------------------------------------------- API → 조문 레코드
HEAD = re.compile(r"^제\s*(\d+)\s*조(?:\s*의\s*(\d+))?\s*(?:\(([^)]*)\))?\s*(.*)$", re.S)
LEVELS = [("part", re.compile(r"^제\d+편")), ("chapter", re.compile(r"^제\d+장")), ("section", re.compile(r"^제\d+절")), ("subsection", re.compile(r"^제\d+관"))]
PARA_MARK = re.compile(r"^\s*(?:([" + re.escape("".join(PARA_NO)) + r"])|<(\d+)>)\s*")   # 원문자 항 번호만(문자 범위로 쓰면 표의 선 글자까지 걸림)
ITEM_MARK = re.compile(r"^\s*(\d+)(?:의(\d+))?\.\s*")
SUB_MARK = re.compile(r"^\s*([가-하])(?:의(\d+))?\.\s*")


def strip_mark(text, rx):
    return rx.sub("", clean(text), count=1)


def sub_records(rid, base_id, base_disp, p_idx, items_api):
    """호·목 → items[]"""
    items = []
    for ho in listify(items_api):
        no = re.sub(r"\D", "", ho.get("호번호", "")) or "0"
        br = str(ho.get("호가지번호") or "").strip()
        iid = f"{base_id}_I{no}" + (f"_{br}" if br else "")
        idisp = base_disp + f"-제{no}호" + (f"의{br}" if br else "")
        it = {"id": iid, "display_id": idisp, "item_number": no + (f"의{br}" if br else ""), "content": strip_mark(ho.get("호내용", ""), ITEM_MARK), "subitems": []}
        for mk in listify(ho.get("목")):
            raw = str(mk.get("목번호", "")).strip().rstrip(".")
            m = re.match(r"([가-하])(?:의(\d+))?$", raw)
            if not m or m.group(1) not in HAN_NO:
                raise ValueError(f"목 번호를 해석하지 못함: {rid} {iid} {raw!r}")
            sid = f"{iid}_S{HAN_NO[m.group(1)]}" + (f"_{m.group(2)}" if m.group(2) else "")
            it["subitems"].append({"id": sid, "display_id": idisp + f"-{m.group(1)}목" + (f"의{m.group(2)}" if m.group(2) else ""), "subitem_number": raw,
                                   "content": strip_mark(mk.get("목내용", ""), SUB_MARK)})
        items.append(it)
    return items


def build_article(reg, u, ctx, supp_idx=0, multi_supp=False, paras_override=None, notes=None, header_text=None):
    """조문단위 하나 → 조문 레코드. u 는 API 의 조문단위(또는 부칙 줄에서 만든 같은 모양의 dict)."""
    rid = reg["id"]
    num = int(re.sub(r"\D", "", str(u.get("조문번호") or "0")) or 0)
    br = str(u.get("조문가지번호") or "").strip()
    nb = f"{num}" + (f"의{br}" if br else "")
    lab = "부칙" if (num == 0 and supp_idx) else f"제{num}조" + (f"의{br}" if br else "")
    title = clean(u.get("조문제목") or "")
    head = clean(u.get("조문내용") or "")
    m = HEAD.match(head)
    rest = clean(m.group(4)) if m else head
    if not title and m and m.group(3):
        title = clean(m.group(3))
    deleted = bool(re.match(r"^(?:삭제|제\s*\d+\s*조(?:\s*의\s*\d+)?\s*삭제)", head) or (re.match(r"^<?\s*삭\s*제\b", rest) and not u.get("항")))
    if num == 0 and supp_idx:
        sfx = f"{supp_idx}" if multi_supp else ""
        aid, disp = f"{rid}_SUPP{sfx}_PRE", f"{reg['display']}-부칙{sfx}"
    elif supp_idx:
        sid = f"_SUPP{supp_idx}" if multi_supp else "_SUPP"
        aid = f"{rid}{sid}_ART_{num}" + (f"_{br}" if br else "")
        disp = f"{reg['display']}-부칙" + (f"{supp_idx}" if multi_supp else "") + f"-{lab}"
    else:
        aid, disp = f"{rid}_ART_{num}" + (f"_{br}" if br else ""), f"{reg['display']}-{lab}"
    rec = {
        "id": aid, "display_id": disp, "regulation_id": rid, "regulation_name": reg["title"], "short_name": reg["short"], "document_type": reg["type"],
        "part": ctx["part"], "chapter": ctx["chapter"], "section": ctx["section"], "subsection": ctx["subsection"],
        "article_number": nb, "article_label": lab, "article_title": title,
        "is_deleted": deleted, "is_supplementary": bool(supp_idx), "supplement_no": supp_idx or None,
        "is_scheduled": False, "scheduled_effective_date": "", "variant_of": None, "version_note": "",
        "paragraphs": [], "notes": list(notes or []),
    }
    if paras_override is not None:                            # 부칙: 이미 만들어진 항 목록
        rec["paragraphs"] = paras_override
    else:
        hangs = [h for h in listify(u.get("항")) if isinstance(h, dict)]
        numbered = [h for h in hangs if str(h.get("항번호") or "").strip()]
        if deleted and not hangs:
            rec["paragraphs"].append({"id": aid + "_P0", "display_id": disp, "paragraph_number": "", "paragraph_index": None, "content": rest or "삭제", "items": []})
        else:
            first = clean(numbered[0].get("항내용", "")) if numbered else ""
            lead = ""
            if rest and not (numbered and first and (first.lstrip("①").strip().startswith(rest[:20]) or rest.startswith(first[:20].lstrip("①").strip()))):
                lead = rest
            if lead and re.fullmatch(r"(?:<[^<>]*>\s*)+", lead):   # 제목 줄 끝의 개정 표지만 있으면 notes 로
                rec["notes"].append(lead)
                lead = ""
            for h in hangs:
                hn = str(h.get("항번호") or "").strip()
                if not hn:                                       # 항 번호 없는 항(본문 + 호)
                    pid = aid + "_P0"
                    body = clean(h.get("항내용", ""))
                    content = (lead + "\n" + body) if (lead and body) else (lead or body)
                    rec["paragraphs"].append({"id": pid, "display_id": disp, "paragraph_number": "", "paragraph_index": None, "content": content,
                                              "items": sub_records(rid, pid, disp, 0, h.get("호"))})
                    lead = ""
                    continue
                if lead:                                         # 항 앞에 따로 본문이 있는 경우
                    rec["paragraphs"].append({"id": aid + "_P0", "display_id": disp, "paragraph_number": "", "paragraph_index": None, "content": lead, "items": []})
                    lead = ""
                mm = re.fullmatch(r"<(\d+)>", hn)                 # 법제처 응답이 ⑯⑰⑱ 을 '<16>' 처럼 내보내는 경우
                if mm and int(mm.group(1)) in PARA_CH:
                    hn = PARA_CH[int(mm.group(1))]
                pidx = PARA_NO.get(hn)
                if not pidx:
                    raise ValueError(f"항 번호를 해석하지 못함: {aid} {hn!r}")
                pid = aid + f"_P{pidx}"
                rec["paragraphs"].append({"id": pid, "display_id": disp + f"-제{pidx}항", "paragraph_number": hn, "paragraph_index": pidx,
                                          "content": strip_mark(h.get("항내용", ""), PARA_MARK), "items": sub_records(rid, pid, disp + f"-제{pidx}항", pidx, h.get("호"))})
            if not rec["paragraphs"]:
                rec["paragraphs"].append({"id": aid + "_P0", "display_id": disp, "paragraph_number": "", "paragraph_index": None, "content": lead or rest, "items": []})
    if paras_override is None and not any(p["content"].strip() or p["items"] for p in rec["paragraphs"]) and rec["notes"]:
        rec["paragraphs"] = [{"id": aid + "_P0", "display_id": disp, "paragraph_number": "", "paragraph_index": None, "content": " ".join(rec["notes"]), "items": []}]
        rec["notes"] = []                                   # 본문이 없는 이동·자리표시 조문은 참고자료 문구가 곧 내용
    rec.update({"effective_date": reg["effective_date"], "revision_date": reg["revision_date"], "source_file": reg["source_file"], "source_path": reg["source_path"],
                "pdf_path": "", "source_page": ""})
    txt = art_plain(rec)
    tags = TAG_RE.findall(txt) + list(rec["notes"])
    rec["amendments"] = sorted(set(tags), key=tags.index)
    ds = [d for t in tags for d in parse_dates(t)]
    rec["last_amended"] = max(ds) if ds else ""
    return rec


def parse_supplement(lines):
    """부칙 한 건의 줄들 → [{num, br, title, paras:[{no, text, items:[{no, br, text, subs}]}]}]
    제목 줄(제N조(…))은 들여쓰기 없는 줄만 인정한다(들여쓴 '제7조를 다음과 같이 한다.' 같은 개정 인용문은 앞 항의 이어지는 글). 번호가 겹치면 이어지는 글로 본다."""
    arts, cur = [], None
    seen = set()
    for raw in lines:
        t = clean(raw)
        if not t:
            continue
        flush_left = raw == raw.lstrip()
        m = re.match(r"^제\s*(\d+)\s*조(?:\s*의\s*(\d+))?\s*\(([^)]*)\)\s*(.*)$", t, re.S) if flush_left else None
        if m and (int(m.group(1)), m.group(2) or "") not in seen:
            cur = {"num": int(m.group(1)), "br": m.group(2) or "", "title": m.group(3), "paras": []}
            seen.add((cur["num"], cur["br"]))
            arts.append(cur)
            t = m.group(4).strip()
            if not t:
                continue
        elif cur is None:
            cur = {"num": 0, "br": "", "title": "", "paras": []}
            arts.append(cur)
        paras = cur["paras"]
        pm = PARA_MARK.match(t)
        im = ITEM_MARK.match(t)
        sm = SUB_MARK.match(t)
        pno = (pm.group(1) or PARA_CH.get(int(pm.group(2)), "")) if pm else ""
        if pm and pno and all(p_["no"] != pno for p_ in paras):
            paras.append({"no": pno, "text": t[pm.end():], "items": []})
        elif im and paras and not re.match(r"^\d+\.\s*\d", t) and all((i_["no"], i_["br"]) != (im.group(1), im.group(2) or "") for i_ in paras[-1]["items"]):
            paras[-1]["items"].append({"no": im.group(1), "br": im.group(2) or "", "text": t[im.end():], "subs": []})
        elif sm and paras and paras[-1]["items"] and sm.group(1) in HAN_NO and all((s_["no"], s_["br"]) != (sm.group(1), sm.group(2) or "") for s_ in paras[-1]["items"][-1]["subs"]):
            paras[-1]["items"][-1]["subs"].append({"no": sm.group(1), "br": sm.group(2) or "", "text": t[sm.end():]})
        elif paras:
            tgt = paras[-1]
            if tgt["items"]:
                it = tgt["items"][-1]
                if it["subs"]:
                    it["subs"][-1]["text"] += "\n" + t
                else:
                    it["text"] += "\n" + t
            else:
                tgt["text"] += "\n" + t
        else:
            paras.append({"no": "", "text": t, "items": []})
    return arts


def supplement_records(reg, supps):
    multi = len(supps) > 1
    out = []
    for si, s in enumerate(supps, 1):
        lines = []
        for blk in listify(s.get("부칙내용")):
            lines += listify(blk) if isinstance(blk, list) else [blk]
        header = clean(lines[0]) if lines else "부칙"
        body = lines[1:] if lines and clean(lines[0]).startswith("부칙") else lines
        for a in parse_supplement(body):
            aid_base = None
            paras = []
            for pn in a["paras"]:
                pno = pn["no"]
                pidx = PARA_NO.get(pno) if pno else None
                tmp = {"num": a["num"], "br": a["br"]}
                # ID 는 build_article 이 정하므로 임시로 자리만 만든다
                paras.append((pno, pidx, pn))
            u = {"조문번호": str(a["num"]), "조문가지번호": a["br"], "조문제목": a["title"], "조문내용": (f"제{a['num']}조" + (f"의{a['br']}" if a["br"] else "") + (f"({a['title']})" if a["title"] else "")) if a["num"] else ""}
            rec = build_article(reg, u, {"part": "", "chapter": "", "section": "", "subsection": ""}, supp_idx=si, multi_supp=multi, paras_override=[])
            aid, disp = rec["id"], rec["display_id"]
            for pno, pidx, pn in paras:
                pid = aid + (f"_P{pidx}" if pidx else "_P0")
                pd = {"id": pid, "display_id": disp + (f"-제{pidx}항" if pidx else ""), "paragraph_number": pno, "paragraph_index": pidx, "content": clean(pn["text"]), "items": []}
                for it in pn["items"]:
                    iid = pid + f"_I{it['no']}" + (f"_{it['br']}" if it["br"] else "")
                    idisp = pd["display_id"] + f"-제{it['no']}호" + (f"의{it['br']}" if it["br"] else "")
                    itd = {"id": iid, "display_id": idisp, "item_number": it["no"] + (f"의{it['br']}" if it["br"] else ""), "content": clean(it["text"]), "subitems": []}
                    for sb in it["subs"]:
                        itd["subitems"].append({"id": iid + f"_S{HAN_NO[sb['no']]}" + (f"_{sb['br']}" if sb["br"] else ""),
                                                "display_id": idisp + f"-{sb['no']}목" + (f"의{sb['br']}" if sb["br"] else ""), "subitem_number": sb["no"] + (f"의{sb['br']}" if sb["br"] else ""),
                                                "content": clean(sb["text"])})
                    pd["items"].append(itd)
                rec["paragraphs"].append(pd)
            if not rec["paragraphs"]:
                rec["paragraphs"].append({"id": aid + "_P0", "display_id": disp, "paragraph_number": "", "paragraph_index": None, "content": "", "items": []})
            txt = art_plain(rec)
            tags = TAG_RE.findall(txt)
            rec["amendments"] = sorted(set(tags), key=tags.index)
            ds = [d for t in tags for d in parse_dates(t)]
            rec["last_amended"] = max(ds) if ds else ""
            rec["supplement_header"] = header
            out.append(rec)
    return out


def api_to_records(reg, doc):
    """API 한 법령 → (메타 보충값, 조문 레코드 목록, 별표 레코드 목록)"""
    law = doc["법령"]
    info = law["기본정보"]
    ctx = {"part": "", "chapter": "", "section": "", "subsection": ""}
    recs = []
    for u in listify(law["조문"]["조문단위"]):
        kind = u.get("조문여부")
        text = clean(u.get("조문내용") or "")
        if kind == "전문":
            for key, rx in LEVELS:
                if rx.match(text):
                    ctx[key] = text
                    i = [k for k, _ in LEVELS].index(key)
                    for lower, _ in LEVELS[i + 1:]:
                        ctx[lower] = ""
                    break
            continue
        if kind != "조문":
            continue
        notes = [clean(x) for x in re.split(r"\n", str(u.get("조문참고자료") or "")) if clean(x)]
        recs.append(build_article(reg, u, ctx, notes=notes))
    supps = listify((law.get("부칙") or {}).get("부칙단위"))
    recs += supplement_records(reg, supps)
    annexes = []
    for b in listify((law.get("별표") or {}).get("별표단위")):
        raw_no = re.sub(r"\D", "", str(b.get("별표번호") or "")) or "0"
        sub = re.sub(r"\D", "", str(b.get("별표키") or ""))[4:6]          # 별표키 '000100E' = 번호 4자리 + 가지번호 2자리 + 종류
        no = str(int(raw_no))
        sub = str(int(sub)) if sub and int(sub) else ""
        number = ("" if no == "0" else no) + (f"의{sub}" if sub else "")
        lines = []
        for blk in listify(b.get("별표내용")):
            lines += [str(x).rstrip() for x in (blk if isinstance(blk, list) else [blk])]
        content = html.unescape("\n".join(lines).strip("\n"))
        title = clean(b.get("별표제목") or "")
        form = str(b.get("별표키") or "").endswith("F")                   # 별표키 끝 글자: E=별표, F=서식(별지)
        kind, abbr = ("별지", "FORM") if form else ("별표", "ANNEX")
        deleted = bool(re.match(r"^삭제\b", title))
        aid = f"{reg['id']}_{abbr}" + (f"_{no}" if no != "0" else "") + (f"_{sub}" if sub else "")
        annexes.append({
            "id": aid, "display_id": f"{reg['display']}-{kind}{number}", "regulation_id": reg["id"], "regulation_name": reg["title"], "short_name": reg["short"],
            "document_type": reg["type"], "annex_kind": kind, "annex_number": number, "annex_title": title, "title_source": "api",
            "is_deleted": deleted, "pages": 0, "content": "" if deleted else content, "content_blocks": [] if deleted else [{"type": "text", "page": 1, "text": content}], "content_format": "blocks",
            "related_articles": [], "related_regulations": [],
            "effective_date": reg["effective_date"], "revision_date": reg["revision_date"], "source_file": reg["source_file"], "source_path": reg["source_path"],
            "pdf_path": "", "source_page": "", "keywords": [],
        })
    return recs, annexes



# ---------------------------------------------------------------- 행정규칙(admrul) → 법령 API 와 같은 모양(항·호·목)으로 바꾼 뒤 같은 변환을 쓴다
HAN_ORDER = {c: i for i, c in enumerate("가나다라마바사아자차카타파하", 1)}
ITEM_RX = re.compile(r"(?<![0-9])(\d{1,2})(?:의(\d{1,2}))?\.(?=[\s\"“‘'「(<＜\[가-힣A-Za-z])")
SUB_RX = re.compile(r"(?<![가-힣])([가-하])(?:의(\d{1,2}))?\.(?=[\s\"“‘'「(<＜\[])")


def _first_ok(before):
    """번호 1(가) 이 항목의 시작인지: 문장 끝(. : ) 다)이나 글머리 바로 뒤여야 하고, '2012. 1. 1.' 의 날짜 조각이면 안 됨"""
    b = before.rstrip()
    if not b:
        return True
    if re.search(r"\d\.$", b):
        return False
    return b[-1] in ".:;)”\"'다음함임것>]"


def split_paras(text):
    """'… ① … ② …' → (앞 글, [(번호 글자, 글])). 원문자를 ①부터 차례로 찾는다."""
    found, pos = [], 0
    for k in range(1, len(PARA_CH) + 1):
        i = text.find(PARA_CH[k], pos)
        if i < 0:
            break
        found.append((i, PARA_CH[k]))
        pos = i + 1
    if not found or found[0][1] != "①":
        return text, []
    lead = text[:found[0][0]].strip()
    out = []
    for n, (i, ch) in enumerate(found):
        j = found[n + 1][0] if n + 1 < len(found) else len(text)
        out.append((ch, text[i + 1:j].strip()))
    return lead, out


def split_items(text):
    """'… 다.1. … 2. …' → (앞 글, [(번호, 가지번호, 글])). 번호는 1부터 차례로(또는 같은 번호의 의N)만 인정."""
    picked, last, lastbr = [], 0, 0
    for m in ITEM_RX.finditer(text):
        n, br = int(m.group(1)), m.group(2)
        if not picked:
            if n == 1 and not br and _first_ok(text[:m.start()]):
                picked.append((m.start(), m.end(), 1, ""))
                last, lastbr = 1, 0
        elif not br and n == last + 1:
            picked.append((m.start(), m.end(), n, ""))
            last, lastbr = n, 0
        elif br and n == last and int(br) == lastbr + 1:
            picked.append((m.start(), m.end(), n, br))
            lastbr = int(br)
    if not picked:
        return text, []
    lead = text[:picked[0][0]].strip()
    out = []
    for k, (st, en, n, br) in enumerate(picked):
        j = picked[k + 1][0] if k + 1 < len(picked) else len(text)
        out.append((n, br, text[en:j].strip()))
    return lead, out


def split_subs(text):
    """'… 가. … 나. …' → (앞 글, [(글자, 가지번호, 글]))"""
    picked, last = [], 0
    for m in SUB_RX.finditer(text):
        h, br = m.group(1), m.group(2)
        idx = HAN_ORDER.get(h, 0)
        if not picked:
            if h == "가" and not br and _first_ok(text[:m.start()]):
                picked.append((m.start(), m.end(), h, ""))
                last = 1
        elif not br and idx == last + 1:
            picked.append((m.start(), m.end(), h, ""))
            last = idx
    if not picked:
        return text, []
    lead = text[:picked[0][0]].strip()
    out = []
    for k, (st, en, h, br) in enumerate(picked):
        j = picked[k + 1][0] if k + 1 < len(picked) else len(text)
        out.append((h, br, text[en:j].strip()))
    return lead, out


def _ho_dicts(text):
    """항 안의 글 → (본문, API 모양의 호 목록)"""
    lead, items = split_items(text)
    hos = []
    for n, br, body in items:
        main, subs = split_subs(body)
        ho = {"호번호": f"{n}.", "호내용": f"{n}" + (f"의{br}" if br else "") + ". " + (main if subs else body)}
        if br:
            ho["호가지번호"] = br
        if subs:
            ho["목"] = [{"목번호": f"{h}.", "목내용": f"{h}. {t}"} for h, _b, t in subs]
        hos.append(ho)
    return lead, hos


def adm_article_unit(text):
    """행정규칙 조문 문자열 한 건('제3조(제목) ① … ② …') → 법령 API 조문단위 모양의 dict"""
    m = re.match(r"^제\s*(\d+)\s*조(?:\s*의\s*(\d+))?(조)?\s*(?:\(([^)]*)\))?\s*(.*)$", text.strip(), re.S)
    if not m:
        return None
    num, br, title, body = m.group(1), m.group(2) or "", clean(m.group(4) or ""), m.group(5).strip()
    head = f"제{num}조" + (f"의{br}" if br else "") + (f"({title})" if title else "")
    u = {"조문번호": num, "조문가지번호": br, "조문제목": title, "조문내용": head, "조문여부": "조문"}
    if m.group(3):                                           # 원문 오기('제26조의6조(…)')는 그대로 두지 않고 참고 표지로 남김
        u["조문참고자료"] = f"[원문 표기: 제{num}조" + (f"의{br}" if br else "") + "조]"
    if re.match(r"^<?\s*삭\s*제", body) or not body:
        u["조문내용"] = head + (" " + body if body else "")
        return u
    lead, paras = split_paras(body)
    hang = []
    if lead:
        lt, hos = _ho_dicts(lead)
        h0 = {"항내용": lt}
        if hos:
            h0["호"] = hos
        hang.append(h0)
    for ch, ptext in paras:
        pt, hos = _ho_dicts(ptext)
        h = {"항번호": ch, "항내용": f"{ch} {pt}".strip()}
        if hos:
            h["호"] = hos
        hang.append(h)
    if hang:
        u["항"] = hang
    return u


def adm_supplement_lines(raw):
    """부칙내용 문자열 한 건('부칙 <제5909호, 2026. 9. 21.>제1조(시행일) … 제2조(…) …') → 줄 목록(법령 부칙내용과 같은 모양)"""
    m = re.match(r"^\s*(부칙\s*<[^>]*>)\s*", raw)
    header = clean(m.group(1)) if m else "부칙"
    body = raw[m.end():] if m else raw
    lines = [header]
    cands = [(x.start(), x.end(), int(x.group(1)), x.group(2) or "", x.group(3)) for x in re.finditer(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?\s*\(([^)]*)\)", body)]
    arts, last = [], (0, 0)
    for st, en, n, br, title in cands:
        cur = (n, int(br or 0))
        ok = (n == last[0] + 1 and not br) or (n == last[0] and br and int(br) == last[1] + 1) or (not arts and n == 1)
        if ok and (st == 0 or _first_ok(body[:st]) or body[:st].rstrip().endswith(">")):
            arts.append((st, en, n, br, title))
            last = cur
    if not arts:
        if body.strip():
            lines.append(clean(body))
        return lines
    if body[:arts[0][0]].strip():
        lines.append(clean(body[:arts[0][0]]))
    for k, (st, en, n, br, title) in enumerate(arts):
        j = arts[k + 1][0] if k + 1 < len(arts) else len(body)
        text = body[en:j].strip()
        lead, paras = split_paras(text)
        l0, hos = _ho_dicts(lead)
        lines.append(f"제{n}조" + (f"의{br}" if br else "") + f"({title}) {l0}".rstrip())
        for ho in hos:
            lines.append("    " + ho["호내용"])
            for mk in ho.get("목", []):
                lines.append("      " + mk["목내용"])
        for ch, pt in paras:
            p0, phos = _ho_dicts(pt)
            lines.append(f"  {ch} {p0}".rstrip())
            for ho in phos:
                lines.append("    " + ho["호내용"])
                for mk in ho.get("목", []):
                    lines.append("      " + mk["목내용"])
    return lines


def admrul_to_records(reg, doc):
    """행정규칙 API 한 건 → (조문 레코드 목록, 별표 레코드 목록[없음])"""
    root = doc["AdmRulService"]
    ctx = {"part": "", "chapter": "", "section": "", "subsection": ""}
    recs = []
    for text in listify(root.get("조문내용")):
        t = clean(text) if not isinstance(text, str) else text.strip()
        if not t:
            continue
        if not re.match(r"^제\s*\d+\s*조", t):
            for key, rx in LEVELS:
                if rx.match(clean(t)):
                    ctx[key] = clean(t)
                    i = [k for k, _ in LEVELS].index(key)
                    for lower, _ in LEVELS[i + 1:]:
                        ctx[lower] = ""
                    break
            continue
        u = adm_article_unit(t)
        if u:
            recs.append(build_article(reg, u, ctx, notes=[u["조문참고자료"]] if u.get("조문참고자료") else None))
    bu = root.get("부칙") or {}
    supps = [{"부칙내용": [adm_supplement_lines(x)]} for x in listify(bu.get("부칙내용"))]
    recs += supplement_records(reg, supps)
    return recs, []


def admrul_reg_meta(entry, doc):
    """registry-api.json 항목 + 행정규칙 기본정보 → 규정 dict"""
    info = doc["AdmRulService"]["행정규칙기본정보"]
    dept = re.match(r"^([^()]*)(?:\(([^)]*)\))?", clean(info.get("담당부서기관명") or ""))
    ministry = clean(info.get("소관부처명") or "")
    agencies = [{"agency": (dept.group(1).strip() if dept and dept.group(1).strip() else ministry), "department": (dept.group(2) or "").strip() if dept else ""}] if ministry else []
    eff, rev = d8(info.get("시행일자")), d8(info.get("발령일자"))
    number = f"제{info['발령번호']}호" if info.get("발령번호") else ""
    reg = dict(entry)
    reg.update({
        "name": entry["title"], "issuer_form": f"{ministry}{info.get('행정규칙종류') or ''}", "number": number, "agencies": agencies,
        "effective_date": eff, "revision_date": rev, "revision_type": info.get("제개정구분명") or "",
        "source_file": entry["api_file"], "source_path": "원문/법제처API/" + entry["api_file"], "pdf_path": "", "pages": 0, "header_raw": [], "outline": [], "format": "article",
        "source_url": f"https://www.law.go.kr/admRulInfoP.do?admRulSeq={entry['api_mst']}" if entry.get("api_mst") else "https://www.law.go.kr/행정규칙/" + entry["title"],
    })
    reasons = [f"시행 예정(시행일 {eff} > 기준일 {TODAY})"] if eff and eff > TODAY else []
    reg["version"] = eff + ("-" + re.sub(r"\D", "", number) if number else "")
    reg["needs_review"] = bool(reasons)
    reg["review_reasons"] = reasons + list(entry.get("note") or [])      # note: 화면에 ℹ 로 보이는 안내(검토 필요 표시는 아님)
    reg["upcoming"] = bool(eff and eff > TODAY)
    return reg


def api_reg_meta(entry, doc):
    """registry-api.json 항목 + API 기본정보 → 규정 dict(빌드의 reg 와 같은 키)"""
    info = doc["법령"]["기본정보"]
    dept = info.get("연락부서") or {}
    dept = (dept.get("부서단위") if isinstance(dept, dict) else dept) or {}
    if isinstance(dept, list):
        dept = dept[0] if dept else {}
    ministry = info["소관부처"]["content"] if isinstance(info.get("소관부처"), dict) else str(info.get("소관부처") or "")
    agencies = [{"agency": ministry, "department": clean(dept.get("부서명", ""))}] if ministry else []
    eff, rev = d8(info.get("시행일자")), d8(info.get("공포일자"))
    number = f"제{info['공포번호']}호" if info.get("공포번호") else ""
    reg = dict(entry)
    reg.update({
        "name": entry["title"], "issuer_form": info["법종구분"]["content"] if isinstance(info.get("법종구분"), dict) else str(info.get("법종구분") or ""),
        "number": number, "agencies": agencies, "effective_date": eff, "revision_date": rev, "revision_type": info.get("제개정구분") or "",
        "source_file": entry["api_file"], "source_path": "원문/법제처API/" + entry["api_file"], "pdf_path": "", "pages": 0, "header_raw": [], "outline": [],
        "format": "article",
        "source_url": (f"https://www.law.go.kr/lsInfoP.do?lsiSeq={entry['api_mst']}" if entry.get("api_mst") else "https://www.law.go.kr/법령/" + entry["title"]),
    })
    reasons = []
    if eff and eff > TODAY:
        reasons.append(f"시행 예정(시행일 {eff} > 기준일 {TODAY})")
    reg["version"] = eff + ("-" + re.sub(r"\D", "", number) if number else "")
    reg["needs_review"] = bool(reasons)
    reg["review_reasons"] = reasons + list(entry.get("note") or [])      # note: 화면에 ℹ 로 보이는 안내(검토 필요 표시는 아님)
    reg["upcoming"] = bool(eff and eff > TODAY)
    return reg


# ---------------------------------------------------------------- 인용·키워드 (build_regdb.py 의 3)·4) 단계와 같은 계산)
def make_resolver(cfgs):
    """no_resolve 규정(같은 법의 다른 본 — 예: 현행본/시행 예정본)은 이름 풀이 대상에서 빼서 이름이 겹쳐 인용 해석이 모호해지는 것을 막는다.
    다만 그 규정 자신의 조문 안 인용('이 법 제5조', '제5조')은 해석하도록 규정 목록에는 남기고, family(예: 'X_ENFORCEMENT')를 알려 준다."""
    nr = [c for c in cfgs if c.get("no_resolve")]
    r = Resolver([c for c in cfgs if not c.get("no_resolve")])
    for c in nr:
        r.regs[c["id"]] = c
        r.family[c["id"]] = c.get("family")
    return r


def derive_citations(resolver, regs_by_id, records):
    """records: {rid: [rec]} → 각 rec 에 related_articles / related_regulations / external_references 를 채우고 간선을 돌려준다"""
    index_of = {rid: {r["article_number"]: r["id"] for r in recs if not r["is_supplementary"] and not r["is_scheduled"]} for rid, recs in records.items()}
    edges = {}
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
                if not tid or tid == rec["id"]:
                    continue
                if tid not in seen_a:
                    seen_a.append(tid)
                if tgt != rid and tgt not in seen_r:
                    seen_r.append(tgt)
            rec["related_articles"], rec["related_regulations"], rec["external_references"] = seen_a, seen_r, seen_e
            for t in seen_a:
                edges[(rec["id"], t)] = 1
    return edges


class Keywords:
    def __init__(self, kw_cfg):
        self.cfg = kw_cfg
        self.terms = [(t["term"], sorted({ns(n) for n in [t["term"]] + t["aliases"]}, key=len, reverse=True)) for t in kw_cfg]
        self.short_re = {term: [re.compile(r"(?<![가-힣])" + re.escape(f)) for f in forms if len(f) <= 3] for term, forms in self.terms}
        self.df = {t: 0 for t, _ in self.terms}

    def count(self, title_text, body_text, bump=True):
        tt, bb = ns(title_text), ns(body_text)
        t1, b1 = re.sub(r"\s+", " ", title_text), re.sub(r"\s+", " ", body_text)
        out = []
        for term, forms in self.terms:
            long_forms = [f for f in forms if len(f) > 3]
            in_t = any(f in tt for f in long_forms) or any(r.search(t1) for r in self.short_re[term])
            c = sum(bb.count(f) for f in long_forms) + sum(len(r.findall(b1)) for r in self.short_re[term])
            if in_t or c:
                out.append((term, in_t, c))
                if bump:
                    self.df[term] += 1
        return out

    def pick(self, rows, n_total):
        sc = []
        for term, in_t, c in rows:
            common = self.df[term] / n_total
            if in_t or (c >= 2 and common < 0.12):
                sc.append((term, 1000 * in_t + c * math.log(1 + 1 / max(common, 0.001))))
        sc.sort(key=lambda x: -x[1])
        return [t for t, _ in sc[:6]]


# ---------------------------------------------------------------- 메인
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="파일을 쓰지 않고 기존 규정에 같은 계산을 적용해 저장된 값과 비교")
    ap.add_argument("--refresh-existing", action="store_true", help="기존(PDF) 규정 조문의 인용 파생값(related_*·external_references)을 새 규정까지 포함해 다시 계산해 저장(키워드·본문은 그대로)")
    a = ap.parse_args()

    reg_cfg = jload(os.path.join(OUT, "config", "registry.json"))["regulations"]
    api_cfg = jload(os.path.join(OUT, "config", "registry-api.json"))["regulations"]
    kw_cfg = jload(os.path.join(OUT, "config", "keyword-dictionary.json"))["terms"]
    api_ids = {r["id"] for r in api_cfg}

    regs_json = jload(os.path.join(OUT, "data", "regulations.json"))
    base_regs = [r for r in regs_json["regulations"] if r["id"] not in api_ids]
    n_base = len(base_regs)
    # 기존 규정(PDF 기반)의 레코드
    base_records, base_annexes = {}, {}
    for r in base_regs:
        d = os.path.join(OUT, "regulations", r["slug"])
        base_records[r["id"]] = jload(os.path.join(d, "articles.json"))
        base_annexes[r["id"]] = jload(os.path.join(d, "annexes.json"))
    cfg_by_id = {c["id"]: c for c in reg_cfg}
    base_cfg = [cfg_by_id[r["id"]] for r in base_regs]

    kw = Keywords(kw_cfg)
    if a.check:
        return check_existing(base_cfg, base_records, kw, api_cfg)

    # 새 규정 변환
    new_regs, new_records, new_annexes = [], {}, {}
    for e in api_cfg:
        doc = jload(os.path.join(SRC_DIR, e["api_file"]))
        if e.get("api_kind") == "admrul":
            reg = admrul_reg_meta(e, doc)
            recs, ann = admrul_to_records(reg, doc)
        else:
            reg = api_reg_meta(e, doc)
            recs, ann = api_to_records(reg, doc)
        new_regs.append(reg)
        new_records[reg["id"]] = recs
        new_annexes[reg["id"]] = ann

    # 인용: 기존 규정 + 새 규정 전체를 대상으로 새 규정의 조문만 계산
    all_cfg = base_cfg + new_regs
    resolver = make_resolver(all_cfg)
    index_all = {**base_records, **new_records}
    # derive_citations 는 넘긴 전체 규정의 조 번호 색인을 쓰므로 기존+새 규정을 모두 넘기고, 기존 규정의 값은 계산 뒤에 되돌린다
    saved = {rid: [(r["related_articles"], r["related_regulations"], r["external_references"]) for r in recs] for rid, recs in base_records.items()}
    derive_citations(resolver, {c["id"]: c for c in all_cfg}, index_all)
    changed_base = {}
    for rid, recs in base_records.items():
        diff = [i for i, (r, s_) in enumerate(zip(recs, saved[rid])) if (r["related_articles"], r["related_regulations"], r["external_references"]) != s_]
        if a.refresh_existing:
            if diff:
                changed_base[rid] = len(diff)
        else:                                               # 기본 동작: 기존 규정의 값은 그대로 되돌림
            for r, s_ in zip(recs, saved[rid]):
                r["related_articles"], r["related_regulations"], r["external_references"] = s_
    if a.refresh_existing:
        n_lost = sum(len(set(s_[0]) - set(r["related_articles"])) for rid in base_records for r, s_ in zip(base_records[rid], saved[rid]))
        n_new = sum(len(set(r["related_articles"]) - set(s_[0])) for rid in base_records for r, s_ in zip(base_records[rid], saved[rid]))
        print(f"기존 조문 갱신: {sum(changed_base.values())}개 조문({len(changed_base)}개 규정), 새 링크 {n_new}개, 사라진 링크 {n_lost}개")
    edges = {(r["id"], t): 1 for recs in index_all.values() for r in recs for t in r["related_articles"]}   # 저장된 related_articles 로 간선을 만든다(기존 규정은 저장값 그대로)

    # 키워드: 새 규정의 조문(기존 조문의 값은 그대로)
    stats = {}
    for rid, recs in base_records.items():
        for r in recs:
            stats[r["id"]] = kw.count(r["article_title"], art_plain(r))
    for rid, recs in new_records.items():
        for r in recs:
            stats[r["id"]] = kw.count(r["article_title"], art_plain(r))
    n_total = max(1, len(stats))
    for rid, recs in new_records.items():
        for r in recs:
            r["keywords"] = kw.pick(stats[r["id"]], n_total)
    for rid, ann in new_annexes.items():
        for x in ann:
            x["keywords"] = kw.pick(kw.count(x["annex_title"], x["annex_title"] + " " + x["content"]), n_total)
            idx = {r["article_number"]: r["id"] for r in new_records[rid] if not r["is_supplementary"]}
            m = re.search(r"\((제\s*\d+\s*조[^)]*?)\s*관련\)", x["annex_title"])
            if m:
                for mm in re.finditer(r"제\s*(\d+)\s*조(?:의\s*(\d+))?", m.group(1)):
                    t = idx.get(mm.group(1) + (f"의{mm.group(2)}" if mm.group(2) else ""))
                    if t and t not in x["related_articles"]:
                        x["related_articles"].append(t)

    write_all(regs_json, base_regs, new_regs, new_records, new_annexes, edges, kw_cfg, base_records, changed_base)
    print(f"API 규정 {len(new_regs)}개 · 조문 {sum(len(v) for v in new_records.values())}개 · 별표 {sum(len(v) for v in new_annexes.values())}개 · 인용 링크 {len(edges)}개")
    return 0


def check_existing(base_cfg, base_records, kw, api_cfg=()):
    """저장된 조문에 인용·키워드 계산을 다시 적용해 저장값과 같은지 본다(이 도구가 build_regdb.py 와 같은 결과를 내는지, 편입 뒤 인용이 최신인지 확인)."""
    api_records = {}
    for e in api_cfg:
        p = os.path.join(OUT, "regulations", e["slug"], "articles.json")
        if os.path.exists(p):
            api_records[e["id"]] = jload(p)
    all_cfg = list(base_cfg) + [e for e in api_cfg if e["id"] in api_records]
    records = {**base_records, **api_records}
    resolver = make_resolver(all_cfg)
    saved = {rid: [(list(r["related_articles"]), list(r["related_regulations"]), list(r["external_references"]), list(r["keywords"])) for r in recs] for rid, recs in records.items()}
    derive_citations(resolver, {c["id"]: c for c in all_cfg}, records)
    stats = {r["id"]: kw.count(r["article_title"], art_plain(r)) for recs in base_records.values() for r in recs}   # 키워드는 기존(PDF) 규정만 대조(API 규정은 편입 때 df 가 달라 따로 계산)
    n_total = max(1, len(stats))
    bad_c = bad_k = total = 0
    examples = []
    for rid, recs in records.items():
        for r, s in zip(recs, saved[rid]):
            total += 1
            if (r["related_articles"], r["related_regulations"], r["external_references"]) != s[:3]:
                bad_c += 1
                if len(examples) < 5:
                    examples.append(("citations", r["id"], s[:3], (r["related_articles"], r["related_regulations"], r["external_references"])))
            if rid in base_records:
                kws = kw.pick(stats[r["id"]], n_total)
                if kws != s[3]:
                    bad_k += 1
                    if len(examples) < 5:
                        examples.append(("keywords", r["id"], s[3], kws))
    print(f"조문 {total}개(기존 {sum(len(v) for v in base_records.values())} + API {sum(len(v) for v in api_records.values())}): 인용 불일치 {bad_c}, 키워드 불일치 {bad_k}")
    for e in examples:
        print(e)
    return 0 if not (bad_c or bad_k) else 1


def write_all(regs_json, base_regs, new_regs, new_records, new_annexes, edges, kw_cfg, base_records=None, changed_base=None):
    api_ids = {r["id"] for r in new_regs}
    n_base = len(base_regs)
    new_list = []
    for reg in new_regs:
        rid = reg["id"]
        recs, ann = new_records[rid], new_annexes[rid]
        meta = {
            "schema_version": SCHEMA_VERSION, "id": rid, "name": reg["title"], "short_name": reg["short"], "display_id_prefix": reg["display"], "slug": reg["slug"],
            "aliases": reg["aliases"], "document_type": reg["type"], "format": "article", "issuer_form": reg["issuer_form"], "number": reg["number"], "group": reg["group"],
            "agencies": reg["agencies"], "effective_date": reg["effective_date"], "revision_date": reg["revision_date"], "revision_type": reg["revision_type"],
            "version": reg["version"],
            "versions": [{"version": reg["version"], "effective_date": reg["effective_date"], "revision_date": reg["revision_date"], "number": reg["number"],
                          "source_file": reg["source_file"], "source_path": reg["source_path"], "status": "loaded", "is_latest": "unverified"}],
            "latest_status": "검토 필요" if reg["needs_review"] else "최신본(법제처 API 에서 받은 현행 법령, 받은 날 기준)",
            "needs_review": reg["needs_review"], "review_reasons": reg["review_reasons"], "status": "예정" if reg["upcoming"] else "시행", "upcoming_versions": [],
            "parent_regulation_id": reg.get("parent"), "source_file": reg["source_file"], "source_path": reg["source_path"], "pdf_path": "", "source_pages": 0,
            "header_raw": [], "outline": [], "source": "api", "source_url": reg["source_url"], "api_received": TODAY,
            "counts": {"articles": sum(1 for r in recs if not r["is_supplementary"]), "scheduled_versions": 0, "deleted_articles": sum(1 for r in recs if r["is_deleted"] and not r["is_supplementary"]),
                       "supplementary_articles": sum(1 for r in recs if r["is_supplementary"]), "annexes": len(ann)},
            "files": {"articles": f"regulations/{reg['slug']}/articles.json", "annexes": f"regulations/{reg['slug']}/annexes.json", "markdown": f"regulations/{reg['slug']}/regulation.md"},
        }
        d = os.path.join(OUT, "regulations", reg["slug"])
        jdump(os.path.join(d, "metadata.json"), meta, indent=1)
        jdump(os.path.join(d, "articles.json"), recs)
        jdump(os.path.join(d, "annexes.json"), ann)
        md = render_markdown(meta, recs, ann).replace("원문 PDF 에서 만든 열람용 사본입니다. 원문이 아니라 추출본이므로 중요한 판단은 원본 PDF 로 확인하세요.",
                                                        "법제처 국가법령정보 공동활용 API 에서 받은 법령을 옮긴 열람용 사본입니다. 중요한 판단은 법제처 국가법령정보센터의 현행 법령으로 확인하세요.")
        for p_ in (os.path.join(d, "regulation.md"), os.path.join(OUT, "markdown", reg["slug"] + ".md")):
            os.makedirs(os.path.dirname(p_), exist_ok=True)
            with open(p_, "w", encoding="utf-8", newline="\n") as f:
                f.write(md)
        new_list.append({k: meta[k] for k in ("id", "name", "short_name", "display_id_prefix", "slug", "aliases", "document_type", "format", "issuer_form", "number", "group", "agencies",
                                              "effective_date", "revision_date", "revision_type", "version", "latest_status", "needs_review", "review_reasons", "status", "upcoming_versions",
                                              "parent_regulation_id", "source_file", "source_path", "pdf_path", "source_pages", "counts", "files", "source_url")})

    for rid in (changed_base or {}):                          # --refresh-existing: 인용 파생값이 바뀐 기존 규정의 조문 파일만 다시 쓴다
        slug = next(r["slug"] for r in base_regs if r["id"] == rid)
        jdump(os.path.join(OUT, "regulations", slug, "articles.json"), base_records[rid])

    # data/regulations.json
    reg_list = base_regs + new_list
    reg_idx = {r["id"]: i for i, r in enumerate(reg_list)}

    # regulation-index.json
    idx_path = os.path.join(OUT, "data", "regulation-index.json")
    idx = jload(idx_path)
    idx_regs = [r for r in idx["regulations"] if r["id"] not in api_ids]
    assert len(idx_regs) == n_base, "기존 규정 순번이 달라짐 — build_regdb.py 를 다시 실행했다면 merge_api_regs.py 를 이어서 실행하세요"
    arts = [r for r in idx["articles"] if r["r"] < n_base]
    anns = [r for r in idx["annexes"] if r["r"] < n_base]
    if changed_base:                                          # 기존 조문 행의 rr(인용한 다른 규정 순번)만 갱신 — 키 순서는 그대로 유지
        recs_by_id = {r["id"]: r for rid in changed_base for r in base_records[rid]}
        for i, row in enumerate(arts):
            rec = recs_by_id.get(row["id"])
            if rec is None:
                continue
            rr = [reg_idx[x] for x in rec["related_regulations"] if x in reg_idx]
            new_row = {}
            for k, v in row.items():
                if k == "rr":
                    continue
                new_row[k] = v
                if k == "c" and rr:
                    new_row["rr"] = rr
            arts[i] = new_row
    for reg in new_regs:
        rid = reg["id"]
        for r in new_records[rid]:
            row = {"id": r["id"], "r": reg_idx[rid], "n": r["article_label"], "t": r["article_title"], "k": r["keywords"], "c": r["chapter"] or r["part"]}
            if r["related_regulations"]:
                row["rr"] = [reg_idx[x] for x in r["related_regulations"] if x in reg_idx]
            if r["is_deleted"]:
                row["x"] = 1
            if r["is_supplementary"]:
                row["s"] = r["supplement_no"]
            arts.append(row)
        for x in new_annexes[rid]:
            anns.append({"id": x["id"], "r": reg_idx[rid], "kind": x["annex_kind"], "n": x["annex_number"], "t": x["annex_title"], "k": x["keywords"], **({"x": 1} if x["is_deleted"] else {})})
    for r in new_list:
        idx_regs.append({"id": r["id"], "name": r["name"], "short_name": r["short_name"], "slug": r["slug"], "aliases": r["aliases"], "document_type": r["document_type"], "group": r["group"],
                         "effective_date": r["effective_date"], "revision_date": r["revision_date"], "number": r["number"], "display_id_prefix": r["display_id_prefix"], "status": r["status"],
                         "needs_review": r["needs_review"], "review_reasons": r["review_reasons"], "latest_status": r["latest_status"], "source_file": r["source_file"], "source_path": r["source_path"],
                         "source_url": r["source_url"], "articles": r["counts"]["articles"], "annexes": r["counts"]["annexes"]})
    build_id = hashlib.sha1(json.dumps([r["id"] for r in arts] + [r["version"] for r in reg_list]).encode()).hexdigest()[:10]
    jdump(os.path.join(OUT, "data", "regulations.json"), {"schema_version": SCHEMA_VERSION, "generated": TODAY, "build_id": build_id, "regulations": reg_list}, indent=1)
    idx.update({"generated": TODAY, "build_id": build_id, "regulations": idx_regs, "articles": arts, "annexes": anns})
    jdump(idx_path, idx)

    # keywords.json
    kpath = os.path.join(OUT, "data", "keywords.json")
    kj = jload(kpath)
    inv = {k: [x for x in v if not any(x.startswith(i + "_") for i in api_ids)] for k, v in kj["index"].items()}
    for reg in new_regs:
        for r in new_records[reg["id"]]:
            for t in r["keywords"]:
                inv.setdefault(t, []).append(r["id"])
        for x in new_annexes[reg["id"]]:
            for t in x["keywords"]:
                inv.setdefault(t, []).append(x["id"])
    kj.update({"generated": TODAY, "build_id": build_id,
               "terms": [{"term": t["term"], "category": t["category"], "aliases": t["aliases"], "count": len(inv.get(t["term"], []))} for t in kw_cfg],
               "index": {k: v for k, v in inv.items() if v}})
    jdump(kpath, kj)

    # relations.json
    rpath = os.path.join(OUT, "data", "relations.json")
    rj = jload(rpath)
    all_edges = dict(edges)                                   # 기존 규정 + API 규정의 related_articles 전체에서 만든 간선
    ids = sorted({x for e in all_edges for x in e})
    pos = {x: i for i, x in enumerate(ids)}
    cross = {}
    for (x, y) in all_edges:
        rx, ry = x.split("_ART_")[0].split("_SUPP")[0], y.split("_ART_")[0].split("_SUPP")[0]
        cross[(rx, ry)] = cross.get((rx, ry), 0) + 1
    rj.update({"generated": TODAY, "build_id": build_id,
               "hierarchy": [{"id": r["id"], "parent": r["parent_regulation_id"], "group": r["group"], "document_type": r["document_type"]} for r in reg_list],
               "regulation_references": sorted([{"from": x, "to": y, "count": c} for (x, y), c in cross.items() if x != y], key=lambda z: (-z["count"], z["from"], z["to"])),
               "article_links": {"ids": ids, "edges": sorted([[pos[x], pos[y]] for (x, y) in all_edges])}})
    jdump(rpath, rj)


if __name__ == "__main__":
    sys.exit(main())
