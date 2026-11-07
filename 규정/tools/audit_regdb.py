#!/usr/bin/env python3
"""규정 DB 독립 감사 — 만든 과정(build_regdb.py)과 다른 경로(PyMuPDF 로 PDF 를 다시 읽기)로 DB 를 원문과 대조한다.

  python3 규정/tools/audit_regdb.py        # 문제가 있으면 종료코드 1

검사
  1) 조 번호 목록: PDF 의 '제N조(' 줄머리 목록 ↔ DB 조문 목록 (누락·초과)
  2) 본문 연속 일치: 각 조문(라벨+제목+항·호·목 본문)이 PDF 글자열 안에 이어서 그대로 들어 있는가 (공백 무시)
  3) source_page: 조문 시작이 그 쪽에 실제로 있는가
  4) ID 형식·중복, 인덱스 ↔ 상세 일치, related_* 일관성
  5) 부속: 수·쪽수·PDF 사본 존재
허용하는 차이(정상): 조문 중간에 끼는 [본조신설 …] 같은 표지는 notes 로 옮겼으므로 본문 연속 일치에서 제외한 뒤 비교한다.
"""
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pymupdf  # noqa: E402
from refs import ns  # noqa: E402

R = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "")
OPINION_ID_RE = re.compile(r"^[A-Z0-9_]+_PART\d+(_P\d+)?$")           # 법령해석례: <규정ID>_PART<부분번호>[_P<문단>]
OUTLINE_ID_RE = re.compile(r"^[A-Z_]+?_C\d+(S\d+)?(_SEC\d+)?(_SUB\d+)?(_N\d+|_INTRO)(_P\d+)?(_I\d+)?(_S\d+)?$|^[A-Z_]+?_C\d+(S\d+)?_(ANNEX|FORM|ATTACH)(_\d+)?$")
ID_RE = re.compile(r"^[A-Z_]+?_(ART|SUPP\d*_ART|SUPP\d*_PRE|ANNEX|FORM|ATTACH)(_\d+)*(_P\d+)?(_I\d+(_\d+)?)?(_S\d+(_\d+)?)?(_NEXT\d+)?$|^[A-Z_]+_(ANNEX|SUPP\d*_PRE)$")
TAGS = re.compile(r"\s*<[^<>]*?\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.?[^<>]*>")      # 장·절 제목 뒤 개정 표지는 DB 에서 뺐으므로 비교에서도 뺀다
NOTE_IN_TEXT = re.compile(r"\[(?:본조신설|전문개정|제목개정|본조삭제|조문이동|전부개정|시행일:[^\]]*)[^\]]*\](?:제\d+조[^\[]{0,12})?")


def load(p):
    return json.load(open(R + p, encoding="utf-8"))


def art_text(a):
    t = ns(a["article_label"] + ("(" + a["article_title"] + ")" if a["article_title"] else ""))
    for p in a["paragraphs"]:
        t += ns(p["paragraph_number"] + p["content"])
        for it in p["items"]:
            t += ns(it["item_number"] + "." + it["content"])
            for sb in it["subitems"]:
                t += ns(sb["subitem_number"] + "." + sb["content"])
    return t


