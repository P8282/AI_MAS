"""규정 본문(줄 목록) → 장·절·조·항·호·목 구조.

원문 문구는 고치지 않는다. 줄 이음(공백/줄바꿈)만 pdf_lines.gap_kind 규칙으로 복원한다.
순서 검사(조 번호는 앞 조보다 커야 하고, 호·목은 1·가 부터 차례로)로 '줄 머리에 우연히 나온 인용'을 조문 시작으로 오인하지 않는다.
"""
import re

from pdf_lines import clean_main, gap_kind, join, right_margin

# 개정 표시: <개정 2025. 10. 1.> · <신설 …> · <삭제 …> 처럼 날짜가 들어간 꺾쇠 표지
TAG_RE = re.compile(r"<[^<>]*?\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.?[^<>]*>")
PARA = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚㉛㉜㉝㉞㉟㊱㊲㊳㊴㊵㊶㊷㊸㊹㊺㊻㊼㊽㊾㊿"
PARA_NO = {c: i + 1 for i, c in enumerate(PARA)}
HAN = list("가나다라마바사아자차카타파하거너더러머버서어저처커터퍼허")
HAN_NO = {c: i + 1 for i, c in enumerate(HAN)}

HEAD_RE = re.compile(r"^제\s*(\d+)\s*(편|장|절|관|항)(의\s*\d+)?\s+(\S.*)$")
ART_RE = re.compile(r"^제\s*(\d+)\s*조(?:\s*의\s*(\d+))?\s*(\(|\[|삭제)")
PARA_RE = re.compile(r"^([" + PARA + r"])\s*(.*)$")
ITEM_RE = re.compile(r"^(\d{1,3})(?:\s*의\s*(\d+))?\.\s*(.*)$")
SUB_RE = re.compile(r"^([가나다라마바사아자차카타파하거너더러머버서어저처커터퍼허])(?:의(\d+))?\.\s*(.*)$")
DEEP_RE = re.compile(r"^(?:\d{1,3}\)|[가나다라마바사아자차카타파하]\)|\(\d{1,3}\)|\([가나다라마바사아자차카타파하]\))")
SUPP_RE = re.compile(r"^부\s*칙(\s*<.*)?$")
NOTE_RE = re.compile(r"^(?:\[(?!별표|별지|별첨|서식)[^\]]*\]|\[시행일\s*:[^\]]*\].*)$")
CLAUSE_END = re.compile(r"(다|한다|된다|있다|없다|아니하다)\.\s*$")
HEAD_BAD = re.compile(r"(따라|따른|의하여|의한|에 의하|에 따)")


def find_close(s, start):
    o = s[start]
    c = {"(": ")", "[": "]"}[o]
    d = 0
    for i in range(start, len(s)):
        if s[i] == o:
            d += 1
        elif s[i] == c:
            d -= 1
            if d == 0:
                return i
    return -1


NOTITLE_DOCS = {"대한민국헌법"}


class Node:
    __slots__ = ("parts", "no", "suffix", "children", "deep")

    def __init__(self, no="", suffix=""):
        self.parts, self.no, self.suffix, self.children, self.deep = [], no, suffix, [], 0

    def add(self, text, kind=""):
        self.parts.append((text, kind))

    def text(self):
        return join(self.parts)


