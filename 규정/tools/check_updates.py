#!/usr/bin/env python3
"""규정 DB 의 규정마다 법제처(open.law.go.kr) 현행 시행일을 조회해, DB 보다 새 개정이 있으면 알림용 파일을 만든다.

  LAWGO_OC=<OC값> python3 규정/tools/check_updates.py            # 점검 후 규정/data/update-status.json 갱신
  LAWGO_OC=<OC값> python3 규정/tools/check_updates.py --no-write # 결과만 출력(파일 안 씀)

대상: config/registry.json(PDF) + config/registry-api.json(API) 규정 전부 + 조달청 법령해석 새 안건(법령해석/조달청/index.json 과 비교). 조회 방식은 fetch_lawgokr.py --db 와 같다(find_exact).
출력: 규정/data/update-status.json — 규정 검색 화면이 읽어서 '개정 알림'으로 보여 준다(규정 본문·data/*.json 은 건드리지 않음).
- 법제처 API 는 신청서에 등록한 IP 에서만 응답하므로 GitHub Actions·서버가 아니라 등록된 PC 에서 실행한다.
- 인증 오류(IP 불일치)가 나면 파일을 쓰지 않고 멈춘다(이전 결과가 빈 값으로 덮이지 않게).
- OC 값은 코드·저장소에 넣지 않는다(환경 변수 LAWGO_OC).
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_lawgokr as F  # noqa: E402

ROOT = F.ROOT
OUT = os.path.join(ROOT, "규정", "data", "update-status.json")
CFG = os.path.join(ROOT, "규정", "config", "update-check.json")      # {"api_query": {규정ID: 검색어}} — 이름 전체로 검색하면 0건인 긴 이름 규정용

PREFIX = re.compile(r"^\((?:계약예규|고시|훈령|예규|지침)\)\s*")
NOTE = re.compile(r"\s*\(현행[^)]*\)\s*$")      # '(현행 2026-07-01 시행본)' 처럼 DB 쪽에서 붙인 설명은 법제처 이름이 아니라서 뗀다


def dash(d8):
    d = re.sub(r"\D", "", d8 or "")
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else ""


def targets():
    out = []
    for fn, api in (("registry.json", False), ("registry-api.json", True)):
        for r in json.load(open(os.path.join(ROOT, "규정", "config", fn), encoding="utf-8"))["regulations"]:
            if r["type"] in F.SKIP_TYPES:
                continue
            if api and r.get("api_kind") == "admrul":
                target = "admrul"
            else:
                target = "law" if r["type"] in F.LAW_TYPES else "admrul"
            out.append((r, target))
    return out


def api_query(rid):
    try:
        return json.load(open(CFG, encoding="utf-8")).get("api_query", {}).get(rid)
    except (OSError, ValueError):
        return None


def _check_one(oc, r, target, today):
    """규정 하나를 법제처와 비교한다(여러 개를 동시에 돌릴 수 있게 독립된 함수). 반환: (row, update 또는 None)"""
    meta_path = os.path.join(ROOT, "규정", "regulations", r["slug"], "metadata.json")
    meta = json.load(open(meta_path, encoding="utf-8")) if os.path.exists(meta_path) else {}
    names = [x for x in (PREFIX.sub("", meta.get("name") or ""), meta.get("name"), PREFIX.sub("", r["title"]), r["title"]) if x]
    names = [NOTE.sub("", x) for x in names] + names
    row, upd = {"id": r["id"], "title": r["title"], "db_eff": dash((meta.get("effective_date") or "").replace("-", ""))}, None
    if not meta.get("effective_date"):      # 시행일이 없는 것(예: 품질인증기준 '안')은 법령이 아니라 비교 대상이 아님
        row["status"] = "skipped"
        return row, None
    try:
        hit = None
        for nm in dict.fromkeys(names):
            hit, _first = F.find_exact(oc, target, nm, api_query(r["id"]))
            if hit:
                break
        db_eff = (meta.get("effective_date") or "").replace("-", "")
        if not hit:
            row["status"] = "not_found"
        else:
            api_eff = hit.get("시행일자") or ""
            row.update(api_eff=api_eff, api_no=hit.get("공포번호") or hit.get("발령번호") or "")
            have = {(v.get("effective_date") or "").replace("-", "") for v in meta.get("versions", [])}
            hit_mst = str(hit.get("법령일련번호") or hit.get("행정규칙일련번호") or "")
            api_mst = str(r.get("api_mst") or meta.get("api_mst") or "")    # Sites 로 API 본문이 된 규정은 metadata 의 일련번호
            if api_mst and hit_mst and api_mst == hit_mst:
                row["status"] = "same"          # API 로 편입한 규정은 저장해 둔 일련번호(=법령의 한 버전)가 같으면 내용이 같다. 목록의 시행일자는 단계별 시행일이라 DB 시행일과 달라 보일 수 있음
            elif api_eff == db_eff or api_eff in have:
                row["status"] = "same"
            elif api_eff > db_eff:
                row["status"] = "api_newer"
                db_no = re.sub(r"\D", "", meta.get("number") or "")
                upd = {"id": r["id"], "name": meta.get("name") or r["title"], "short": r.get("short") or r.get("display") or "",
                       "db_effective": dash(db_eff), "api_effective": dash(api_eff), "api_number": row["api_no"],
                       "upcoming": api_eff > today.replace("-", ""),
                       "phased": bool(db_no) and db_no == re.sub(r"\D", "", str(row["api_no"]))}   # 공포번호가 같으면 같은 개정 법률의 다음(단계) 시행일
            else:
                row["status"] = "db_newer"
    except SystemExit:
        raise
    except Exception as e:
        row.update(status="error", error=str(e)[:80])
    return row, upd


def check_regs(oc, tg, today, progress=None, total=None, workers=5):
    """규정마다 법제처 현행 시행일을 비교한다. 법제처 응답을 기다리는 시간이 대부분이라 workers 개를 동시에 조회한다.
    progress(n, total, 이름, 상태) 는 규정 하나를 마칠 때마다(끝난 순서대로, 메인 스레드에서) 부른다. 반환: (rows, updates) — 규정 목록 순서대로."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    total = total or len(tg)
    res = [None] * len(tg)
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        futs = {pool.submit(_check_one, oc, r, t, today): i for i, (r, t) in enumerate(tg)}
        for n, f in enumerate(as_completed(futs), 1):
            res[futs[f]] = f.result()          # 법제처 인증 오류(SystemExit)는 여기서 그대로 올라가 점검을 멈춘다
            if progress:
                progress(n, total, res[futs[f]][0]["title"], res[futs[f]][0]["status"])
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return [x[0] for x in res], [x[1] for x in res if x[1]]


