"""법제처 법령해석례(질의요지·회답·이유 …로 쓰인 문서)를 같은 DB 구조로 옮긴다.

  예: 법령해석례 18-0366 '협상에 의한 계약 시 제안서평가위원회 심의 대상의 범위'

대응(해석례 → 조문):
  【질의요지】【회답】【이유】 · ＜관계 법령＞ · 【법제처 법령해석의 효력 등에 대한 안내】 한 부분 = 조문(article) 하나
  그 부분 안의 문단(빈 줄·세로 간격으로 나뉨) = 항(paragraph) — 번호 표지 없음
쪽 머리글(해석례 이름)·바닥글(법제처 N 국가법령정보센터)은 뺀다.
"""
import re

from pdf_lines import FOOT_RE, gap_kind, join, page_lines, right_margin, squash

HEAD_RE = re.compile(r"^【\s*(.+?)\s*】$")
LAW_RE = re.compile(r"^[＜<]\s*(관계\s*법령)\s*[＞>]$")
RUNHEAD_RE = re.compile(r'^"[^"]*"\(법령해석례,\s*([\d\-]+)\)$')
FRONT_RE = re.compile(r"^\[\s*([\d\-]+)\s*,\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?\s*,\s*([^\]]*)\]$")


def parse(path, spacer=None):
    """반환: dict(number, title, date, requester, front, sections[{heading, page0, page1, paras[str]}], pages)"""
    L = page_lines(path)
    pages = max(r["page"] for r in L)
    L = [r for r in L if not FOOT_RE.match(squash(r["text"])) and not RUNHEAD_RE.match(r["text"].strip())]
    margin = right_margin(L)
    res = {"number": "", "title": "", "date": "", "requester": "", "front": [], "sections": [], "pages": pages}
    # 머리: 첫 【…】 앞의 줄들 = 제목(여러 줄) + '[번호, 날짜, 질의자]'
    k = 0
    title = []
    while k < len(L) and not HEAD_RE.match(L[k]["text"].strip()):
        t = L[k]["text"].strip()
        res["front"].append(t)
        m = FRONT_RE.match(t)
        if m:
            res["number"] = m.group(1)
            res["date"] = f"{int(m.group(2)):04d}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"
            res["requester"] = m.group(5).strip()
        else:
            title.append(L[k])
        k += 1
    if title:
        parts = []
        for i, r in enumerate(title):             # 제목이 두 줄로 나뉜 것: 줄 끝 위치로 이음
            kind = ""
            if i:
                kind = gap_kind(title[i - 1], max(x["x1"] for x in title), r, spacer).replace("\n", " ")
            parts.append((r["text"].strip(), kind))
        res["title"] = join(parts).strip()
    cur = None
    for r in L[k:]:
        t = r["text"].strip()
        hm = HEAD_RE.match(t)
        lm = LAW_RE.match(t)
        if hm or lm:
            head = re.sub(r"\s+", " ", (hm or lm).group(1))
            cur = {"heading": head, "page0": r["page"], "page1": r["page"], "lines": []}
            res["sections"].append(cur)
            continue
        if cur is None:
            continue
        cur["page1"] = r["page"]
        cur["lines"].append(r)
    for sec in res["sections"]:
        sec["paras"] = _paragraphs(sec.pop("lines"), margin, spacer)
    return res


MARK_RE = re.compile(r"^(?:[○ㅇ※]|\d{1,2}\.\s|[①-⑳]|[＜<]|\(\d{1,2}\)\s)")


def _paragraphs(lines, margin, spacer=None):
    """줄 목록 → 문단 목록. 문단 나눔: 앞 줄과의 세로 간격이 보통 줄 간격보다 크거나, 줄이 '○ · 1. · ① · ＜' 같은 표지로 시작할 때"""
    if not lines:
        return []
    pitch = sorted(b["y0"] - a["y0"] for a, b in zip(lines, lines[1:]) if b["page"] == a["page"] and b["y0"] > a["y0"])
    base = pitch[len(pitch) // 2] if pitch else 20.0
    paras, cur = [], [lines[0]]
    for a, b in zip(lines, lines[1:]):
        if b["page"] == a["page"]:
            gap = b["y0"] - a["y0"] > base * 1.4
        else:                                     # 쪽이 바뀐 자리: 앞 줄이 오른쪽 끝까지 차지 않았으면 문단이 끝난 것
            gap = gap_kind(a, margin, b, spacer) == "\n"
        newp = gap or bool(MARK_RE.match(b["text"].strip()))
        if newp:
            paras.append(cur)
            cur = []
        cur.append(b)
    paras.append(cur)
    out = []
    for p in paras:
        parts = []
        for i, r in enumerate(p):
            kind = gap_kind(p[i - 1], margin, r, spacer) if i else ""
            parts.append((r["text"].strip(), kind))
        out.append(join(parts).strip())
    return [x for x in out if x]
