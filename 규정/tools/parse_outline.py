"""장·절·관 + 번호 목록(1. 가. 1) 가)) 으로 쓰인 예규(조 번호가 없는 문서)를 같은 DB 구조로 옮긴다.

  예: 행정안전부 예규 '지방자치단체 입찰 및 계약집행기준' (386쪽)

대응(조 → 항목):
  제N장 ▸ 제N절 ▸ 제N관          → chapter · section · subsection
  '1.' (한 범위 안의 번호 목록)    → 조문(article) — 제목은 그 줄, ID 에 장·절·관·번호가 모두 들어감
  '가.'                           → 항(paragraph)    '1)' → 호(item)    '가)' → 목(subitem)
  '■ … [별표|별지|별첨 …]' 쪽들     → 별표·별지·별첨(annex), 해당 쪽을 표·글 블록으로
굵게 보이려 같은 글자를 겹쳐 그린 PDF 라 글자 겹침을 먼저 걷어낸다(pdf_lines.page_lines_dedup).
번호 목록은 '앞 번호 + 1' 순서일 때만 새 항목으로 본다(문장 중 줄머리 '1.' 같은 것을 오인하지 않기 위해).
"""
import re

from pdf_lines import gap_kind, join, page_lines_dedup

HAN = list("가나다라마바사아자차카타파하")
HAN_NO = {c: i + 1 for i, c in enumerate(HAN)}
HEAD_RE = re.compile(r"^제\s*(\d+)\s*(장|절|관)(?:의\s*(\d+))?\s*(.*)$")
L1 = re.compile(r"^(\d{1,2})\.\s+(\S.*)$")
L1_BARE = re.compile(r"^(\d{1,2})\.$")
L2 = re.compile(r"^([가나다라마바사아자차카타파하])\.\s*(.*)$")
L3 = re.compile(r"^(\d{1,2})\)\s*(.*)$")
L4 = re.compile(r"^([가나다라마바사아자차카타파하])\)\s*(.*)$")
PAGENO = re.compile(r"^-\s*\d+\s*-$")
APPX = re.compile(r"^■\s*.*?제\s*(\d+)\s*장(?:의\s*(\d+))?.*?\[\s*(별표|별지|별첨|붙임)\s*(?:양식)?\s*(?:제\s*)?(\d+)?\s*(?:호)?\s*(?:서식|양식)?\s*\]\s*$")
SENT_END = re.compile(r"(다|함|음|임)[.,)]*$|[,;]$")


class Scope:
    def __init__(self, key):
        self.key = key            # (chapter, ch_suffix, section, subsection)
        self.title = {"chapter": "", "section": "", "subsection": ""}
        self.items = []           # 한 범위의 '1.' 항목들 (dict)
        self.intro = []           # 번호 항목 앞의 글


ROMAN = "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ"
ROMAN_RE = re.compile(r"^([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ])\.\s*(\S.*)$")      # style="roman": 로마숫자 제목(Ⅰ. 목적)을 장으로 본다


