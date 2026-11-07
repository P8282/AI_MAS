"""조문 안의 인용(제N조…)을 찾아 같은 규정·다른 규정의 조문 ID 로 연결한다. (파생 데이터 — 원문이 아님)"""
import re

ART_REF = re.compile(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?")
CONT_GAP = re.compile(r"^(?:\s|,|ㆍ|·|및|또는|과|와|이나|부터|까지|제\s*\d+\s*항|제\s*\d+\s*호|[가-하]목|제\s*\d+\s*조(?:의\s*\d+)?|\([^)]{0,12}\))*$")
BRACKET = re.compile(r"[「『“\"]([^「『“」』”\"]{2,80})[」』”\"]\s*$")      # 여는 괄호는 이름에 넣지 않음(“「민법」 → 민법)
GENERIC = re.compile(r"(?<![가-힣])(시행규칙|시행령|규칙|법|영)\s*$")
SELF_WORD = re.compile(r"(?<![가-힣])이\s*(?:법|영|규칙|예규|기준|세부기준|규정|고시|지침|훈령|조건|특수조건)\s*$")
SAME_WORD = re.compile(r"(?:같은|동)\s*(?:법|영|규칙|령|규정|조건|예규|고시|훈령|지침|기준|시행령|시행규칙)\s*$|동법\s*$|동령\s*$")
DEF_TAIL = re.compile(r"[「『]([^」』]{2,80})[」』]\s*\(\s*이하[^()]{0,60}\)\s*$")
EXT_LAW = re.compile(r"(?<![가-힣])([가-힣]{1,12}(?:법|법률))\s*$")
DEF_RE = re.compile(r"「([^」]{2,80})」\s*\(\s*이하\s*[“\"‘']?([^”\"’'\)]{1,30}?)[”\"’']?\s*(?:이?라|라)\s*한다")


def ns(s):
    return re.sub(r"\s+", "", s)


class Resolver:
    def __init__(self, regs):
        """regs: [{id,title,short,aliases,group,...}]"""
        self.regs = {r["id"]: r for r in regs}
        pairs = []
        for r in regs:
            for name in {r["title"], r["short"], *r["aliases"]}:
                n = ns(name.replace("(계약예규)", "").replace("ㆍ", "·"))
                if len(n) >= 3 or n in ("민법", "헌법"):      # 2글자 이름은 "민법"·"헌법"만 허용(다른 2글자 약칭은 일반 낱말과 겹칠 수 있음)
                    pairs.append((n, r["id"]))
        # 짧은 별칭이 여러 규정에 걸리면(예: 'MAS 업무처리규정') 제외
        cnt = {}
        for n, i in pairs:
            cnt.setdefault(n, set()).add(i)
        self.names = sorted([(n, next(iter(v))) for n, v in cnt.items() if len(v) == 1], key=lambda x: -len(x[0]))
        self.by_exact = {n: i for n, i in self.names}
        self.family = {}
        for r in regs:
            m = re.match(r"^(.*)_(ACT|ENFORCEMENT|RULE)$", r["id"])
            if m:                                   # 법·시행령·시행규칙 3종 세트: '법'·'영'·'규칙' 은 같은 세트 안의 규정을 가리킨다
                self.family[r["id"]] = m.group(1)
            else:                                   # 예규·조달청 규정 등은 국가계약 세트(지방계약 문서는 지방계약 세트)를 기본으로
                self.family[r["id"]] = {"국가계약": "STATE_CONTRACT", "조달청": "STATE_CONTRACT", "지방계약": "LOCAL_CONTRACT"}.get(r["group"])

    @staticmethod
    def _spaced_before(text, n):
        """text 끝의 n글자(공백 제외)가 앞 낱말과 띄어 쓰였는지: '예정가격은 국가계약법' 의 '국가계약법' 은 True"""
        cnt = 0
        for i in range(len(text) - 1, -1, -1):
            if not text[i].isspace():
                cnt += 1
                if cnt == n:
                    return i == 0 or text[i - 1].isspace() or not re.match(r"[가-힣]", text[i - 1])
        return True

    def generic(self, reg_id, word):
        fam = self.family.get(reg_id)
        if not fam:
            return None
        m = {"법": "ACT", "영": "ENFORCEMENT", "시행령": "ENFORCEMENT", "규칙": "RULE", "시행규칙": "RULE"}[word]
        rid = f"{fam}_{m}"
        return rid if rid in self.regs else None

    def lookup(self, name):
        n = ns(name.replace("(계약예규)", "").replace("ㆍ", "·"))
        n = re.sub(r"^(?:구|현행|개정)", "", n)
        return self.by_exact.get(n)

    def local_defs(self, text):
        d = {}
        for m in DEF_RE.finditer(text):
            rid = self.lookup(m.group(1))
            if rid:
                d[ns(m.group(2)).strip("「」『』“”\"'‘’")] = rid
        return d

    def extract(self, reg_id, text, local, last_explicit):
        """text 안의 조 인용 → [(target_reg_id|None, num, branch, ext_name|None)]; local: 정의된 약칭 → 규정ID"""
        out, prev_end, prev_t = [], -1, None
        for m in ART_REF.finditer(text):
            num, br = int(m.group(1)), int(m.group(2) or 0)
            pre = text[max(0, m.start() - 70):m.start()]
            pre_s = pre.rstrip()
            target, ext = None, None
            explicit = False
            if prev_end >= 0 and CONT_GAP.match(text[prev_end:m.start()]) and prev_t is not None and m.start() - prev_end < 40:
                target, ext = prev_t
                explicit = True
            else:
                d = DEF_TAIL.search(pre_s)
                b = BRACKET.search(pre_s)
                if d:
                    rid = self.lookup(d.group(1))
                    target, ext, explicit = (rid, None if rid else d.group(1).strip(), True)
                elif b:
                    rid = self.lookup(b.group(1))
                    target, ext, explicit = (rid, None if rid else b.group(1).strip(), True)
                else:
                    nsp = ns(pre_s)
                    for name, rid in self.names:
                        if nsp.endswith(name) and (len(nsp) == len(name) or not re.match(r"[가-힣]", nsp[-len(name) - 1]) or self._spaced_before(pre_s, len(name))):
                            target, explicit = rid, True
                            break
                    if not explicit:
                        if SELF_WORD.search(pre_s):
                            target, explicit = reg_id, True
                        elif SAME_WORD.search(pre_s):
                            lt = last_explicit.get("t")
                            target, ext = lt if lt else (reg_id, None)
                            explicit = True
                        else:
                            g = GENERIC.search(pre_s)
                            if g:
                                w = g.group(1)
                                rid = local.get(w) or self.generic(reg_id, w)
                                if rid:
                                    target, explicit = rid, True
                    if not explicit:
                        e = EXT_LAW.search(pre_s)
                        if e and not SELF_WORD.search(pre_s):
                            target, ext, explicit = None, e.group(1), True
                    if not explicit:
                        # 조(제N조)가 없는 개요형 문서의 '제73조' 는 자기 문서가 아니라 다른 법령 조문(시행령 등)을 가리키므로 연결하지 않는다
                        target = None if self.regs[reg_id].get("format") == "outline" else reg_id
            prev_end, prev_t = m.end(), (target, ext)
            if explicit and (target != reg_id or ext):
                last_explicit["t"] = (target, ext)
            out.append((target, num, br, ext))
        return out
