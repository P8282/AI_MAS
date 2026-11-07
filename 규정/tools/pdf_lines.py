"""PDF → 줄 단위 텍스트 추출 (PyMuPDF).

국가법령정보센터 PDF 는 글자 단위로 줄을 바꾸므로(단어 중간에서도 줄이 바뀜) 줄 끝 위치로 이음 방식을 추정한다.
  · 줄이 오른쪽 여백 끝까지 찼으면(남은 폭 < 1글자) 단어 중간에서 바뀐 것으로 보고 공백 없이 잇는다.
  · 남은 폭이 1~1.5글자면 단어 사이에서 바뀐 것으로 보고 공백 하나로 잇는다.
  · 그보다 많이 남았으면 원문의 강제 줄바꿈으로 보고 줄바꿈("\n")으로 잇는다.
이 추정은 공백 하나 수준의 차이만 만들 수 있고(검색은 공백을 무시), 글자 자체는 바꾸지 않는다.
"""
import re
import statistics
import unicodedata

import pymupdf

FOOT_RE = re.compile(r"^법제처\s*\d+\s*국가법령정보센터$")


def nfc(s):
    return unicodedata.normalize("NFC", s)


def squash(s):
    return re.sub(r"\s+", "", s)


def page_lines(path):
    """[{page, text, x0, x1, y0, size}] — 읽는 순서(위→아래, 왼→오)"""
    doc = pymupdf.open(path)
    out = []
    for pno, page in enumerate(doc, 1):
        rows = []
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                spans = [s for s in l["spans"] if s["text"] != ""]
                if not spans:
                    continue
                text = "".join(s["text"] for s in spans)
                if not text.strip():
                    continue
                x0, y0, x1, y1 = l["bbox"]
                size = spans[-1]["size"] or 10.0
                rows.append({"page": pno, "text": text.replace(" ", " "), "x0": x0, "x1": x1, "y0": y0, "size": size})
        rows.sort(key=lambda r: (round(r["y0"] / 3), r["x0"]))
        out.extend(rows)
    doc.close()
    return out


def clean_main(lines, title):
    """머리글(문서 제목)·바닥글(법제처 N 국가법령정보센터) 제거. 첫 페이지 맨 위 제목 줄도 머리글이다."""
    t = squash(title)
    res, seen_page = [], set()
    for r in lines:
        s = squash(r["text"])
        if FOOT_RE.match(s):
            continue
        if r["page"] not in seen_page:
            seen_page.add(r["page"])
            if s == t:
                continue
        res.append(r)
    return res


def right_margin(lines):
    xs = [r["x1"] for r in lines if len(r["text"].strip()) > 25]
    return max(xs) if xs else max(r["x1"] for r in lines)


def gap_kind(prev, margin):
    """이전 줄 다음에 올 이음 방식: '' (공백 없이) | ' ' | '\\n'"""
    rem = (margin - prev["x1"]) / prev["size"]
    if rem < 1.0:
        return ""
    if rem <= 1.5:
        return " "
    return "\n"


def join(parts):
    """[(text, kind_before)] → 문자열"""
    s = ""
    for i, (t, k) in enumerate(parts):
        s = t if i == 0 else s + k + t
    return s