def parse(path, spacer, style=None):
    """반환: dict(front, scopes[Scope…], annex_spans[(page_from, page_to, chapter, kind, number, marker)], pages, lines)"""
    lines = page_lines_dedup(path)
    pages = max(r["page"] for r in lines)
    margin = max((r["x1"] for r in lines if len(r["text"]) > 30), default=max(r["x1"] for r in lines))
    front = [r["text"] for r in lines if r["page"] == 1]
    # 부속 쪽 범위: '■ … [별표 …]' 가 있는 쪽부터 다음 부속 쪽 직전 또는 다음 장 시작 쪽 직전까지
    marker_pages = {}
    for r in lines:
        m = APPX.match(r["text"])
        if m and r["page"] not in marker_pages:
            marker_pages[r["page"]] = ((int(m.group(1)), m.group(2) or ""), m.group(3), m.group(4) or "", r["text"])
    toc = {}
    for r in lines:
        mt = re.match(r"^제\s*(\d+)\s*장(?:의\s*(\d+))?\s+(.*?)\s*·{3,}", r["text"])
        if mt and r["page"] <= 3:
            toc[(int(mt.group(1)), mt.group(2) or "")] = re.sub(r"\s+", "", mt.group(3))
    chap_start = {}
    for r in lines:
        m = HEAD_RE.match(r["text"])
        if m and m.group(2) == "장" and r["page"] not in marker_pages and "····" not in r["text"]:
            n = (int(m.group(1)), m.group(3) or ""); tt = re.sub(r"\s+", "", m.group(4))
            if n in toc and tt and (tt == toc[n] or (toc[n].startswith(tt) and len(tt) >= len(toc[n]) - 2)):
                chap_start.setdefault(n, r["page"])
    supp_i = None             # style="roman": 부칙이 본문과 같은 쪽에서 시작하므로 쪽이 아니라 줄 위치로 가른다
    if style == "roman":      # 부칙 표제가 '부칙 <제2026-13호,2026.8.4.>' 꼴이고 본문이 첫 쪽부터 시작
        supp_i = next((i for i, r in enumerate(lines) if re.match(r"^부\s*칙(\s*<.*>)?$", r["text"])), len(lines))
        supp_start = pages + 1
    else:
        supp_start = min((r["page"] for r in lines if re.match(r"^부\s*칙$", r["text"]) and r["x0"] > 200 and r["page"] > 3), default=pages + 1)
    starts = sorted(chap_start.values())
    spans = []
    mp = sorted(marker_pages)
    for k, pg in enumerate(mp):
        nxt = [p for p in starts if p > pg]
        end = min([mp[k + 1] - 1] if k + 1 < len(mp) else [pages], [nxt[0] - 1] if nxt else [pages], [supp_start - 1])
        ch, kind, num, marker = marker_pages[pg]
        spans.append({"from": pg, "to": max(pg, min(end[0] if isinstance(end, list) else end, pages)), "chapter": ch[0], "ch_suf": ch[1], "kind": kind, "number": num, "marker": marker})
    appx_pages = {p for s in spans for p in range(s["from"], s["to"] + 1)}

    ch_title, sec_title, sub_title = {}, {}, {}
    scopes = {}
    order = []
    cur = None
    ctx = {"ch": None, "sec": 0, "sub": 0}
    last_num = {"l1": 0, "l2": 0, "l3": 0, "l4": 0}
    node = {"item": None, "l2": None, "l3": None, "l4": None, "last": None}
    prev = None
    rejected = []
    last_scope_key = None

    def scope_for(chn, chs, sec, sub):
        key = (chn, chs, sec, sub)
        if key not in scopes:
            scopes[key] = Scope(key)
            order.append(key)
        return scopes[key]

    def add_text(n, text, kind):
        n["parts"].append((text, kind))

    for li, r in enumerate(lines):
        t = r["text"]
        if supp_i is not None and li >= supp_i:
            break
        if r["page"] in appx_pages or (r["page"] == 1 and style != "roman") or r["page"] >= supp_start:
            prev = None
            continue
        if PAGENO.match(t) or "····" in t or re.match(r"^목\s*차$", t):
            continue
        kind = gap_kind(prev, margin, r, spacer) if prev else ""
        prev = r
        if style == "roman":
            rm = ROMAN_RE.match(t)
            if rm and r["x0"] < 80 and len(rm.group(2)) < 60 and ROMAN.index(rm.group(1)) + 1 == (ctx["ch"][0] if ctx["ch"] else 0) + 1:
                cn = ROMAN.index(rm.group(1)) + 1
                ctx.update(ch=(cn, ""), sec=0, sub=0)
                ch_title[(cn, "")] = re.sub(r"\s+", " ", rm.group(2)).strip()
                cur = scope_for(cn, "", 0, 0)
                for k in last_num:
                    last_num[k] = 0
                node.update(item=None, l2=None, l3=None, l4=None, last=None)
                continue
            t = re.sub(r"^\((\d{1,2})\)\s*", r"\1) ", t)      # '(1) …' 호를 '1) …' 로
        m = HEAD_RE.match(t)
        if m:
            n, suf, kd = int(m.group(1)), m.group(3) or "", m.group(2)
            title = m.group(4).strip()
            ok = False
            if title and not SENT_END.search(title) and len(title) < 70 and '"' not in title:
                if kd == "장":
                    tt = re.sub(r"\s+", "", title)
                    ok = (n, suf) in toc and (tt == toc[(n, suf)] or (toc[(n, suf)].startswith(tt) and len(tt) >= len(toc[(n, suf)]) - 2))
                    if ok:
                        ctx.update(ch=(n, suf), sec=0, sub=0)
                        ch_title[(n, suf)] = re.sub(r"\s+", " ", title)
                elif ctx["ch"] is not None and r["x0"] < 75:
                    if kd == "절" and not suf and (n == ctx["sec"] + 1 or n == ctx["sec"] or n == 1):
                        ok = True
                        ctx.update(sec=n, sub=0)
                        sec_title[(ctx["ch"], n)] = re.sub(r"\s+", " ", title)
                    elif kd == "관" and not suf and (n == ctx["sub"] + 1 or n == ctx["sub"] or n == 1) and ctx["sec"]:
                        ok = True
                        ctx.update(sub=n)
                        sub_title[(ctx["ch"], ctx["sec"], n)] = re.sub(r"\s+", " ", title)
            if ok:
                cur = scope_for(ctx["ch"][0], ctx["ch"][1], ctx["sec"], ctx["sub"])
                for k in last_num:
                    last_num[k] = 0
                node.update(item=None, l2=None, l3=None, l4=None, last=None)
                continue
        if cur is None:
            continue
        # 번호 목록: 줄 머리가 왼쪽(x0<80)이고 순서가 맞을 때만
        left = r["x0"] < 80
        hit = None
        if left:
            m1 = L1.match(t) or L1_BARE.match(t)
            if m1 and int(m1.group(1)) == last_num["l1"] + 1:
                hit = ("l1", int(m1.group(1)), (m1.group(2) if m1.lastindex and m1.lastindex > 1 else ""))
            elif node["item"] is not None:
                m2 = L2.match(t)
                m3 = L3.match(t)
                m4 = L4.match(t)
                if m2 and HAN_NO[m2.group(1)] == last_num["l2"] + 1:
                    hit = ("l2", HAN_NO[m2.group(1)], m2.group(2), m2.group(1))
                elif m4 and node["l3"] is not None and HAN_NO[m4.group(1)] == last_num["l4"] + 1:
                    hit = ("l4", HAN_NO[m4.group(1)], m4.group(2), m4.group(1))
                elif m3 and int(m3.group(1)) == last_num["l3"] + 1 and (node["l2"] is not None or True):
                    hit = ("l3", int(m3.group(1)), m3.group(2), m3.group(1))
        if hit:
            lv = hit[0]
            if lv == "l1":
                it = {"no": hit[1], "first": hit[2], "parts": [], "paras": [], "page0": r["page"], "page1": r["page"]}
                cur.items.append(it)
                last_num.update(l1=hit[1], l2=0, l3=0, l4=0)
                node.update(item=it, l2=None, l3=None, l4=None, last=it)
            elif lv == "l2":
                p = {"no": hit[1], "mark": hit[3] + ".", "parts": [(hit[2], "")], "items": []}
                node["item"]["paras"].append(p)
                last_num.update(l2=hit[1], l3=0, l4=0)
                node.update(l2=p, l3=None, l4=None, last=p)
            elif lv == "l3":
                if node["l2"] is None:                      # 가. 없이 바로 1) 이 나오면 항 번호 없는 항 하나로 받는다
                    p = {"no": 0, "mark": "", "parts": [], "items": []}
                    node["item"]["paras"].append(p)
                    node["l2"] = p
                p = node["l2"]
                i3 = {"no": hit[1], "mark": hit[3] + ")", "parts": [(hit[2], "")], "subs": []}
                p["items"].append(i3)
                last_num.update(l3=hit[1], l4=0)
                node.update(l3=i3, l4=None, last=i3)
            else:
                s4 = {"no": hit[1], "mark": hit[3] + ")", "parts": [(hit[2], "")]}
                node["l3"]["subs"].append(s4)
                last_num["l4"] = hit[1]
                node.update(l4=s4, last=s4)
            node["item"]["page1"] = r["page"]
            continue
        # 이어쓰기
        if node["item"] is None:
            cur.intro.append((t, kind, r["page"]))
            continue
        tgt = node["last"]
        node["item"]["page1"] = r["page"]
        hard = re.match(r"^[※■□○◦▪▫•\-–①-⑩]|^\(\d{1,2}\)", t) is not None
        tgt["parts"].append((t, "\n" if hard else kind))
    supps = []
    blk = None
    prevl = None
    for li, r in enumerate(lines):
        if (supp_i is not None and li < supp_i) or (supp_i is None and r["page"] < supp_start):
            continue
        t = r["text"]
        if PAGENO.match(t):
            continue
        if (re.match(r"^부\s*칙(\s*<.*>)?$", t) if supp_i is not None else (re.match(r"^부\s*칙$", t) and r["x0"] > 200)):
            blk = {"idx": len(supps) + 1, "paras": [], "page0": r["page"], "page1": r["page"]}
            supps.append(blk)
            prevl = None
            continue
        if blk is None:
            continue
        kind = gap_kind(prevl, margin, r, spacer) if prevl else ""
        prevl = r
        blk["page1"] = r["page"]
        mm = re.match(r"^([①-⑩])\s*(.*)$", t) if r["x0"] < 80 else None
        mm2 = re.match(r"^(\d{1,2})\.\s+(.*)$", t) if r["x0"] < 80 and not mm else None
        if mm or mm2:
            blk["paras"].append({"mark": mm.group(1) if mm else mm2.group(1) + ".", "parts": [((mm or mm2).group(2), "")]})
        elif blk["paras"]:
            blk["paras"][-1]["parts"].append((t, kind))
        else:
            blk["paras"].append({"mark": "", "parts": [(t, "")]})
    for sc in scopes.values():
        c, _s, sec, sub = sc.key
        sc.title = {"chapter": ch_title.get((c, _s), ""), "section": sec_title.get(((c, _s), sec), "") if sec else "", "subsection": sub_title.get(((c, _s), sec, sub), "") if sub else ""}
    return {"front": front, "scopes": [scopes[k] for k in order], "annex_spans": spans, "supps": supps, "pages": pages, "lines": lines, "margin": margin}


def text_of(parts):
    return join(parts)
