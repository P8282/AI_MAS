#!/usr/bin/env python3
"""대응 조문 표 만들기 → 규정/data/correspondence.json

국가계약법 계열 ↔ 지방계약법 계열 ↔ 방위사업법 계열, 그리고 적격심사·계약이행능력심사 규정끼리
"같은 내용을 다루는 조문"을 서로 이어 준다. (규정 검색 조문 상세의 '대응 조문' 항목이 읽음)

  python3 규정/tools/build_correspondence.py          # 만들기
  python3 규정/tools/build_correspondence.py --show   # 묶음 목록만 보기

방식: ① 같은 종류(법/시행령/시행규칙)끼리 조문 제목이 같으면(동의어 보정 후, 일대일일 때만) 자동으로 이음
      ② 방위사업법 계열처럼 구조가 다른 것은 MANUAL 묶음으로 직접 지정
      ③ 심사 규정 6개는 제목의 핵심어(KEYS)가 같은 조문끼리 이음
      묶음에 든 조문은 서로 모두 대응 조문이 된다(양방향). 틀린 것은 MANUAL_DROP 으로 뺀다.
"""
import json, os, re, sys, collections

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
REG = lambda slug: os.path.join(ROOT, "regulations", slug, "articles.json")

LAW = {   # 같은 종류끼리 제목으로 잇는 쌍
    "법": ["국가계약법", "지방계약법"],
    "시행령": ["국가계약법-시행령", "지방계약법-시행령"],
    "시행규칙": ["국가계약법-시행규칙", "지방계약법-시행규칙"],
}
SYN = [("지연배상금", "지체상금"), ("계약담당공무원", "계약담당자"), ("국가계약", "계약"), ("지방계약", "계약")]

# 방위사업법 계열은 구조가 달라 직접 지정: (규정, 조문번호)들이 한 묶음. 한 묶음 안의 조문은 서로 대응.
MANUAL = [
    {"topic": "지체상금(지연배상금)", "members": [
        ("국가계약법", "제26조"), ("국가계약법-시행령", "제74조"), ("국가계약법-시행규칙", "제75조"),
        ("지방계약법", "제30조"), ("지방계약법-시행령", "제90조"), ("지방계약법-시행규칙", "제75조"),
        ("방위사업법", "제46조의4"), ("방위사업법-시행령", "제61조의9"), ("방위사업법-시행령", "제61조의10"), ("방위사업법-시행규칙", "제37조의2")]},
]
MANUAL_DROP = []   # [("규정", "조문번호", "규정", "조문번호")] 자동으로 이어진 것 중 틀린 쌍을 뺀다

REVIEW = ["적격심사기준", "조달청-물품구매적격심사-세부기준", "방위사업청-물품-적격심사-기준",
          "조달청-경쟁물품-계약이행능력심사-세부기준", "중기부-경쟁제품-계약이행능력심사-세부기준", "방위사업청-경쟁제품-계약이행능력심사-세부기준"]
KEYS = [   # (핵심어 이름, 제목에 들어 있어야 하는 정규식)
    ("심사대상", r"심사대상"), ("배점", r"배점"), ("제출서류", r"^제출서류|심사자료요구"), ("평가방법", r"평가방법|^심사방법"),
    ("공동수급체", r"공동수급체"), ("결격", r"결격"), ("재심사", r"^재심사"), ("낙찰자 결정", r"^낙찰자결정$"),
    ("부정제출", r"부정한방법"), ("위원회", r"위원회구성"), ("생산능력", r"생산능력"), ("평가결과 설명", r"평가결과설명"), ("신인도", r"신인도"),
]

def load(slug):
    a = json.load(open(REG(slug), encoding="utf-8"))
    return [x for x in a if not x.get("is_deleted") and not x.get("is_supplementary") and not x.get("is_scheduled")]

def nt(t):
    return re.sub(r"[\s·ㆍ,()「」]", "", t or "")

def syn(t):
    t = nt(t)
    for a, b in SYN: t = t.replace(a, b)
    return t

def main():
    show = "--show" in sys.argv
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b): parent[find(a)] = find(b)
    info = {}    # id → (규정, 조문번호)

    # ① 국가 ↔ 지방: 같은 종류끼리 제목이 같은 조문 (일대일일 때만)
    for kind, (a_slug, b_slug) in LAW.items():
        A, B = load(a_slug), load(b_slug)
        for x in A + B: info[x["id"]] = (x["regulation_id"] if False else (a_slug if x in A else b_slug), x["article_label"])
        byb = collections.defaultdict(list)
        for y in B: byb[syn(y["article_title"])].append(y)
        bya = collections.Counter(syn(x["article_title"]) for x in A)
        for x in A:
            k = syn(x["article_title"])
            if not k or k in ("목적", "정의", "시행일", "기타사항") or bya[k] != 1 or len(byb.get(k, [])) != 1: continue
            union(x["id"], byb[k][0]["id"])

    # ② 직접 지정한 묶음
    lab = {}
    for slug in sum(LAW.values(), []) + ["방위사업법", "방위사업법-시행령", "방위사업법-시행규칙"]:
        for x in load(slug): lab[(slug, x["article_label"])] = x["id"]; info[x["id"]] = (slug, x["article_label"])
    for m in MANUAL:
        ids = []
        for slug, label in m["members"]:
            if (slug, label) not in lab: print("!! 없는 조문:", slug, label); continue
            ids.append(lab[(slug, label)])
        for i in ids[1:]: union(ids[0], i)

    # ③ 심사 규정: 핵심어가 같은 조문끼리
    byk = collections.defaultdict(list)
    for slug in REVIEW:
        seen = set()
        for x in load(slug):
            info[x["id"]] = (slug, x["article_label"])
            t = nt(x["article_title"])
            for name, rx in KEYS:
                if re.search(rx, t) and (slug, name) not in seen:
                    seen.add((slug, name)); byk[name].append(x["id"]); break
    for name, ids in byk.items():
        for i in ids[1:]: union(ids[0], i)

    groups = collections.defaultdict(list)
    for i in list(parent): groups[find(i)].append(i)
    drop = set()
    for a, al, b, bl in MANUAL_DROP:
        if (a, al) in lab and (b, bl) in lab: drop.add(frozenset((lab[(a, al)], lab[(b, bl)])))
    links = {}
    for g in groups.values():
        if len(g) < 2: continue
        for i in g:
            others = [j for j in g if j != i and frozenset((i, j)) not in drop]
            if others: links[i] = others
    if show:
        for g in groups.values():
            if len(g) > 1: print(" | ".join(f"{info.get(i, ('?', i))[0]} {info.get(i, ('?', i))[1]}" for i in g))
        print(len([g for g in groups.values() if len(g) > 1]), "묶음 /", len(links), "조문")
        return
    out = {"schema_version": "1.0", "_comment": "규정/tools/build_correspondence.py 로 만든 대응 조문 표 (조문 ID → 대응 조문 ID들). 직접 고치려면 스크립트의 MANUAL / MANUAL_DROP 을 고치고 다시 실행.",
           "links": {k: v for k, v in sorted(links.items())}}
    p = os.path.join(ROOT, "data", "correspondence.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("만듦:", p, len(links), "조문")

if __name__ == "__main__":
    main()