PPS_DIR = os.path.join(ROOT, "법령해석", "조달청")


def check_pps(oc):
    """조달청 법령해석(ppsCgmExpc) 목록을 법제처에서 받아, 화면에 이미 있는 해석(법령해석/조달청/index.json)에 없는 새 안건을 센다. 본문은 받지 않는다."""
    have = {x["id"] for x in json.load(open(os.path.join(PPS_DIR, "index.json"), encoding="utf-8"))["items"]}
    items, page, total = [], 1, None
    while True:
        r = F.get("lawSearch.do", {"OC": oc, "target": "ppsCgmExpc", "type": "JSON", "display": 100, "page": page})
        if "result" in r and not any(isinstance(v, dict) for v in r.values()):
            sys.exit(f"법제처 응답 오류: {r.get('result')} / {r.get('msg')}\n(이 PC 의 현재 공인 IP 가 신청서에 등록된 IP 와 같은지 확인하세요)")
        root = next(v for v in r.values() if isinstance(v, dict))
        total = int(root.get("totalCnt") or 0) if total is None else total
        rows = [v for k, v in root.items() if isinstance(v, (list, dict)) and k not in ("keyword",)]
        rows = rows[0] if rows else []
        rows = rows if isinstance(rows, list) else [rows]
        rows = [x for x in rows if isinstance(x, dict)]
        items += rows
        if not rows or len(items) >= total or page >= 50:
            break
        page += 1
        time.sleep(0.3)
    ids = {str(x.get("법령해석일련번호")) for x in items if x.get("법령해석일련번호")}
    new = [x for x in items if str(x.get("법령해석일련번호")) not in have and x.get("법령해석일련번호")]
    new.sort(key=lambda x: x.get("해석일자") or "", reverse=True)
    return {"db_count": len(have), "api_count": len(ids), "new_count": len(new),
            "new": [{"id": str(x.get("법령해석일련번호")), "t": re.sub(r"\s+", " ", x.get("안건명") or "").strip()[:120], "d": dash(x.get("해석일자"))} for x in new[:30]]}


