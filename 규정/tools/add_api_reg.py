#!/usr/bin/env python3
"""법제처 API 로 받은 규정 '하나'를 규정 DB 맨 뒤에 붙인다(다른 규정 파일은 그대로).

  python3 규정/tools/add_api_reg.py <규정ID> --dry-run   # 만들 내용(조문·별표 수, 시행일 등)만 출력, 파일 안 씀
  python3 규정/tools/add_api_reg.py <규정ID>             # 붙이기

왜 따로 있나: merge_api_regs.py 는 registry-api.json 의 API 규정 전부를 원문/법제처API/ 원본으로 다시 만든다.
Sites 반영(import_sites_export.py) 이후에는 Sites 가 관리하는 규정(config/sites-managed.json)까지 원본으로 되돌리고,
원본 파일(api_file)이 없는 Sites 규정에서 멈추므로, 2026-10-16 부터 새 규정 1개 추가에는 이 도구를 쓴다.

입력: registry-api.json 의 해당 항목(api_file 필수, 행정규칙은 api_kind "admrul"·api_mst 도 필수) + 원문/법제처API/<api_file>
하는 일: merge_api_regs.py 와 같은 변환(조문·별표·인용·키워드) → regulations/<slug>/·markdown/<slug>.md 생성,
        data/regulations.json·regulation-index.json·keywords.json·relations.json·effective-dates.json 맨 뒤에 이 규정 행만 추가.
기존 규정의 순번·ID·조문 파일·인용 파생값은 바꾸지 않는다. 이미 DB 에 있는 ID 면 멈춘다(다시 붙이려면 git 으로 되돌린 뒤 실행).
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import merge_api_regs as M  # noqa: E402


def admrul_annexes(reg, doc):
    """행정규칙 응답의 별표(AdmRulService.별표.별표단위) → 별표 레코드. merge_api_regs.api_to_records 의 법령 별표 변환과 같은 모양.
    (merge_api_regs.admrul_to_records 는 예전 응답에 별표 본문이 없어 별표를 만들지 않음 — 그 도구의 결과는 바꾸지 않으려고 여기서만 처리)"""
    out = []
    for b in M.listify((doc["AdmRulService"].get("별표") or {}).get("별표단위")):
        raw_no = M.re.sub(r"\D", "", str(b.get("별표번호") or "")) or "0"
        sub = M.re.sub(r"\D", "", str(b.get("별표키") or ""))[4:6]
        no = str(int(raw_no))
        sub = str(int(sub)) if sub and int(sub) else ""
        number = ("" if no == "0" else no) + (f"의{sub}" if sub else "")
        lines = []
        for blk in M.listify(b.get("별표내용")):
            lines += [str(x).rstrip() for x in (blk if isinstance(blk, list) else [blk])]
        content = M.html.unescape("\n".join(lines).strip("\n"))
        title = M.clean(b.get("별표제목") or "")
        form = str(b.get("별표키") or "").endswith("F") or str(b.get("별표구분") or "") == "서식"
        kind, abbr = ("별지", "FORM") if form else ("별표", "ANNEX")
        deleted = bool(M.re.match(r"^삭제\b", title))
        aid = f"{reg['id']}_{abbr}" + (f"_{no}" if no != "0" else "") + (f"_{sub}" if sub else "")
        out.append({
            "id": aid, "display_id": f"{reg['display']}-{kind}{number}", "regulation_id": reg["id"], "regulation_name": reg["title"], "short_name": reg["short"],
            "document_type": reg["type"], "annex_kind": kind, "annex_number": number, "annex_title": title, "title_source": "api",
            "is_deleted": deleted, "pages": 0, "content": "" if deleted else content, "content_blocks": [] if deleted else [{"type": "text", "page": 1, "text": content}], "content_format": "blocks",
            "related_articles": [], "related_regulations": [],
            "effective_date": reg["effective_date"], "revision_date": reg["revision_date"], "source_file": reg["source_file"], "source_path": reg["source_path"],
            "pdf_path": "", "source_page": "", "keywords": [],
        })
    ids = [x["id"] for x in out]
    if len(ids) != len(set(ids)):
        sys.exit(f"별표 ID 가 겹쳐요: {sorted(i for i in ids if ids.count(i) > 1)}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reg_id")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    reg_cfg = M.jload(os.path.join(M.OUT, "config", "registry.json"))["regulations"]
    api_cfg = M.jload(os.path.join(M.OUT, "config", "registry-api.json"))["regulations"]
    kw_cfg = M.jload(os.path.join(M.OUT, "config", "keyword-dictionary.json"))["terms"]
    entry = next((e for e in api_cfg if e["id"] == a.reg_id), None)
    if not entry:
        sys.exit(f"registry-api.json 에 {a.reg_id} 항목이 없어요.")
    for k in ("api_file", "slug", "title") + (("api_mst",) if entry.get("api_kind") == "admrul" else ()):   # 법령은 api_mst 가 없으면 법제처 링크를 법령 이름 주소로 만듦
        if not entry.get(k):
            sys.exit(f"{a.reg_id}: registry-api.json 항목에 {k} 가 필요해요.")
    regs_json = M.jload(os.path.join(M.OUT, "data", "regulations.json"))
    base_regs = regs_json["regulations"]
    if any(r["id"] == a.reg_id for r in base_regs):
        sys.exit(f"{a.reg_id} 는 이미 규정 DB 에 있어요(다시 붙이지 않음).")
    if os.path.exists(os.path.join(M.OUT, "regulations", entry["slug"])):
        sys.exit(f"규정/regulations/{entry['slug']} 폴더가 이미 있어요.")
    raw = os.path.join(M.SRC_DIR, entry["api_file"])
    if "OC=" in open(raw, encoding="utf-8").read().replace("OC=OC_REDACTED", ""):
        sys.exit("원본에 가려지지 않은 인증키(OC=…)가 보여요. 사용하지 마세요.")

    doc = M.jload(raw)
    if entry.get("api_kind") == "admrul":
        reg = M.admrul_reg_meta(entry, doc)
        recs, ann = M.admrul_to_records(reg, doc)
        ann = admrul_annexes(reg, doc)
    else:
        reg = M.api_reg_meta(entry, doc)
        recs, ann = M.api_to_records(reg, doc)

    # 인용: 기존 규정 전체 + 새 규정으로 계산하고, 기존 규정의 값은 되돌림(merge_api_regs.py 기본 동작과 같음)
    cfg_by_id = {c["id"]: c for c in reg_cfg + api_cfg}
    base_cfg = [cfg_by_id[r["id"]] for r in base_regs]
    base_records = {r["id"]: M.jload(os.path.join(M.OUT, "regulations", r["slug"], "articles.json")) for r in base_regs}
    resolver = M.make_resolver(base_cfg + [reg])
    index_all = {**base_records, reg["id"]: recs}
    saved = {rid: [(r["related_articles"], r["related_regulations"], r["external_references"]) for r in rs] for rid, rs in base_records.items()}
    M.derive_citations(resolver, {c["id"]: c for c in base_cfg + [reg]}, index_all)
    for rid, rs in base_records.items():
        for r, s_ in zip(rs, saved[rid]):
            r["related_articles"], r["related_regulations"], r["external_references"] = s_
    edges = {(r["id"], t): 1 for rs in index_all.values() for r in rs for t in r["related_articles"]}

    # 키워드: 새 규정의 조문·별표만(df 는 전체 조문 기준)
    kw = M.Keywords(kw_cfg)
    stats = {r["id"]: kw.count(r["article_title"], M.art_plain(r)) for rs in index_all.values() for r in rs}
    n_total = max(1, len(stats))
    for r in recs:
        r["keywords"] = kw.pick(stats[r["id"]], n_total)
    idx = {r["article_number"]: r["id"] for r in recs if not r["is_supplementary"]}
    for x in ann:
        x["keywords"] = kw.pick(kw.count(x["annex_title"], x["annex_title"] + " " + x["content"]), n_total)
        m = M.re.search(r"\((제\s*\d+\s*조[^)]*?)\s*관련\)", x["annex_title"])
        if m:
            for mm in M.re.finditer(r"제\s*(\d+)\s*조(?:의\s*(\d+))?", m.group(1)):
                t = idx.get(mm.group(1) + (f"의{mm.group(2)}" if mm.group(2) else ""))
                if t and t not in x["related_articles"]:
                    x["related_articles"].append(t)

    n_art = sum(1 for r in recs if not r["is_supplementary"])
    print(f"{reg['id']} · {reg['name']} · 시행 {reg['effective_date']} · {reg['number']} · 조문 {n_art} · 부칙 {len(recs) - n_art} · 별표 {len(ann)}"
          f" · 이 규정 조문의 인용 링크 {sum(len(r['related_articles']) for r in recs)}개")
    if a.dry_run:
        return 0

    M.write_all(regs_json, base_regs, [reg], {reg["id"]: recs}, {reg["id"]: ann}, edges, kw_cfg)
    ep = os.path.join(M.OUT, "data", "effective-dates.json")      # 홈 배너 '시행 안내'용 경량 목록(import_sites_export.py 와 같은 형식)
    ej = M.jload(ep)
    ej["items"].append([reg["short"], reg["effective_date"], reg.get("group", "")])
    ej["generated"] = M.TODAY
    M.jdump(ep, ej)
    print(f"붙임: 규정/regulations/{reg['slug']}/ · data/*.json 맨 뒤 1개 규정")
    return 0


if __name__ == "__main__":
    sys.exit(main())
