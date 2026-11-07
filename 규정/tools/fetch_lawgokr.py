#!/usr/bin/env python3
"""표준교재의 '규정 DB 에 없는 법령'(표준교재/data/relations.json › external_references)을
국가법령정보 공동활용 API(open.law.go.kr)에서 받아 원본 JSON 으로 저장한다.

  LAWGO_OC=<OC값> python3 규정/tools/fetch_lawgokr.py            # 전체 받기
  python3 규정/tools/fetch_lawgokr.py --dry-run                  # 대상 목록만 출력(네트워크·OC 불필요)
  LAWGO_OC=... python3 규정/tools/fetch_lawgokr.py --only 물품관리법

저장:  규정/tools/_lawgokr/<법령명>.json (본문) · _search/<법령명>.json (검색 응답) · report.json (결과 요약)
이 도구는 data/·regulations/ 를 건드리지 않는다(기존 DB 와 ID 는 그대로).
OC 값은 코드·저장소에 넣지 말고 환경 변수 LAWGO_OC 로만 전달한다.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
REL = os.path.join(ROOT, "표준교재", "data", "relations.json")
OUT = os.path.join(HERE, "_lawgokr")
BASE = "https://www.law.go.kr/DRF/"


def norm(s):
    return re.sub(r"[\s·ㆍ]", "", s or "")


def get(endpoint, params, retries=3):
    url = BASE + endpoint + "?" + urllib.parse.urlencode(params)
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # 네트워크·JSON 오류는 재시도 후 호출부로
            if i == retries - 1:
                raise
            time.sleep(2 ** (i + 1))


def pick(search, name):
    """검색 응답에서 법령명이 정확히 같은 항목을 고른다(없으면 None). 응답 키는 실제 응답 확인 전이라 방어적으로 읽는다."""
    root = search.get("LawSearch", search)
    items = root.get("law") or []
    if isinstance(items, dict):
        items = [items]
    exact = [x for x in items if norm(x.get("법령명한글")) == norm(name)]
    return exact[0] if exact else None


def save(obj, path, oc):
    """JSON 저장. 응답의 상세링크 등에 요청한 인증키(OC)가 그대로 들어 있어서 저장 전에 가린다."""
    txt = json.dumps(obj, ensure_ascii=False, indent=1)
    if oc:
        txt = txt.replace(oc, "OC_REDACTED")
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)


def admrul_search(a):
    """행정규칙(조달청 규정·고시·계약예규 등) 검색 응답을 _lawgokr/_admrul/_search/ 에 저장. 본문 받기는 응답 구조를 확인한 뒤에 추가한다."""
    oc = os.environ.get("LAWGO_OC", "").strip()
    if not oc:
        sys.exit("환경 변수 LAWGO_OC 가 필요합니다.")
    names = json.load(open(os.path.join(OUT, "report.json"), encoding="utf-8"))["not_found"]
    if a.only:
        names = [n for n in names if a.only in n]
    d = os.path.join(OUT, "_admrul", "_search")
    os.makedirs(d, exist_ok=True)
    for i, n in enumerate(names, 1):
        print(f"[{i}/{len(names)}] {n}", flush=True)
        s = get("lawSearch.do", {"OC": oc, "target": "admrul", "type": "JSON", "query": n})
        if "result" in s and "AdmRulSearch" not in s and "LawSearch" not in s:
            print("  응답:", s.get("result"), "/", s.get("msg"))
        save(s, os.path.join(d, re.sub(r'[\\/:*?"<>|]', "_", n) + ".json"), oc)
        time.sleep(0.3)
    return 0


LAW_TYPES = {"법률", "시행령", "시행규칙", "부령", "대통령령", "헌법"}      # registry type → 법령(law) 검색, 나머지는 행정규칙(admrul)
SKIP_TYPES = {"법령해석례"}


def find_exact(oc, target, name, query=None):
    """target(law|admrul) 검색에서 이름이 정확히 같은 항목을 찾는다(여러 쪽, 이름이 같은 것이 여럿이면 시행일자가 가장 늦은 것). 반환: (항목 또는 None, 첫 쪽 응답)
    query: 검색어를 이름과 다르게 줄 때(아주 긴 이름은 이름 전체로 검색하면 0건인 경우가 있어 짧은 단어로 검색). 이름 비교는 그대로 name 으로 한다."""
    key, root_key, name_key = ("law", "LawSearch", "법령명한글") if target == "law" else ("admrul", "AdmRulSearch", "행정규칙명")
    first = get("lawSearch.do", {"OC": oc, "target": target, "type": "JSON", "query": query or name, "display": 100})
    if "result" in first and root_key not in first:
        sys.exit(f"법제처 응답 오류: {first.get('result')} / {first.get('msg')}\n(이 PC 의 현재 공인 IP 가 신청서에 등록된 IP 와 같은지 확인하세요: curl https://api.ipify.org)")
    hits, page, resp = [], 1, first
    while True:
        root = resp.get(root_key, resp)
        items = root.get(key) or []
        if isinstance(items, dict):
            items = [items]
        hits += [x for x in items if norm(x.get(name_key)) == norm(name)]
        total = int(root.get("totalCnt") or 0)
        if page * 100 >= total or page >= 5:
            break
        page += 1
        resp = get("lawSearch.do", {"OC": oc, "target": target, "type": "JSON", "query": query or name, "display": 100, "page": page})
    hits.sort(key=lambda x: x.get("시행일자") or "", reverse=True)
    return (hits[0] if hits else None), first


def db_check(a):
    """규정 DB 의 규정마다 법제처 현행 시행일을 조회해 DB(metadata.json)의 시행일과 비교한다. 결과: _lawgokr/_db/db_check.json (본문은 받지 않음, 검색 응답만 _db/_search/ 에 저장)."""
    oc = os.environ.get("LAWGO_OC", "").strip()
    if not oc:
        sys.exit("환경 변수 LAWGO_OC 가 필요합니다.")
    reg = json.load(open(os.path.join(ROOT, "규정", "config", "registry.json"), encoding="utf-8"))["regulations"]
    onlys = [x for x in (a.only or "").split(",") if x]       # --only 는 쉼표로 여러 개, 규정 ID 또는 이름 일부
    reg = [r for r in reg if r["type"] not in SKIP_TYPES and (not onlys or any(o == r["id"] or o in r["title"] for o in onlys))]
    d = os.path.join(OUT, "_db", "_search")
    os.makedirs(d, exist_ok=True)
    rows = []
    for i, r in enumerate(reg, 1):
        title = re.sub(r"^\((?:계약예규|고시|훈령|예규|지침)\)\s*", "", r["title"])
        target = "law" if r["type"] in LAW_TYPES else "admrul"
        print(f"[{i}/{len(reg)}] {r['title']}", flush=True)
        row = {"id": r["id"], "title": r["title"], "type": r["type"], "target": target}
        try:
            hit, first = None, None
            meta_path = os.path.join(ROOT, "규정", "regulations", r["slug"], "metadata.json")
            meta = json.load(open(meta_path, encoding="utf-8")) if os.path.exists(meta_path) else {}
            full = re.sub(r"^\((?:계약예규|고시|훈령|예규|지침)\)\s*", "", meta.get("name") or "")      # registry title 이 PDF 머리말 줄바꿈으로 잘린 규정은 metadata.name 이 온전한 이름
            for nm in dict.fromkeys([x for x in (full, meta.get("name"), title, r["title"]) if x]):   # 접두어를 뗀 이름 → 원래 이름 순서로 시도
                hit, first = find_exact(oc, target, nm)
                if hit:
                    break
            save(first, os.path.join(d, re.sub(r'[\\/:*?"<>|]', "_", r["id"]) + ".json"), oc)
            db_eff = (meta.get("effective_date") or "").replace("-", "")
            if not hit:
                row.update(status="not_found", db_effective=db_eff)
            else:
                api_eff = hit.get("시행일자") or ""
                row.update(api_name=hit.get("법령명한글") or hit.get("행정규칙명"), api_effective=api_eff, api_number=hit.get("공포번호") or hit.get("발령번호"),
                           db_effective=db_eff, db_number=meta.get("number"),
                           status="same" if api_eff == db_eff else ("api_newer" if api_eff > db_eff else
                                    ("db_scheduled" if any((v.get("effective_date") or "").replace("-", "") == api_eff for v in meta.get("versions", [])) else "db_newer")))   # db_scheduled: DB 에 시행 예정본이 더 있고 현행본도 함께 보관 중
        except Exception as e:
            row.update(status="error", error=str(e))
        rows.append(row)
        time.sleep(0.3)
    os.makedirs(os.path.join(OUT, "_db"), exist_ok=True)
    save(rows, os.path.join(OUT, "_db", "db_check.json"), oc)
    cnt = {}
    for x in rows:
        cnt[x["status"]] = cnt.get(x["status"], 0) + 1
    print(cnt)
    for x in rows:
        if x["status"] != "same":
            print(x["status"], "|", x["title"], "| DB", x.get("db_effective"), "| API", x.get("api_effective"))
    return 0


def lookup(a):
    """검색어로 이름 후보를 보여 준다(이름 확인용). 응답 키는 find_exact 와 같다."""
    oc = os.environ.get("LAWGO_OC", "").strip()
    if not oc:
        sys.exit("환경 변수 LAWGO_OC 가 필요합니다.")
    key, root_key, name_key = ("law", "LawSearch", "법령명한글") if a.target == "law" else ("admrul", "AdmRulSearch", "행정규칙명")
    r = get("lawSearch.do", {"OC": oc, "target": a.target, "type": "JSON", "query": a.lookup, "display": 20})
    if "result" in r and root_key not in r:
        sys.exit(f"법제처 응답 오류: {r.get('result')} / {r.get('msg')}")
    root = r.get(root_key, r)
    items = root.get(key) or []
    if isinstance(items, dict):
        items = [items]
    print(f"총 {root.get('totalCnt')}건 중 {len(items)}건")
    for x in items:
        print(x.get(name_key), "|", x.get("소관부처명"), "|", x.get("행정규칙종류") or x.get("법령구분명"), "| 시행", x.get("시행일자"), "|", x.get("현행연혁코드") or x.get("현행연혁구분"))
    return 0


def fetch_one(name):
    """법령명 하나의 현행 본문을 받아 _lawgokr/<법령명>.json 으로 저장(인증키는 가림)."""
    oc = os.environ.get("LAWGO_OC", "").strip()
    if not oc:
        sys.exit("환경 변수 LAWGO_OC 가 필요합니다.")
    hit, first = find_exact(oc, "law", name)
    if not hit:
        sys.exit(f"법제처에서 '{name}' 이름이 정확히 같은 현행 법령을 찾지 못했습니다.")
    body = get("lawService.do", {"OC": oc, "target": "law", "type": "JSON", "MST": hit.get("법령일련번호")})
    os.makedirs(OUT, exist_ok=True)
    fn = re.sub(r'[\\/:*?"<>|]', "_", name) + ".json"
    save(body, os.path.join(OUT, fn), oc)
    print(f"저장: {os.path.join(OUT, fn)}  (법령일련번호 {hit.get('법령일련번호')}, 시행일자 {hit.get('시행일자')}, 공포번호 {hit.get('공포번호')})")
    return 0


def fetch_rules(names):
    """행정규칙 이름(들)의 현행 본문을 받아 _lawgokr/<이름>.json 으로 저장(인증키는 가림). 이름은 법제처 행정규칙명과 같아야 한다((계약예규) 같은 접두어 포함)."""
    oc = os.environ.get("LAWGO_OC", "").strip()
    if not oc:
        sys.exit("환경 변수 LAWGO_OC 가 필요합니다.")
    os.makedirs(OUT, exist_ok=True)
    bad = 0
    for name in names:
        hit, first = find_exact(oc, "admrul", name)
        if not hit:
            print(f"못 찾음: {name}  (--lookup \"{name}\" 로 정확한 이름을 확인하세요)")
            bad += 1
            continue
        body = get("lawService.do", {"OC": oc, "target": "admrul", "type": "JSON", "ID": hit.get("행정규칙일련번호")})
        fn = re.sub(r'[\\/:*?"<>|]', "_", name) + ".json"
        save(body, os.path.join(OUT, fn), oc)
        print(f"저장: {fn}  (행정규칙일련번호 {hit.get('행정규칙일련번호')}, 시행일자 {hit.get('시행일자')}, {hit.get('행정규칙종류')}, 발령번호 {hit.get('발령번호')})")
        time.sleep(0.3)
    return 1 if bad else 0


def probe_cgm(cands):
    """중앙부처 법령해석(target=<기관>CgmExpc) 이름 확인: 후보마다 목록 1건을 조회해 결과 요약과 응답 앞부분을 출력(저장 안 함)."""
    oc = os.environ.get("LAWGO_OC", "").strip()
    if not oc:
        sys.exit("환경 변수 LAWGO_OC 가 필요합니다.")
    cands = cands or ["ppsCgmExpc", "ppsCgmexpc", "ppsCgmExp", "ppsExpc", "kostatCgmExpc"]   # kostatCgmExpc 는 공개 가이드에 있는 국가데이터처 것(비교용)
    for t in cands:
        try:
            r = get("lawSearch.do", {"OC": oc, "target": t, "type": "JSON", "display": 3})
        except Exception as e:
            print(f"[{t}] 오류: {e}")
            continue
        txt = json.dumps(r, ensure_ascii=False)
        if oc:
            txt = txt.replace(oc, "OC_REDACTED")
        print(f"[{t}] 최상위 키 {list(r)[:4]}")
        print("   ", txt[:700])
    return 0


def fetch_cgm(target, only=None):
    """중앙부처 법령해석 전체: 목록(display=100 쪽 넘기기) → 안건마다 본문. 결과는 _lawgokr/_cgm/<target>.json {"list":[…], "bodies":{일련번호: 본문}} 한 파일.
    이미 받은 본문은 건너뛰므로 중간에 멈춰도 다시 실행하면 이어 받는다. 인증·IP 오류가 나면 즉시 중단."""
    oc = os.environ.get("LAWGO_OC", "").strip()
    if not oc:
        sys.exit("환경 변수 LAWGO_OC 가 필요합니다.")
    d = os.path.join(OUT, "_cgm")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, re.sub(r'[\\/:*?"<>|]', "_", target) + ".json")
    state = jload_safe(path) or {"target": target, "list": [], "bodies": {}, "errors": {}}
    # 1) 목록
    items, page, total = [], 1, None
    while True:
        r = get("lawSearch.do", {"OC": oc, "target": target, "type": "JSON", "display": 100, "page": page})
        if "result" in r and not any(isinstance(v, dict) for v in r.values()):
            sys.exit(f"법제처 응답 오류: {r.get('result')} / {r.get('msg')}\n(공인 IP 가 신청서와 같은지, 이 target 을 신청했는지 확인하세요)")
        root = next(v for v in r.values() if isinstance(v, dict))
        total = int(root.get("totalCnt") or 0) if total is None else total
        rows = [v for k, v in root.items() if isinstance(v, (list, dict)) and k not in ("keyword",)]
        rows = rows[0] if rows else []
        rows = rows if isinstance(rows, list) else [rows]
        rows = [x for x in rows if isinstance(x, dict)]
        items += rows
        print(f"목록 {len(items)}/{total}", flush=True)
        if not rows or len(items) >= total or page >= 200:
            break
        page += 1
        time.sleep(0.3)
    state["list"] = items
    ids = [str(x.get("법령해석일련번호")) for x in items if x.get("법령해석일련번호")]
    if only:
        ids = [i for i in ids if only in i]
    todo = [i for i in ids if i not in state["bodies"]]
    print(f"본문 받기: 전체 {len(ids)}건 중 남은 {len(todo)}건", flush=True)
    for n, i in enumerate(todo, 1):
        try:
            b = get("lawService.do", {"OC": oc, "target": target, "type": "JSON", "ID": i})
            if "result" in b and not any(isinstance(v, dict) for v in b.values()):
                sys.exit(f"법제처 응답 오류: {b.get('result')} / {b.get('msg')}")
            state["bodies"][i] = b
            state["errors"].pop(i, None)
        except SystemExit:
            save(state, path, oc)
            raise
        except Exception as e:
            state["errors"][i] = str(e)
        if n % 25 == 0 or n == len(todo):
            save(state, path, oc)
            print(f"[{n}/{len(todo)}] 저장", flush=True)
        time.sleep(0.3)
    save(state, path, oc)
    print(f"완료: 목록 {len(items)}건, 본문 {len(state['bodies'])}건, 오류 {len(state['errors'])}건 → {path}")
    return 0


def jload_safe(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="이 이름이 들어간 법령만")
    ap.add_argument("--admrul", action="store_true", help="report.json 의 not_found 를 행정규칙(admrul)에서 검색해 응답만 저장")
    ap.add_argument("--lookup", help="검색어로 법제처에서 이름을 찾아 상위 20건의 이름·소관부처·시행일을 출력(이름 확인용, 아무것도 저장하지 않음)")
    ap.add_argument("--target", default="admrul", choices=["law", "admrul"], help="--lookup 의 검색 대상(기본 admrul)")
    ap.add_argument("--law", help="법령명을 정확히 지정해 현행 본문 하나를 받는다(외부 법령 목록과 무관, 예: --law \"중소기업 기술혁신 촉진법\"). 결과: _lawgokr/<법령명>.json")
    ap.add_argument("--rule", nargs="+", metavar="이름", help="행정규칙(훈령·예규·고시·지침·공고) 현행 본문을 이름을 정확히 지정해 받는다(여러 개 가능, 예: --rule \"조달물자 전문기관검사 업무규정\" \"(계약예규) 물품구매(제조)입찰유의서\"). 결과: _lawgokr/<이름>.json")
    ap.add_argument("--probe-cgm", nargs="*", metavar="target", help="중앙부처 법령해석 target 이름 후보로 목록 1건씩 조회해 응답을 출력(이름 확인용). 후보를 안 주면 조달청 후보들(ppsCgmExpc …)을 시험")
    ap.add_argument("--cgm", metavar="target", help="중앙부처 법령해석(예: ppsCgmExpc=조달청) 목록 전체와 본문을 받아 _lawgokr/_cgm/<target>.json 하나로 저장(중단 후 다시 실행하면 이어 받음)")
    ap.add_argument("--db", action="store_true", help="규정 DB(registry.json)의 규정마다 법제처 현행 시행일을 조회해 DB 의 시행일과 비교(본문은 받지 않음)")
    a = ap.parse_args()

    if a.lookup:
        return lookup(a)
    if a.law:
        return fetch_one(a.law)
    if a.probe_cgm is not None:
        return probe_cgm(a.probe_cgm)
    if a.cgm:
        return fetch_cgm(a.cgm, a.only)
    if a.rule:
        return fetch_rules(a.rule)
    if a.admrul:
        return admrul_search(a)
    if a.db:
        return db_check(a)
    names = list(json.load(open(REL, encoding="utf-8"))["external_references"])
    if a.only:
        names = [n for n in names if a.only in n]
    if a.dry_run:
        print(f"{len(names)}개")
        print("\n".join(names))
        return 0

    oc = os.environ.get("LAWGO_OC")
    if not oc:
        sys.exit("환경 변수 LAWGO_OC 가 필요합니다.")
    os.makedirs(os.path.join(OUT, "_search"), exist_ok=True)
    report = {"ok": [], "not_found": [], "error": []}
    for i, n in enumerate(names, 1):
        print(f"[{i}/{len(names)}] {n}", flush=True)
        fn = re.sub(r'[\\/:*?"<>|]', "_", n) + ".json"
        try:
            s = get("lawSearch.do", {"OC": oc, "target": "law", "type": "JSON", "query": n})
            if "result" in s and "LawSearch" not in s:   # 인증·IP 오류 응답이면 법령 탓이 아니므로 즉시 중단(전체가 not_found 로 기록되는 것을 막음)
                sys.exit(f"법제처 응답 오류: {s.get('result')} / {s.get('msg')}\n(이 PC 의 현재 공인 IP 가 신청서에 등록된 IP 와 같은지 확인하세요: curl https://api.ipify.org)")
            save(s, os.path.join(OUT, "_search", fn), oc)
            hit = pick(s, n)
            root = s.get("LawSearch", s)
            total, page = int(root.get("totalCnt") or 0), 1
            while not hit and page * 20 < total and page < 10:   # '상법'처럼 부분 일치가 많으면 첫 쪽(20건)에 없을 수 있음
                page += 1
                hit = pick(get("lawSearch.do", {"OC": oc, "target": "law", "type": "JSON", "query": n, "page": page}), n)
            if not hit:
                report["not_found"].append(n)  # 법령이 아닌 행정규칙·조약 등일 수 있음 → 수동 확인
                continue
            body = get("lawService.do", {"OC": oc, "target": "law", "type": "JSON", "MST": hit.get("법령일련번호")})
            save(body, os.path.join(OUT, fn), oc)
            report["ok"].append({"name": n, "mst": hit.get("법령일련번호"), "시행일자": hit.get("시행일자")})
        except Exception as e:
            report["error"].append({"name": n, "error": str(e)})
        time.sleep(0.3)
    save(report, os.path.join(OUT, "report_only.json" if a.only else "report.json"), oc)   # --only 시험 실행이 전체 결과(report.json)를 덮어쓰지 않게 분리
    print({k: len(v) for k, v in report.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
