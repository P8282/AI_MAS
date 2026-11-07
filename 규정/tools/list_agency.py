#!/usr/bin/env python3
"""한 기관(기본: 방위사업청)이 소관하는 법령·행정규칙 목록을 법제처 API 로 받아 정리한다. 본문은 받지 않고, 규정 DB 에 이미 있는지만 표시한다.
(어떤 규정을 DB 에 넣을지 고르기 전의 '목록 확인' 단계. 이 도구는 DB·registry 를 바꾸지 않는다.)

  LAWGO_OC=<OC값> python3 규정/tools/list_agency.py                  # 방위사업청 목록 → 규정/tools/_lawgokr/_agency/방위사업청.json
  LAWGO_OC=... python3 규정/tools/list_agency.py --agency 조달청 --org 1230000   # 다른 기관 / 소관부처 코드를 직접 줄 때

법제처 API 는 등록한 IP(이 PC)에서만 응답한다. OC 값은 환경 변수 LAWGO_OC 로만 전달한다(저장 파일에서는 가려짐).
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_lawgokr as F  # noqa: E402

CFG = os.path.join(F.ROOT, "규정", "config")
OUTDIR = os.path.join(F.OUT, "_agency")
KINDS = (("law", "LawSearch", "법령명한글", "법령일련번호"), ("admrul", "AdmRulSearch", "행정규칙명", "행정규칙일련번호"))


def items_of(resp, root_key, key):
    root = resp.get(root_key, resp)
    items = root.get(key) or []
    return ([items] if isinstance(items, dict) else items), int(root.get("totalCnt") or 0)


def search_all(oc, target, root_key, key, params):
    """검색 결과를 모든 쪽에서 모은다(쪽당 100건)."""
    rows, page = [], 1
    while True:
        resp = F.get("lawSearch.do", {"OC": oc, "target": target, "type": "JSON", "display": 100, "page": page, **params})
        if "result" in resp and root_key not in resp:
            sys.exit(f"법제처 응답 오류: {resp.get('result')} / {resp.get('msg')}\n(이 PC 의 공인 IP 가 신청서에 등록된 IP 와 같은지 확인하세요: curl https://api.ipify.org)")
        got, total = items_of(resp, root_key, key)
        rows += got
        if len(got) < 100 or (total and page * 100 >= total) or page >= 30:      # totalCnt 가 없는 응답(행정규칙)도 쪽이 꽉 차 있으면 다음 쪽을 본다
            return rows, total or len(rows)
        page += 1
        time.sleep(0.2)


SWEEP = ["방위사업", "방산", "방위산업", "국방", "방위", "군수", "무기", "무기체계", "획득", "사업관리", "연구개발", "시험평가", "원가", "계약", "입찰", "감항", "감독관",
         "품질", "조달", "수출", "성과", "직제", "인증", "표준", "부품", "국산화", "소요", "전력", "정비", "해외", "협력", "보안", "업무처리", "지침", "운영규정", "관리규정", "심의", "위원회"]


def pick_org(oc, agency, codes, diag):
    """응답의 소관부처코드가 '1290000,1451000,1690000'처럼 여러 개일 수 있다(공동 소관). 하나씩 시험해서, 결과가 실제로 이 기관 것으로 걸러지는 코드를 고른다."""
    for c in [x.strip() for x in re.split(r"[,\s]+", codes) if x.strip()]:
        resp = F.get("lawSearch.do", {"OC": oc, "target": "law", "type": "JSON", "display": 100, "org": c})
        rows, total = items_of(resp, "LawSearch", "law")
        ok = sum(1 for x in rows if agency in (x.get("소관부처명") or ""))
        diag.append(f"소관부처 코드 {c}: 법제처 {total}건 중 첫 쪽 {len(rows)}건에서 이 기관 {ok}건")
        print("  " + diag[-1], flush=True)
        if rows and ok * 2 >= len(rows):
            return c
    return ""


def sweep_admrul(oc, agency, diag):
    """행정규칙은 소관부처 조건(org)이 잘 안 먹어서, 낱말 여러 개로 이름 검색한 결과를 합친 뒤 소관부처명이 이 기관인 것만 남긴다."""
    found = {}
    for w in SWEEP:
        rows, total = search_all(oc, "admrul", "AdmRulSearch", "admrul", {"query": w})
        mine = [x for x in rows if agency in (x.get("소관부처명") or "")]
        for x in mine:
            found[str(x.get("행정규칙일련번호") or x.get("행정규칙명"))] = x
        diag.append(f"낱말 '{w}': 받은 {len(rows)}건 → 이 기관 {len(mine)}건 (누적 {len(found)}건)")
        print("  " + diag[-1], flush=True)
        time.sleep(0.2)
    return list(found.values())


def find_org_code(oc, agency):
    """기관 코드: 그 기관 소관으로 알려진 법령을 검색해 응답의 소관부처코드를 읽는다(방위사업청 → 방위사업법)."""
    for seed in ("방위사업법", agency):
        rows, _ = search_all(oc, "law", "LawSearch", "law", {"query": seed})
        for x in rows:
            if agency in (x.get("소관부처명") or "") and x.get("소관부처코드"):
                return str(x["소관부처코드"])
    return ""


def db_names():
    """규정 DB(registry·registry-api)에 있는 규정 이름들(정규화)."""
    names = set()
    for fn in ("registry.json", "registry-api.json"):
        p = os.path.join(CFG, fn)
        if not os.path.exists(p):
            continue
        for r in json.load(open(p, encoding="utf-8"))["regulations"]:
            for n in [r.get("title"), r.get("short"), r.get("display")] + list(r.get("aliases") or []):
                if n:
                    names.add(F.norm(re.sub(r"^\((?:계약예규|고시|훈령|예규|지침)\)\s*", "", n)))
    return names


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agency", default="방위사업청")
    ap.add_argument("--org", default="", help="소관부처 코드(모르면 자동으로 찾음)")
    a = ap.parse_args()
    oc = os.environ.get("LAWGO_OC", "").strip()
    if not oc:
        sys.exit("환경 변수 LAWGO_OC 가 필요합니다.")
    have, out, diag, seen = db_names(), [], [], set()
    codes = a.org or find_org_code(oc, a.agency)
    if not codes:
        sys.exit(f"{a.agency} 의 소관부처 코드를 찾지 못했어요. --org 로 직접 주세요.")
    print(f"{a.agency} 소관부처 코드 후보: {codes}", flush=True)
    org = pick_org(oc, a.agency, codes, diag)
    if not org:
        sys.exit("어느 코드로도 이 기관 규정만 걸러지지 않았어요. 위 '소관부처 코드' 줄들을 알려 주세요.")
    print(f"사용할 코드: {org}", flush=True)
    for target, root_key, name_key, id_key in KINDS:
        rows, total = search_all(oc, target, root_key, target, {"org": org})
        mine = [x for x in rows if a.agency in (x.get("소관부처명") or "")]      # org 로 걸렀는데도 다른 기관(법원 예규 등)이 섞이면 소관부처명으로 한 번 더 거름. 소관부처명이 비어 있으면 제외
        other = {}
        for x in rows:
            if x not in mine:
                other[x.get("소관부처명") or "(비어 있음)"] = other.get(x.get("소관부처명") or "(비어 있음)", 0) + 1
        print(f"  {'법령' if target == 'law' else '행정규칙'}: 받은 {len(rows)}건(법제처 표시 {total}건) → 이 기관 {len(mine)}건"
              + (f" · 제외 {dict(sorted(other.items(), key=lambda kv: -kv[1])[:6])}" if other else ""), flush=True)
        diag.append(f"{target}: 받은 {len(rows)}건 · 표시 {total}건 · 이 기관 {len(mine)}건 · 제외 {other}")
        if rows:
            diag.append(f"{target} 응답 항목 이름들: {sorted(rows[0].keys())}")
        if target == "admrul":
            extra = sweep_admrul(oc, a.agency, diag)
            have_ids = {str(x.get(id_key) or x.get(name_key)) for x in mine}
            mine += [x for x in extra if str(x.get(id_key) or x.get(name_key)) not in have_ids]
            print(f"  행정규칙 합계(org + 낱말 검색): {len(mine)}건", flush=True)
        for x in mine:
            nm = x.get(name_key) or ""
            out.append({"target": target, "name": nm, "mst": str(x.get(id_key) or ""),
                        "kind": x.get("법령구분명") or x.get("행정규칙종류") or "", "announced": x.get("공포일자") or x.get("발령일자") or "",
                        "effective": x.get("시행일자") or "", "status": x.get("현행연혁코드") or "", "agency": x.get("소관부처명") or "",
                        "in_db": F.norm(re.sub(r"^\((?:계약예규|고시|훈령|예규|지침)\)\s*", "", nm)) in have})
    os.makedirs(OUTDIR, exist_ok=True)
    path = os.path.join(OUTDIR, a.agency + ".json")
    F.save({"agency": a.agency, "org": org, "count": len(out), "items": out}, path, oc)
    new = [r for r in out if not r["in_db"]]
    print(f"\n합계 {len(out)}건 · 이미 DB 에 있음 {len(out) - len(new)}건 · 새로 넣을 수 있는 것 {len(new)}건\n저장: {path}\n")
    print("== 진단(문제가 있으면 이 부분을 붙여 주세요) ==")
    for d in diag:
        print("  " + d)
    print()
    for r in out:
        print(f"{'·' if r['in_db'] else '＋'} [{'법령' if r['target'] == 'law' else '행정규칙'}] {r['kind']:<6} {r['name']}  (시행 {r['effective']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