def build_status(rows, updates, today, pps=None):
    cnt = {}
    for x in rows:
        cnt[x["status"]] = cnt.get(x["status"], 0) + 1
    status = {"schema_version": "1.0", "checked_at": today, "source": "법제처 국가법령정보 공동활용 API(시행일자 비교)",
              "total": len(rows), "counts": cnt, "updates": updates}
    others = [{"id": x["id"], "title": x.get("title", ""), "status": x["status"], "db_effective": x.get("db_eff", ""), "api_effective": dash(x.get("api_eff")), "error": x.get("error", "")}
              for x in rows if x["status"] not in ("same", "api_newer")]      # 같지도 새 시행일도 아닌 규정(건너뜀·DB가 더 새로움·못 찾음·오류)을 이름과 함께 남김
    if others:
        status["others"] = others
    if pps is not None:
        status["interpretations"] = {"조달청": pps}
    return status


def write_status(status):
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=1)
        f.write("\n")


def too_many_errors(rows):
    return sum(1 for x in rows if x["status"] == "error") * 2 > len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-write", action="store_true", help="결과만 출력하고 파일은 쓰지 않음")
    ap.add_argument("--only", help="이 이름·ID 가 들어간 규정만(쉼표 구분, 시험용 — 이때는 파일을 쓰지 않음)")
    ap.add_argument("--no-pps", action="store_true", help="조달청 법령해석 새 안건 점검은 건너뜀")
    ap.add_argument("--workers", type=int, default=5, help="동시에 조회할 개수(기본 5)")
    a = ap.parse_args()
    oc = os.environ.get("LAWGO_OC", "").strip()
    if not oc:
        sys.exit("환경 변수 LAWGO_OC 가 필요합니다.")
    today = datetime.date.today().isoformat()
    onlys = [x for x in (a.only or "").split(",") if x]
    tg = [(r, t) for r, t in targets() if not onlys or any(o == r["id"] or o in r["title"] for o in onlys)]
    do_pps = not a.no_pps and not onlys
    total = len(tg) + (1 if do_pps else 0)
    rows, updates = check_regs(oc, tg, today, lambda n, t, name, st: print(f"[{n}/{t}] {st:10} {name}", flush=True), total, a.workers)
    pps = None
    if do_pps:
        pps = check_pps(oc)
        print(f"[{total}/{total}] 조달청 법령해석: 화면 {pps['db_count']}건 · 법제처 {pps['api_count']}건 · 새 안건 {pps['new_count']}건", flush=True)
    status = build_status(rows, updates, today, pps)
    print(status["counts"])
    for x in rows:
        if x["status"] in ("not_found", "db_newer", "error"):
            print(f"  {x['status']:10} {x['title']} | DB {x.get('db_eff')} | 법제처 {dash(x.get('api_eff'))} {x.get('error', '')}")
    if too_many_errors(rows):
        sys.exit("오류가 절반을 넘어 파일을 쓰지 않아요(네트워크·IP 확인 후 다시 실행).")
    if a.no_write or onlys:
        print(json.dumps(status, ensure_ascii=False, indent=1))
        return 0
    write_status(status)
    print(f"→ {os.path.relpath(OUT, ROOT)} (개정 알림 {len(updates)}건" + (f" · 조달청 새 해석 {pps['new_count']}건" if pps else "") + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
