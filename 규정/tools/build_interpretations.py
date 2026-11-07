#!/usr/bin/env python3
"""조달청 법령해석(법제처 중앙부처 법령해석 API, target=ppsCgmExpc)을 규정 DB 옆의 '관련 해석' 데이터로 만든다.

  python3 규정/tools/build_interpretations.py [--src 규정/tools/_lawgokr/_cgm/ppsCgmExpc.json]

입력: fetch_lawgokr.py --cgm ppsCgmExpc 가 만든 파일 {"list":[…], "bodies":{일련번호: 본문}} (저장소에는 올리지 않고 data/pps-cgm 브랜치에 있음)
출력: 법령해석/조달청/
  index.json        안건 목록(본문 없음): 번호·제목·해석일·키워드·인용한 조문 ID  — 검색·목록용
  links.json        조문 ID → 안건 번호들(최근 해석 순)  — 조문 상세의 '관련 조달청 해석'용
  by-year/<연도>.json   안건 번호 → {q:[질의요지 문단], a:[회답 문단]}  — 눌렀을 때만 불러옴
규정 조문 자체(articles.json)나 data/*.json 은 건드리지 않는다. 원문이 아니라 법제처가 공개한 조달청 민원 회신이므로 법적 효력이 없다.
개인 식별 가능성이 있는 값(민원 신청번호·이메일·전화번호)은 가린다.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
warnings.filterwarnings("ignore", category=SyntaxWarning)
import merge_api_regs as M  # noqa: E402  (pymupdf 자리표시, split_items·Keywords·make_resolver 재사용)

ROOT = M.ROOT
OUT = os.path.join(ROOT, "법령해석", "조달청")
TODAY = datetime.date.today().isoformat()

MASKS = [
    (re.compile(r"신청번호\s*[:：]?\s*[0-9A-Za-z][0-9A-Za-z-]*"), "신청번호 [비공개]"),
    (re.compile(r"\b[0-9][A-Z]{2}-\d{4}-\d{6,8}\b"), "[민원번호 비공개]"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[이메일 비공개]"),
    (re.compile(r"(?<![\d-])0\d{1,2}[-.)\s]?\d{3,4}[-.\s]\d{4}(?!\d)"), "[전화번호 비공개]"),
]


def mask(t):
    for rx, rep in MASKS:
        t = rx.sub(rep, t)
    return t


def iso(d8):
    d = re.sub(r"\D", "", d8 or "")
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else ""


def paragraphs(text):
    """한 덩어리로 이어진 질의요지·회답을 읽기 좋게 나눈다: '1. 2. 3.' 번호와 '<질의N 관련>' 표지 앞에서 줄을 바꾼다(글자는 그대로)."""
    t = mask(M.clean(text))
    lead, items = M.split_items(t)
    out = ([lead] if lead else []) + [f"{n}. {body}" for n, _br, body in items]
    res = []
    for p in out:
        res += [x.strip() for x in re.split(r"(?=<질의\s*\d+\s*관련>)", p) if x.strip()]
    return res or ([t] if t else [])


def list_only(src, a):
    """본문 없이 목록만: {id, t(안건명), n(안건번호), d(해석일자), u(상세링크 중 국세법령정보시스템 문서번호)}. 링크는 화면에서 이 번호로 조립."""
    pre = "https://taxlaw.nts.go.kr/qt/USEQTA002P.do?ntstDcmId="
    rows = []
    for it in src["list"]:
        i = str(it.get("법령해석일련번호") or "")
        link = it.get("법령해석상세링크") or ""
        if not i or not link.startswith(pre):
            continue
        rows.append({"id": i, "t": mask(M.clean(it.get("안건명") or "")), "n": mask(M.clean(it.get("안건번호") or "")),
                     "d": iso(it.get("해석일자")), "u": link[len(pre):]})
    rows.sort(key=lambda r: (r["d"], r["id"]), reverse=True)
    build_id = hashlib.sha1(json.dumps([r["id"] for r in rows]).encode()).hexdigest()[:10]
    M.jdump(os.path.join(OUT, "index.json"), {
        "schema_version": "1.0", "generated": TODAY, "build_id": build_id, "target": a.target, "kind": "list", "link_prefix": pre,
        "source": "법제처 국가법령정보 공동활용 API — 중앙부처 법령해석(" + a.org + ", 목록만)",
        "disclaimer": a.org + " 법령해석은 법제처 API 가 본문을 제공하지 않아 제목·번호·날짜만 보여 줘요. 누르면 국세법령정보시스템(외부 사이트)의 원문으로 이동해요.",
        "count": len(rows), "items": rows})
    print(f"{a.org} 해석 목록 {len(rows)}건 (본문 없음)")
    return 0


def analyse(items, bodies, resolver, article_ids):
    """안건마다 제목·질의요지·회답(문단 나눔, 개인정보 가림)과 인용한 규정 조문 ID 를 뽑는다. 반환: rows[{id,t,d,a}], texts{id: (제목, 질의, 회답, 전체글, 날짜)}"""
    rows, texts = [], {}
    for it in items:
        i = str(it["법령해석일련번호"])
        b = bodies.get(i)
        if not b:
            continue
        title = mask(M.clean(b.get("안건명") or it.get("안건명") or ""))
        q, an = paragraphs(b.get("질의요지") or ""), paragraphs(b.get("회답") or "")
        full = "\n".join(q + an)
        arts, last = [], {}
        for target, num, br, _ext in resolver.extract("INTERP", full, {}, last):
            if target and target != "INTERP":
                aid = f"{target}_ART_{num}" + (f"_{br}" if br else "")
                if aid in article_ids and aid not in arts:
                    arts.append(aid)
        date = iso(b.get("해석일자") or it.get("해석일자"))
        rows.append({"id": i, "t": title, "d": date, "a": arts})
        texts[i] = (title, q, an, full, date)
    return rows, texts


def append_mode(a):
    """이미 만든 해석(index.json·links.json·by-year)에 새 안건 본문만 더한다. --append 파일: {"bodies": {번호: 법제처 응답}} 또는 관리자모드 내려받기 파일의 pps_items.
    기존 안건의 키워드·문단은 그대로 두고, 키워드 계산에 쓰는 용어 빈도(df)는 기존 안건의 저장된 글에서 다시 세어 새 안건 것과 합친다."""
    env = M.jload(a.append)
    if "pps_items" in env:
        raw = {str(x["id"]): json.loads(x["body_text"]) for x in env["pps_items"]}
    else:
        raw = env["bodies"]
    bodies = {k: v[next(iter(v))] for k, v in raw.items()}
    regs = M.jload(os.path.join(M.OUT, "config", "registry.json"))["regulations"] + M.jload(os.path.join(M.OUT, "config", "registry-api.json"))["regulations"]
    regs.append({"id": "INTERP", "title": a.org + " 법령해석", "short": a.org + " 법령해석", "aliases": [], "group": "", "format": "outline"})
    resolver = M.make_resolver(regs)
    article_ids = {r["id"] for r in M.jload(os.path.join(M.OUT, "data", "regulation-index.json"))["articles"]}
    kw = M.Keywords(M.jload(os.path.join(M.OUT, "config", "keyword-dictionary.json"))["terms"])

    old = M.jload(os.path.join(OUT, "index.json"))
    old_rows = {r["id"]: r for r in old["items"]}
    year_dir = os.path.join(OUT, "by-year")
    by_year = {}
    for fn in sorted(os.listdir(year_dir)):
        by_year[fn[:-5]] = M.jload(os.path.join(year_dir, fn))
    new_ids = [i for i in bodies if i not in old_rows]
    if not new_ids:
        print("새로 더할 안건이 없어요(이미 모두 들어 있음)."); return 0
    rows_new, texts = analyse([{"법령해석일련번호": i} for i in new_ids], bodies, resolver, article_ids)

    # 키워드 용어 빈도(df): 기존 안건의 저장된 글(제목+질의+회답)로 다시 세고, 새 안건을 더한다
    for r in old["items"]:
        qa = by_year.get((r["d"] or "0000")[:4], {}).get(r["id"], {})
        kw.count(r["t"], "\n".join((qa.get("q") or []) + (qa.get("a") or [])))
    n_total = max(1, len(old_rows) + len(rows_new))
    stats = {r["id"]: kw.count(texts[r["id"]][0], texts[r["id"]][3]) for r in rows_new}
    for r in rows_new:
        r["k"] = kw.pick(stats[r["id"]], n_total)
        _t, q, an, _f, date = texts[r["id"]]
        by_year.setdefault((date or "0000")[:4], {})[r["id"]] = {"q": q, "a": an}
    rows = list(old["items"]) + rows_new
    rows.sort(key=lambda r: (r["d"], r["id"]), reverse=True)
    links = {}
    for r in rows:
        for aid in r["a"]:
            links.setdefault(aid, []).append(r["id"])
    build_id = hashlib.sha1(json.dumps([r["id"] for r in rows]).encode()).hexdigest()[:10]
    repl = {"generated": TODAY, "build_id": build_id, "count": len(rows), "items": rows}
    M.jdump(os.path.join(OUT, "index.json"), {k: repl.get(k, v) for k, v in old.items()})      # 기존 키 순서를 그대로 둬서 바뀐 줄만 diff 에 나오게
    old_links = M.jload(os.path.join(OUT, "links.json"))
    M.jdump(os.path.join(OUT, "links.json"), {k: {"generated": TODAY, "build_id": build_id, "links": links}.get(k, v) for k, v in old_links.items()})
    touched = {(texts[i][4] or "0000")[:4] for i in texts}
    for y in touched:
        M.jdump(os.path.join(year_dir, f"{y}.json"), by_year[y])
    print(f"{a.org} 새 해석 {len(rows_new)}건 추가 → 전체 {len(rows)}건 · 연결된 새 해석 {sum(1 for r in rows_new if r['a'])}건 · 갱신한 연도 {sorted(touched)}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="ppsCgmExpc", help="법제처 target 이름(기본 ppsCgmExpc=조달청)")
    ap.add_argument("--out", default="조달청", help="법령해석/ 아래 출력 폴더 이름(기본 조달청)")
    ap.add_argument("--org", default="조달청", help="기관 표시 이름(기본 조달청)")
    ap.add_argument("--list-only", action="store_true", help="본문이 제공되지 않는 기관(예: 재정경제부)용: 목록만으로 index.json 만들기(링크는 상세링크)")
    ap.add_argument("--append", default=None, help="새 안건 본문만 기존 결과에 더함(관리자모드 내려받기 파일의 pps_items 또는 {bodies:{…}})")
    ap.add_argument("--src", default=None, help="받은 파일(기본 규정/tools/_lawgokr/_cgm/<target>.json)")
    a = ap.parse_args()
    global OUT
    OUT = os.path.join(ROOT, "법령해석", a.out)
    a.src = a.src or os.path.join(ROOT, "규정", "tools", "_lawgokr", "_cgm", a.target + ".json")
    if a.append:
        return append_mode(a)
    src = M.jload(a.src)
    if a.list_only:
        return list_only(src, a)
    bodies = {k: v[next(iter(v))] for k, v in src["bodies"].items()}

    # 인용 해석: 규정 DB 의 이름으로 '「법령명」 제N조' 를 조문 ID 로 연결(번호만 있는 '제N조'는 연결하지 않음)
    regs = M.jload(os.path.join(M.OUT, "config", "registry.json"))["regulations"] + M.jload(os.path.join(M.OUT, "config", "registry-api.json"))["regulations"]
    regs.append({"id": "INTERP", "title": a.org + " 법령해석", "short": a.org + " 법령해석", "aliases": [], "group": "", "format": "outline"})
    resolver = M.make_resolver(regs)
    idx = M.jload(os.path.join(M.OUT, "data", "regulation-index.json"))
    article_ids = {r["id"] for r in idx["articles"]}

    kw = M.Keywords(M.jload(os.path.join(M.OUT, "config", "keyword-dictionary.json"))["terms"])
    rows, texts = analyse(src["list"], bodies, resolver, article_ids)
    n_total = max(1, len(rows))
    stats = {r["id"]: kw.count(texts[r["id"]][0], texts[r["id"]][3]) for r in rows}
    for r in rows:
        r["k"] = kw.pick(stats[r["id"]], n_total)
    rows.sort(key=lambda r: (r["d"], r["id"]), reverse=True)

    links = {}
    for r in rows:
        for aid in r["a"]:
            links.setdefault(aid, []).append(r["id"])
    by_year = {}
    for r in rows:
        title, q, an, _full, date = texts[r["id"]]
        by_year.setdefault((date or "0000")[:4], {})[r["id"]] = {"q": q, "a": an}

    build_id = hashlib.sha1(json.dumps([r["id"] for r in rows]).encode()).hexdigest()[:10]
    meta = {"schema_version": "1.0", "generated": TODAY, "build_id": build_id, "target": a.target,
            "source": "법제처 국가법령정보 공동활용 API — 중앙부처 법령해석(" + a.org + ")",
            "disclaimer": a.org + ("이" if (ord(a.org[-1]) - 0xAC00) % 28 else "가") + " 민원 질의에 회신한 상담 내용으로, 법적 효력이 없습니다. 원문 그대로가 아니라 읽기 좋게 문단만 나눴고 신청번호·이메일·전화번호는 가렸습니다."}
    M.jdump(os.path.join(OUT, "index.json"), {**meta, "count": len(rows), "items": rows})
    M.jdump(os.path.join(OUT, "links.json"), {**meta, "links": links})
    for y, d in by_year.items():
        M.jdump(os.path.join(OUT, "by-year", f"{y}.json"), d)
    n_linked = sum(1 for r in rows if r["a"])
    print(f"{a.org} 해석 {len(rows)}건 · 조문에 연결된 해석 {n_linked}건 · 연결된 조문 {len(links)}개 · 연도 {sorted(by_year)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