class Spacer:
    """줄이 바뀐 자리에 공백이 있었는지 추정한다.

    PDF 는 글자 단위로 줄을 채워서 '단어 사이에서 바뀐 줄'과 '단어 중간에서 바뀐 줄'이 모양만으로는 같다.
    그래서 문서 안쪽(줄 중간)에서 관찰한 사실로 판단한다.
      1) 앞줄 끝 어절 + 뒷줄 첫 어절을 붙인 말이 다른 곳에 한 어절로 나온 적 있으면 붙인다.
      2) 앞줄 끝 어절이 다른 곳에 한 어절로 나온 적이 있고 붙인 말은 없으면 띄운다.
      3) 앞뒤 글자 쌍이 줄 중간에서 '붙어 있던 횟수' 대 '띄어 있던 횟수'로 정한다.
      4) 그래도 모르면 글자별 '어절 끝/어절 처음일 가능성'으로 정한다.
    """

    def __init__(self):
        from collections import Counter
        self.vocab = Counter()
        self.sp, self.ns = Counter(), Counter()
        self.end_sp, self.end_all = Counter(), Counter()    # 글자 a 뒤가 공백인 횟수 / 뒤에 글자가 있는 횟수
        self.beg_sp, self.beg_all = Counter(), Counter()    # 글자 b 앞이 공백인 횟수 / 앞에 글자가 있는 횟수

    def fit(self, texts):
        for t in texts:
            t = t.strip()
            toks = t.split()
            for w in toks[1:-1]:
                self.vocab[w] += 1
            for i in range(1, len(t) - 1):
                a, b = t[i - 1], t[i + 1]
                if t[i] == " " and a != " " and b != " ":
                    self.sp[(a, b)] += 1
                    self.end_sp[a] += 1; self.end_all[a] += 1
                    self.beg_sp[b] += 1; self.beg_all[b] += 1
                elif t[i] != " " and a != " ":
                    self.ns[(a, t[i])] += 1
                    self.end_all[a] += 1
                    self.beg_all[t[i]] += 1
        return self

    def decide(self, a_text, b_text, rem):
        """a_text: 앞줄, b_text: 뒷줄, rem: 앞줄 끝에 남은 폭(글자 수). → '' | ' '"""
        w1, w2 = a_text.split()[-1], b_text.split()[0]
        a, b = w1[-1], w2[0]
        if a == "제" and b.isdigit():          # '제' + 숫자(제17조)는 항상 붙는다
            return ""
        cat = w1 + w2
        if self.vocab.get(cat, 0) >= 1 and self.vocab.get(w1, 0) < 2:
            return ""
        if self.vocab.get(w1, 0) >= 2 and self.vocab.get(cat, 0) == 0:
            return " "
        s, n = self.sp.get((a, b), 0), self.ns.get((a, b), 0)
        if s + n >= 3:
            return " " if s > n else ""
        pa = (self.end_sp[a] + 0.5) / (self.end_all[a] + 1)
        pb = (self.beg_sp[b] + 0.5) / (self.beg_all[b] + 1)
        prior = 1.0 if rem >= 1.0 else 0.7
        return " " if pa * pb * prior > (1 - pa) * (1 - pb) * 0.8 else ""


def gap_kind(prev, margin, nxt=None, spacer=None):
    rem = (margin - prev["x1"]) / prev["size"]
    if rem > 1.5:
        return "\n"
    if spacer is not None and nxt is not None:
        return spacer.decide(prev["text"].strip(), nxt["text"].strip(), rem)
    return " " if rem >= 1.0 else ""


def dedupe_chars(chars, key_c=0, key_x=1, key_y=2, tol=2.6):
    """굵게 보이게 같은 글자를 1~2pt 어긋나게 겹쳐 그린 PDF 용: 같은 글자가 tol 안에 이미 있으면 버린다.
    chars: 튜플/리스트 목록, key_* 는 (글자, x, y) 위치. 글자 크기(≥8pt)보다 훨씬 작은 tol 이라 진짜 겹글자('ㅇㅇ', '99')는 남는다."""
    from collections import defaultdict
    seen = defaultdict(list)
    out = []
    for c in sorted(chars, key=lambda c: (round(c[key_y]), c[key_x])):
        lst = seen[c[key_c]]
        if any(abs(k[key_x] - c[key_x]) < tol and abs(k[key_y] - c[key_y]) < tol for k in lst[-6:]):
            continue
        lst.append(c)
        out.append(c)
    return out


def page_lines_dedup(path, tol=2.6):
    """page_lines 와 같은 모양([{page,text,x0,x1,y0,size}])이되, 겹쳐 그린 글자를 먼저 걷어낸 것"""
    doc = pymupdf.open(path)
    res = []
    for pno, page in enumerate(doc, 1):
        chars = []
        for b in page.get_text("rawdict")["blocks"]:
            if b.get("type") != 0:
                continue
            for ln in b["lines"]:
                for sp in ln["spans"]:
                    for ch in sp["chars"]:
                        x0, y0, x1, y1 = ch["bbox"]
                        chars.append([ch["c"], x0, (y0 + y1) / 2, x1, sp["size"]])
        chars = dedupe_chars(chars, 0, 1, 2, tol)
        chars.sort(key=lambda c: (c[2], c[1]))
        rows, cur = [], []
        for c in chars:
            if cur and abs(c[2] - cur[-1][2]) > 3:
                rows.append(cur)
                cur = []
            cur.append(c)
        if cur:
            rows.append(cur)
        for r in rows:
            r.sort(key=lambda c: c[1])
            t, last = "", None
            for c in r:
                if last and c[1] - last[3] > 0.25 * c[4] and c[0] != " " and last[0] != " ":
                    t += " "
                t += c[0]
                last = c
            t = t.replace(" ", " ").strip()
            if t:
                res.append({"page": pno, "text": t, "x0": r[0][1], "x1": r[-1][3], "y0": r[0][2], "size": r[0][4] or 10.0})
    doc.close()
    return res