def audit_outline(reg, arts, anns):
    """개요형(장·절·번호 목록) 문서: PDF 를 글자 겹침 제거로 다시 읽어, 모든 줄이 DB(조문·부칙·별표 글자·장절 제목)에 들어 있는지 확인"""
    from pdf_lines import page_lines_dedup
    import parse_outline as po
    probs = []
    meta = load("regulations/" + reg["slug"] + "/metadata.json")
    chunks = []
    for a in arts:
        mm = re.search(r"(\d+)\.$", a["article_label"])
        pre = (mm.group(1) + ".") if mm else ""
        chunks.append(ns(pre + a["article_title"]))
        for p in a["paragraphs"]:
            chunks.append(ns((pre if not p["paragraph_number"] else "") + p["paragraph_number"] + p["content"]))
            chunks.append(ns(pre + p["paragraph_number"] + p["content"]))
            for it in p["items"]:
                chunks.append(ns(it["item_number"] + it["content"]))
                chunks += [ns(sb["subitem_number"] + sb["content"]) for sb in it["subitems"]]
        chunks += [ns(x) for x in (a["chapter"], a["section"], a["subsection"])]
    chunks += [ns(o["title"]) for o in meta.get("outline", [])]
    for a in anns:
        chunks.append(ns(a["content"]))
        for b in a["content_blocks"]:
            if b["type"] == "table":
                chunks.append("".join(ns(c) for rw in b["rows"] for c in rw))
    # 기호 글꼴 글자(사용자 영역)는 build 에서 일반 글자로 바뀌므로 양쪽에서 빼고 비교
    KILL = re.compile("[\ue000-\uf8ff□ㆍ『』{}㉑-㉚↓−=]")
    blob = KILL.sub("", "".join(chunks))
    lines = page_lines_dedup(R + reg["pdf_path"])
    by_page = collections.defaultdict(list)
    for r in lines:
        by_page[r["page"]].append(r["text"])
    missing = []
    appx = set()                                               # 부속(별표·서식) 쪽은 표라서 줄 단위 대신 글자수 보존율로 확인(validate_regdb.py)
    for a in anns:
        pg = [int(x) for x in a["source_page"].split("-")]
        appx |= set(range(pg[0], pg[-1] + 1))
    for pn, ts in by_page.items():
        if pn == 1 or pn in appx:
            continue
        heads = sum(1 for t in ts if po.HEAD_RE.match(t) or po.PAGENO.match(t) or "····" in t or re.match(r"^목\s*차$", t))
        if heads >= 0.8 * len(ts) and pn <= 3 or (heads >= 0.8 * len(ts) and len(ts) <= 25 and not any(po.L1.match(t) for t in ts)):
            continue                                           # 목차·장 표지(제목만 나열된 쪽)
        for t in ts:
            s_ = KILL.sub("", ns(t))
            if len(s_) < 3 or po.PAGENO.match(t) or "····" in t or re.match(r"^부\s*칙\s*<.*>$", t):      # 부칙 표제('부칙 <제…호,날짜>')는 부칙 번호로 따로 저장
                continue
            if s_ in ns(reg["short_name"]) or s_ in ns(reg["name"]):      # 쪽마다 붙는 문서 제목(머리글) 조각
                continue
            alts = [s_, re.sub(r"^\((\d{1,2})\)", r"\1)", s_), re.sub(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]\.", "", s_)]      # '(1) …'(→'1) …')·'Ⅳ. 제목'(→제목): 로마숫자 제목·괄호 호 표기 문서
            if not any(a_ in blob for a_ in alts):
                missing.append((pn, t[:50]))
    if missing:
        probs.append(f"[{reg['short_name']}] PDF 줄이 DB 에 없음 {len(missing)}건 — 예: {missing[:4]}")
    return probs


def audit_opinion(reg, arts):
    """법령해석례: PDF 의 모든 줄(쪽 머리글·바닥글·【…】 표제·머리 3줄 제외)이 DB(문단·표제)에 들어 있는지 확인"""
    from pdf_lines import FOOT_RE, page_lines, squash
    import parse_opinion as po
    meta = load("regulations/" + reg["slug"] + "/metadata.json")
    hdr = [ns(x) for x in meta["header_raw"]]
    blob = "".join(ns(a["article_label"]) + "".join(ns(p["content"]) for p in a["paragraphs"]) for a in arts)
    missing = []
    for r in page_lines(R + reg["pdf_path"]):
        t = r["text"].strip()
        s_ = ns(t)
        if len(s_) < 3 or FOOT_RE.match(squash(t)) or po.RUNHEAD_RE.match(t) or po.HEAD_RE.match(t) or po.LAW_RE.match(t) or any(s_ in h or h in s_ for h in hdr):
            continue
        if s_ not in blob:
            missing.append((r["page"], t[:50]))
    probs = [f"[{reg['short_name']}] PDF 줄이 DB 에 없음 {len(missing)}건 — 예: {missing[:4]}"] if missing else []
    if not arts:
        probs.append(f"[{reg['short_name']}] 조문이 없음")
    return probs


def main():
    problems, notes = [], []
    regs = load("data/regulations.json")["regulations"]
    idx = load("data/regulation-index.json")
    allids = set()
    for reg in regs:
        name = reg["short_name"]
        arts, anns = load(reg["files"]["articles"]), load(reg["files"]["annexes"])
        doc = pymupdf.open(R + reg["pdf_path"])
        pages = [p.get_text() for p in doc]
        npages = [ns(p) for p in pages]
        opinion = reg.get("format") == "opinion"
        outline = reg.get("format") in ("outline", "opinion")
        if opinion:
            problems += audit_opinion(reg, arts)
        elif outline:
            problems += audit_outline(reg, arts, anns)
        # 1) 조 번호 목록
        src = collections.Counter()
        art_re = r"^\s{0,4}제\s*(\d+)\s*조(?:\s*의\s*(\d+))?\s+\S" if reg["name"] == "대한민국헌법" else r"^\s{0,4}제\s*(\d+)\s*조(?:\s*의\s*(\d+))?\s*(?:[(\[]|삭제)"      # 헌법은 조 제목 없이 '제1조 ①…' 으로 씀
        for pg in pages:
            for ln in pg.split("\n"):
                m = re.match(art_re, ln)
                if m:
                    src[(int(m.group(1)), int(m.group(2) or 0))] += 1
        db = collections.Counter()
        for a in ([] if outline else arts):
            if a["article_label"] != "부칙":
                m = re.match(r"(\d+)(?:의(\d+))?$", a["article_number"])
                db[(int(m.group(1)), int(m.group(2) or 0))] += 1
        if outline:
            src, db = collections.Counter(), collections.Counter()
        for k in db:
            if db[k] > src.get(k, 0):
                problems.append(f"[{name}] DB 에만 있는 조 {k}")
        for k in src:
            if src[k] > db.get(k, 0):
                notes.append(f"[{name}] PDF 줄머리에 있으나 DB 에 없는 조 {k} (본문 중 줄머리 인용이면 정상)")
        # 2) 본문 연속 일치
        full = re.sub(r"법제처\d+국가법령정보센터", "", "".join(npages))
        full = full.replace(ns(reg["name"]), "")
        full_nonote = NOTE_IN_TEXT.sub("", full)
        for a in ([] if outline else arts):
            t = art_text(a)
            if a["is_supplementary"]:
                continue                                  # 부칙은 제목 줄(부칙 <제N호,…>)이 따로라 연속 비교 대상에서 제외(2b 줄 단위로 확인)
            if t not in full and t not in full_nonote:
                # 마침표가 PDF 에서 다음 줄로 밀린 경우 등을 감안해 끝 4글자를 뺀 앞부분으로 한 번 더
                if t[:-4] not in full_nonote and t[:-4] not in full:
                    notes.append(f"[{name}] 본문 연속 일치 실패(수동 확인): {a['display_id']}")
        # 2b) 줄 단위 포함: PDF 의 모든 줄이 DB 글자열(조문 평문 + notes + 장·절 제목 + 부칙 제목)에 들어 있는가
        meta = load("regulations/" + reg["slug"] + "/metadata.json")
        hdr = [ns(x) for x in meta["header_raw"]]
        chunks = []
        for a in arts:
            chunks.append(art_text(a))
            chunks += [ns(n) for n in a["notes"]] + [ns(x) for x in (a["part"], a["chapter"], a["section"], a["subsection"])]
            if a.get("supplement_header"):
                chunks.append(ns(a["supplement_header"]))
        chunks += [ns(o["title"]) for o in meta.get("outline", [])]
        blob = "".join(chunks)
        blob_b = blob.replace("(", "[").replace(")", "]")
        missing = []
        for pn, pg in enumerate(pages if not outline else [], 1):
            for ln in pg.split("\n"):
                s_ = ns(TAGS.sub("", ln) if re.match(r"^\s*제\s*\d+\s*(편|장|절|관|항)(의\s*\d+)?\s+\S", ln) else ln)      # 장·절 제목의 개정 표지만 DB 에서 뺐다
                if len(s_) < 4 or re.match(r"^법제처\d+국가법령정보센터$", s_) or s_ == ns(reg["name"]) or any(s_ in h or h in s_ for h in hdr):
                    continue
                if s_ not in blob and s_.replace("(", "[").replace(")", "]") not in blob_b:
                    missing.append((pn, ln.strip()[:60]))
        if missing:
            problems.append(f"[{name}] PDF 줄이 DB 에 없음 {len(missing)}건 — 예: {missing[:3]}")
        # 3) source_page
        for a in arts:
            if a["article_label"] == "부칙":
                continue
            a0, a1 = int(a["source_page"].split("-")[0]), int(a["source_page"].split("-")[-1])
            lab = ns(a["article_label"])
            if outline:
                continue
            if not any(lab in npages[p - 1] for p in range(a0, a1 + 1)):
                problems.append(f"[{name}] source_page 에 조문이 없음: {a['display_id']} {a['source_page']}")
        # 4) ID·인덱스·related
        rows = {r["id"]: r for r in idx["articles"] if idx["regulations"][r["r"]]["id"] == reg["id"]}
        for a in arts:
            if a["id"] in allids:
                problems.append(f"ID 중복 {a['id']}")
            allids.add(a["id"])
            if not ID_RE.match(a["id"]) and not OUTLINE_ID_RE.match(a["id"]) and not OPINION_ID_RE.match(a["id"]):
                problems.append(f"ID 형식 {a['id']}")
            r = rows.get(a["id"])
            if not r or r["t"] != a["article_title"] or r["n"] != a["article_label"] or r["k"] != a["keywords"]:
                problems.append(f"인덱스 불일치 {a['id']}")
            rr = {t.split("_ART_")[0] for t in a["related_articles"] if not t.startswith(a["regulation_id"] + "_ART")}
            if set(a["related_regulations"]) != rr:
                problems.append(f"related_regulations 불일치 {a['id']}")
        if len(rows) != len(arts):
            problems.append(f"[{name}] 인덱스 조문 수 {len(rows)} ≠ {len(arts)}")
        # 5) 부속
        for a in anns:
            if a["id"] in allids:
                problems.append(f"ID 중복 {a['id']}")
            allids.add(a["id"])
            if not os.path.exists(R + a["pdf_path"]):
                problems.append(f"부속 PDF 사본 없음 {a['id']}")
            elif a["pdf_path"] == reg["pdf_path"]:                 # 본문 PDF 안에 든 부속: 쪽 범위 길이와 비교
                pg = [int(x) for x in a["source_page"].split("-")]
                if a["pages"] != pg[-1] - pg[0] + 1:
                    problems.append(f"부속 쪽수 불일치 {a['id']}")
            elif pymupdf.open(R + a["pdf_path"]).page_count != a["pages"]:
                problems.append(f"부속 쪽수 불일치 {a['id']}")
        if doc.page_count != reg["source_pages"]:
            problems.append(f"[{name}] 쪽수 불일치")
    for t in {i for r in idx["articles"] for i in r.get("rr", [])}:
        if not 0 <= t < len(idx["regulations"]):
            problems.append("인덱스 rr 범위 오류")
    n_files = sum(1 for _r, _d, fs in os.walk(R + "pdf") for f in fs if f.startswith("[") and os.path.abspath(_r) != os.path.abspath(R + "pdf"))     # 하위 폴더의 [별표…] 파일(맨 위의 '[개정전문]' 본문은 제외)
    n_db = sum(r["counts"]["annexes"] for r in regs) - sum(len([1 for a in load(r["files"]["annexes"]) if a["pdf_path"] == r["pdf_path"]]) for r in regs)   # 본문 PDF 안에 든 부속은 제외
    if n_files != n_db:
        problems.append(f"부속 수 DB {n_db} ≠ PDF {n_files}")
    print(f"감사: 규정 {len(regs)}개 · ID {len(allids)}개 · 문제 {len(problems)}건 · 참고 {len(notes)}건")
    for x in problems:
        print("문제:", x)
    for x in notes:
        print("참고:", x)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