class Parser:
    def __init__(self, lines, title, spacer=None):
        self.spacer = spacer
        self.notitle = title.strip() in NOTITLE_DOCS      # 조 제목 없이 '제1조 ①대한민국은 …' 으로 쓰인 문서(대한민국헌법)
        self.lines = clean_main(lines, title)
        self.margin = right_margin(self.lines)
        self.front, self.articles, self.supps, self.rejected = [], [], [], []
        self.ctx = {"part": "", "chapter": "", "section": "", "subsection": ""}
        self.art = None
        self.last_key = (0, 0)
        self.supp = None
        self.p = self.i = self.s = None   # 현재 항/호/목 노드
        self.last = None                    # 마지막으로 글을 이은 노드
        self.pend = None                    # 줄을 넘는 조 제목
        self.prev = None
        self.prev_line = None
        self.rejected_heads = []
        self.outline = []                   # 편·장·절·관·항 제목을 나온 순서대로 (조문이 없는 삭제된 장도 포함)

    # ── 조 시작 ──
    def start_article(self, num, branch, r, deleted):
        self.art = {"num": num, "branch": branch, "title": "", "deleted": deleted, "ctx": dict(self.ctx),
                    "supp": self.supp["idx"] if self.supp else 0, "page0": r["page"], "page1": r["page"],
                    "notes": [], "paras": []}
        (self.supp["articles"] if self.supp else self.articles).append(self.art)
        self.p = self.i = self.s = self.last = None

    def para_node(self, no, text):
        n = Node(no)
        n.add(text)
        self.art["paras"].append(n)
        self.p, self.i, self.s, self.last = n, None, None, n

    def ensure_para(self):
        if not self.art["paras"]:
            self.para_node("", "")
            self.art["paras"][0].parts = []

    # ── 본문 줄 ──
    def body(self, text, kind):
        a = self.art
        # 항
        m = PARA_RE.match(text)
        if m:
            n = PARA_NO[m.group(1)]
            last = PARA_NO.get(a["paras"][-1].no, 0) if a["paras"] and a["paras"][-1].no else 0
            if n == last + 1 or (n == 1 and not a["paras"]) or (n == 1 and a["paras"] and a["paras"][-1].no == "" and not a["paras"][-1].children and not a["paras"][-1].text().strip()):
                if n == 1 and a["paras"] and a["paras"][-1].no == "" and not a["paras"][-1].text().strip() and not a["paras"][-1].children:
                    a["paras"].pop()
                self.para_node(m.group(1), m.group(2))
                return
        self.ensure_para()
        P = self.p
        # 호
        m = ITEM_RE.match(text)
        if m and self.s is None or (m and self.s is not None):
            n, suf = int(m.group(1)), m.group(2)
            lastn, lastsuf = (P.children[-1].no, P.children[-1].suffix) if P.children else (0, "")
            lastn = int(lastn) if lastn else 0
            if (not suf and n == lastn + 1) or (suf and n == lastn and (not lastsuf or int(suf) > int(lastsuf))) or (suf and n == lastn + 1 and False):
                it = Node(str(n), suf or "")
                it.add(m.group(3))
                P.children.append(it)
                self.i, self.s, self.last = it, None, it
                return
        # 목 (호 안에서만)
        m = SUB_RE.match(text)
        if m and self.i is not None:
            n, suf = HAN_NO[m.group(1)], m.group(2)
            I = self.i
            lastn = HAN_NO[I.children[-1].no] if I.children else 0
            if n == lastn + 1:
                sb = Node(m.group(1), suf or "")
                sb.add(m.group(3))
                I.children.append(sb)
                self.s, self.last = sb, sb
                return
        # 이어쓰기
        tgt = self.last or self.s or self.i or P
        if DEEP_RE.match(text) and tgt.parts:
            tgt.add(text, "\n")
        else:
            tgt.add(text, kind)
        self.last = tgt

    # ── 전체 ──
    def run(self):
        for r in self.lines:
            text = r["text"].strip()
            if not text:
                continue
            kind = gap_kind(self.prev, self.margin, r, self.spacer) if self.prev else ""
            self.prev_line = self.prev
            self.prev = r

            if self.pend is not None:   # 제목 괄호가 다음 줄로 이어짐
                buf = self.pend + kind + text
                i = find_close(buf, 0)
                if i < 0:
                    self.pend = buf
                    continue
                self.pend = None
                self.art["title"] = buf[1:i]
                rest = buf[i + 1:].strip()
                self.art["page1"] = r["page"]
                if rest:
                    self.ensure_para(); self.body(rest, "")
                continue

            if SUPP_RE.match(text):
                self.supp = {"idx": len(self.supps) + 1, "header": text, "articles": [], "pre": []}
                self.supps.append(self.supp)
                self.art, self.last_key = None, (0, 0)
                self.p = self.i = self.s = self.last = None
                continue

            m = HEAD_RE.match(text)
            if m and not self.supp and len(text) < 70 and not CLAUSE_END.search(text) and not (HEAD_BAD.search(text) and m.group(2) == "항") and (self.margin - r["x1"]) / r["size"] > 1.5:
                k = m.group(2)
                raw_head = text
                text = re.sub(r"\s*" + TAG_RE.pattern, "", text).strip()      # 장·절 제목 뒤의 <개정 …> 표지는 제목이 아니므로 뺀다
                self.outline.append({"kind": k, "title": text, "raw": raw_head, "page": r["page"], "deleted": "삭제" in raw_head[len(text):]})
                if k == "항":      # 민법의 '제N항' 소제목은 관 아래 단계 → subsection 에 이어 붙인다
                    base = self.ctx["subsection"].split(" › ")[0]
                    self.ctx["subsection"] = (base + " › " if base else "") + text
                else:
                    self.ctx[{"편": "part", "장": "chapter", "절": "section", "관": "subsection"}[k]] = text
                    for lower in {"편": ("chapter", "section", "subsection"), "장": ("section", "subsection"), "절": ("subsection",), "관": ()}[k]:
                        self.ctx[lower] = ""
                self.art = None
                self.p = self.i = self.s = self.last = None
                continue

            if m and not self.supp and not (len(text) < 70 and not CLAUSE_END.search(text) and not (HEAD_BAD.search(text) and m.group(2) == "항") and (self.margin - r["x1"]) / r["size"] > 1.5):
                self.rejected_heads.append({"page": r["page"], "text": text[:90]})
            if self.notitle and not self.supp:
                text = re.sub(r"^(제\s*\d+\s*조(?:\s*의\s*\d+)?)\s+(?=\S)", r"\1() ", text, count=1)      # 제목이 없으면 빈 제목 '()' 을 넣어 같은 경로로 처리
            m = ART_RE.match(text)
            if m:
                key = (int(m.group(1)), int(m.group(2) or 0))
                variant = False
                if self.supp:
                    L = [x for x in self.supp["articles"] if x["num"] > 0]
                    ok = (key == (1, 0) and not L) or bool(L and ((L[-1]["num"] < key[0] <= L[-1]["num"] + 10 and key[1] == 0) or (key[0] == L[-1]["num"] and key[1] > L[-1]["branch"])))
                else:
                    ok = key > self.last_key
                    if key == self.last_key and self.articles and (self.articles[-1]["num"], self.articles[-1]["branch"]) == key:
                        ok = variant = True      # 같은 조가 연달아 한 번 더 나옴 = 시행일이 다른 개정본(시행 예정본)
                if ok:
                    if not self.supp:
                        self.last_key = key
                    deleted = m.group(3) == "삭제"
                    self.start_article(key[0], key[1], r, deleted)
                    if variant:
                        base = next(a for a in reversed(self.articles[:-1]) if (a["num"], a["branch"]) == key and not a.get("variant"))
                        base["variants"] = base.get("variants", 0) + 1
                        self.art["variant"] = base["variants"]
                    if deleted:
                        n = Node("")
                        n.add(text[text.index("삭제"):].strip())
                        self.art["paras"].append(n)
                        self.p, self.last = n, n
                        continue
                    op = m.end() - 1
                    cl = find_close(text, op)
                    if cl < 0:
                        self.pend = text[op:]
                        continue
                    self.art["title"] = text[op + 1:cl]
                    rest = text[cl + 1:].strip()
                    if self.art["title"].strip() == "삭제":
                        self.art["deleted"] = True
                    if rest:
                        self.ensure_para(); self.body(rest, "")
                    continue
                self.rejected.append({"page": r["page"], "text": text[:90]})

            if self.art is None:
                if not self.supp:
                    self.front.append(text)
                    continue
                self.start_article(0, 0, r, False)      # 조 번호 없이 서술된 부칙(예: '이 규정은 …부터 시행한다.')
                self.art["pre"] = True
            self.art["page1"] = r["page"]
            if NOTE_RE.match(text):
                self.art["notes"].append(text)
                self.last = None
                continue
            self.body(text, kind)
        return self

    def art_continuing_ref(self, text):
        return False
