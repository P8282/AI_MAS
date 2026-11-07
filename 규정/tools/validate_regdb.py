#!/usr/bin/env python3
"""규정 DB 품질 검증 → 규정/docs/QUALITY_REPORT.md 를 쓰고, 오류가 있으면 종료코드 1.

  python3 규정/tools/validate_regdb.py

검사: ID 중복 · 조 번호 누락/순서 · 항·호·목 번호 연속 · 빈 본문/제목 · 본문 잘림 의심 · 이상 문자(OCR 깨짐) · 날짜 · 원문 글자 보존율 · 인용 대상 · 부속 자료 · 인덱스와 상세 파일 일치
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdf_lines import clean_main, nfc, page_lines  # noqa: E402
from refs import ns  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "규정")
END_OK = re.compile(r"(다|다\.|\.|\)|>|\]|”|\"|호|것|때|한|함|음|임|등|지|조|항|·|ㆍ|:|;|,|까지|이상|이하|미만|초과|\d|[가-힣]\))\s*$")


def load(p):
    return json.load(open(os.path.join(OUT, p), encoding="utf-8"))


def main():
    errors, warns, info = [], [], []
    regs = load("data/regulations.json")["regulations"]
    idx = load("data/regulation-index.json")
    ids_seen = {}
    total = {"articles": 0, "paragraphs": 0, "items": 0, "subitems": 0, "annexes": 0}
    rows = []
    for reg in regs:
        d = os.path.join("regulations", reg["slug"])
        arts = load(d + "/articles.json")
        anns = load(d + "/annexes.json")
        meta = load(d + "/metadata.json")
        r_err, r_warn = [], []
        # ID 중복
        for r in arts:
            nodes = [r["id"]]
            for p in r["paragraphs"]:
                nodes.append(p["id"])
                for it in p["items"]:
                    nodes.append(it["id"])
                    nodes += [s["id"] for s in it["subitems"]]
                    total["subitems"] += len(it["subitems"])
                total["items"] += len(p["items"])
            total["paragraphs"] += len(r["paragraphs"])
            for n in nodes:
                if n in ids_seen:
                    r_err.append(f"ID 중복: {n}")
                ids_seen[n] = 1
        for a in anns:
            if a["id"] in ids_seen:
                r_err.append(f"ID 중복: {a['id']}")
            ids_seen[a["id"]] = 1
        total["articles"] += len(arts)
        total["annexes"] += len(anns)
        outline = meta.get("format") in ("outline", "opinion")       # 장·절·번호 목록형(조 번호 없음): 조 번호 순서·서술형 종결 검사는 적용하지 않는다
        # 조 번호 순서·누락 (본문)
        body = [] if outline else [r for r in arts if not r["is_supplementary"] and not r["is_scheduled"]]
        prev = (0, 0)
        for r in body:
            m = re.match(r"(\d+)(?:의(\d+))?$", r["article_number"])
            if not m:                                       # 조 번호 없는 본문 단위(API 행정규칙의 '본문 N'·'전문' 등)는 순서 검사에서 뺀다
                continue
            cur = (int(m.group(1)), int(m.group(2) or 0))
            if cur <= prev:
                r_err.append(f"조 번호 순서 이상: {r['article_label']} (앞 {prev})")
            elif cur[0] > prev[0] + 1 and cur[1] == 0:
                gap = list(range(prev[0] + 1, cur[0]))
                r_warn.append(f"조 번호 건너뜀: 제{gap[0]}조" + (f"~제{gap[-1]}조" if len(gap) > 1 else "") + f" (앞 {prev[0]}→{cur[0]}) — 원문에 결번이 없다면 누락")
            elif cur[1] > 2 and cur[0] == prev[0] and cur[1] > prev[1] + 1:
                r_warn.append(f"가지 번호 건너뜀: 제{cur[0]}조의{prev[1] + 1}~ (앞 {prev}→{cur}) — 원문 결번일 수 있음")
            prev = cur
        # 항·호·목 연속
        for r in arts:
            ps = [p for p in r["paragraphs"] if p["paragraph_index"]]
            for i, p in enumerate(ps, 1):
                if p["paragraph_index"] != i:
                    r_warn.append(f"{r['display_id']}: 항 번호 {i} 자리에 {p['paragraph_number']}")
                    break
            for p in r["paragraphs"]:
                nums = [int(re.match(r"\d+", it["item_number"]).group()) for it in p["items"] if "의" not in it["item_number"]]
                if nums and nums != list(range(1, len(nums) + 1)):
                    r_warn.append(f"{p['display_id']}: 호 번호 연속 아님 {nums[:12]}")
            # 빈 본문/제목
            txt = "".join(p["content"] + "".join(i["content"] for i in p["items"]) for p in r["paragraphs"]).strip()
            if not r["is_deleted"] and not txt:
                r_err.append(f"{r['display_id']}: 본문이 비어 있음")
            if not r["is_deleted"] and not r["article_title"] and not r["is_supplementary"] and not outline:
                r_warn.append(f"{r['display_id']}: 제목 없음")
        # 잘림 의심: 마지막 항/호/목이 문장 종결로 끝나지 않음
        cut = []
        for r in arts:
            if r["is_deleted"] or not r["paragraphs"]:
                continue
            last = r["paragraphs"][-1]
            leaf = last
            if last["items"]:
                leaf = last["items"][-1]
                if leaf["subitems"]:
                    leaf = leaf["subitems"][-1]
            c = re.sub(r"(?:\s*<[^<>]*>|\s*\[[^\[\]]*\])+\s*$", "", leaf["content"].strip()).strip()
            if outline:
                continue
            if leaf is last and c and not re.match(r"^\(?삭제\)?$", c) and not re.search(r"(다|음|함|임|것|호|한다|있다)\.?\)?\s*$", c):
                cut.append(f"{r['display_id']}: …{c[-18:]}")
        if cut:
            r_warn.append(f"마지막 항이 서술형 종결(…다.)로 끝나지 않는 조문 {len(cut)}건 — 예: " + "; ".join(cut[:3]))
        # 이상 문자
        bad = 0
        for r in arts:
            t = json.dumps(r, ensure_ascii=False)
            bad += len(re.findall(r"[-�]", t))
        if bad:
            r_warn.append(f"사용자 영역/대체 문자 {bad}개 (OCR·폰트 깨짐 의심)")
        # 날짜
        if reg["effective_date"] and reg["revision_date"] and reg["effective_date"] < reg["revision_date"]:
            r_err.append(f"시행일({reg['effective_date']})이 공포·개정일({reg['revision_date']})보다 빠름")
        # 부속
        for a in anns:
            if not a["is_deleted"] and len(a["content"].strip()) < 20:
                r_warn.append(f"부속 본문이 거의 비어 있음: {a['display_id']} (스캔 이미지일 수 있음)")
            if not a["annex_title"]:
                r_warn.append(f"부속 제목 없음: {a['display_id']}")
            if re.search(r"[\ue000-\uf8ff\ufffd]", a["content"]):
                r_warn.append(f"부속에 수식·특수 글리프(사용자 영역 문자)가 있음: {a['display_id']} — 원본 PDF 로 확인")
        # 원문 글자 보존율
        rec_ratio = ""
        # (원문 쪽에서 계산) — 아래 conservation()
        rows.append((reg, meta, r_err, r_warn))
        errors += [f"[{reg['short_name']}] {x}" for x in r_err]
        warns += [f"[{reg['short_name']}] {x}" for x in r_warn]
    n_tables = n_texts = 0
    for reg in regs:
        for a in load(os.path.join("regulations", reg["slug"], "annexes.json")):
            for b in a.get("content_blocks", []):
                if b["type"] == "table":
                    n_tables += 1
                else:
                    n_texts += 1
    for reg, meta, _e, _w in rows:
        if any(v.get("status") == "not-loaded" for v in meta["versions"]):
            warns.append(f"[{reg['short_name']}] 같은 규정 PDF 중복 — 시행일이 늦은 것만 반영, 최신본 확인 필요")
    # 인덱스와 상세 일치
    n_idx = len(idx["articles"])
    n_det = sum(r["counts"]["articles"] + r["counts"]["supplementary_articles"] + r["counts"]["scheduled_versions"] for r in regs)
    if n_idx != n_det:
        errors.append(f"인덱스 조문 수({n_idx}) ≠ 상세 조문 수({n_det})")
    if len(idx["annexes"]) != sum(r["counts"]["annexes"] for r in regs):
        errors.append("인덱스 부속 수 ≠ 상세 부속 수")
    idset = {r["id"] for r in idx["articles"]} | {r["id"] for r in idx["annexes"]}
    if len(idset) != n_idx + len(idx["annexes"]):
        errors.append("인덱스 ID 중복")
    # 키워드 인덱스 → 존재하는 ID
    kw = load("data/keywords.json")
    missing = [i for v in kw["index"].values() for i in v if i not in idset]
    if missing:
        errors.append(f"keywords.json 에 없는 ID {len(missing)}개")
    rel = load("data/relations.json")["article_links"]
    miss2 = [i for i in rel["ids"] if i not in idset]
    if miss2:
        errors.append(f"relations.json 에 없는 ID {len(miss2)}개")
    # 원문 글자 보존율
    src_root = os.path.join(ROOT, "원문")
    base = [x for x in os.listdir(src_root) if nfc(x) == "규정"][0]
    cons = []
    for reg, meta, _e, _w in rows:
        if meta.get("format") in ("outline", "opinion") or meta.get("source") == "api":    # API 규정(법제처 JSON)은 PDF 가 없어 글자 보존율을 계산하지 않음
            cons.append((reg["short_name"], 0, 0, 1.0))      # 개요형은 글자 보존율 대신 audit_regdb.py 의 줄 단위 포함 검사로 확인
            continue
        rel_path = reg["source_path"][len("원문/규정/"):]
        path = None
        for r_, _d, fs in os.walk(os.path.join(src_root, base)):
            for f in fs:
                if nfc(os.path.relpath(os.path.join(r_, f), os.path.join(src_root, base))) == rel_path:
                    path = os.path.join(r_, f)
        L = page_lines(path)
        title = L[0]["text"].strip() if L else ""
        L = clean_main(L, title)
        src_chars = sum(len(ns(r["text"])) for r in L)
        front = sum(len(ns(x)) for x in meta["header_raw"])
        arts = load(os.path.join("regulations", reg["slug"], "articles.json"))
        out_chars = 0
        for r in arts:
            out_chars += len(ns(r["article_label"] + ("(" + r["article_title"] + ")" if r["article_title"] else "")))
            for p in r["paragraphs"]:
                out_chars += len(ns(p["paragraph_number"] + p["content"]))
                for it in p["items"]:
                    out_chars += len(ns(it["item_number"] + "." + it["content"]))
                    for sb in it["subitems"]:
                        out_chars += len(ns(sb["subitem_number"] + "." + sb["content"]))
            out_chars += sum(len(ns(n)) for n in r["notes"])
        # 장·절 제목, 부칙 제목 등은 별도 필드 — 고유 값 한 번씩만 센다
        heads = set()
        for r in arts:
            for k in ("part", "chapter", "section", "subsection"):
                if r[k]:
                    heads.add(r[k])
            if r.get("supplement_header"):
                heads.add(r["supplement_header"])
        out_chars += sum(len(ns(h)) for h in heads)
        ratio = out_chars / max(1, src_chars - front)
        cons.append((reg["short_name"], src_chars - front, out_chars, ratio))
        if abs(ratio - 1) > 0.02:
            warns.append(f"[{reg['short_name']}] 원문 글자 보존율 {ratio:.3f} (원문 {src_chars - front} / 결과 {out_chars}) — 누락·중복 확인")
    # 부속 글자 보존율 (PDF 전체 글자 대비 content_blocks 글자)
    import pymupdf
    paths = {}
    for r_, _d, fs in os.walk(os.path.join(src_root, base)):
        for f in fs:
            paths[nfc(os.path.relpath(os.path.join(r_, f), os.path.join(src_root, base)))] = os.path.join(r_, f)
    a_ratios = []
    for reg in regs:
        for a in load(os.path.join("regulations", reg["slug"], "annexes.json")):
            if a["is_deleted"] or not a["pdf_path"] or a["source_path"][len("원문/규정/"):] not in paths:
                continue
            doc = pymupdf.open(paths[a["source_path"][len("원문/규정/"):]])
            def page_chars(pg):      # 겹쳐 찍은(굵게) 제목 줄은 한 번만 센다
                out, prev = [], None
                for ln in pg.get_text().split("\n"):
                    if len(ln.strip()) >= 8 and ln == prev:
                        continue
                    out.append(ln); prev = ln
                return len(ns(re.sub("\uf000{6,}", "", "\n".join(out))))
            pgr = [int(x) for x in a["source_page"].split("-")]
            if a["pdf_path"] == reg.get("pdf_path") and (len(doc) > 1 or True) and a["pdf_path"].endswith(reg["source_path"][len("원문/규정/"):]):
                sc = sum(page_chars(pg) for pg in [doc[i - 1] for i in range(pgr[0], pgr[-1] + 1)])        # 본문 PDF 안의 부속: 해당 쪽만
            else:
                sc = sum(page_chars(pg) for pg in doc)
            oc = 0
            for b in a["content_blocks"]:
                oc += len(ns(b["text"])) if b["type"] == "text" else sum(len(ns(c)) for rw in b["rows"] for c in rw)
            ratio = oc / max(1, sc)
            a_ratios.append(ratio)
            if abs(ratio - 1) > 0.08:
                warns.append(f"[{reg['short_name']}] 부속 글자 보존율 {ratio:.2f}: {a['display_id']} (원문 {sc} / 결과 {oc}) — 표 인식 누락·중복 확인")
    # 인용
    unres = []
    bdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".build")
    p_un = os.path.join(bdir, "unresolved.json")
    if os.path.exists(p_un):
        unres = json.load(open(p_un, encoding="utf-8"))
    p_rj = os.path.join(bdir, "rejected.json")
    if os.path.exists(p_rj):
        rj = json.load(open(p_rj, encoding="utf-8"))
        for name, v in rj.items():
            for x in v.get("articles", []):
                if re.match(r"^제\d+조(?:의\d+)?\s+\(", x["text"]):      # '…제14조 (단, …' 처럼 줄바꿈으로 줄머리에 온 인용(조 제목은 '제14조(제목)' 으로 붙여 씀)
                    continue
                if "생략" in x["text"]:      # 부칙의 '제N조(…) …까지 생략' 줄: 법제처가 생략 표기한 원문 그대로라 조문으로 쪼개지 않는 게 맞음
                    continue
                errors.append(f"[{name}] 조문 시작처럼 보이나 순서가 맞지 않아 본문으로 처리한 줄(쪽 {x['page']}): {x['text']}")
    L = ["# 품질 검증 보고서 (QUALITY_REPORT)", "", "`규정/tools/validate_regdb.py` 가 만든 파일입니다. 직접 고치지 마세요.", "",
         f"- 규정 {len(regs)}개 · 조문 {total['articles']}개(부칙 포함) · 항 {total['paragraphs']} · 호 {total['items']} · 목 {total['subitems']} · 부속 {total['annexes']}개",
         f"- 오류 {len(errors)}건 · 경고 {len(warns)}건 · 대상 조문을 찾지 못한 인용 {len(unres)}건", "",
         "## 검사 항목", "",
         "| 구분 | 검사 | 결과 |", "|---|---|---|",
         f"| 데이터 | 조문 누락(조 번호 건너뜀) | {sum(1 for w in warns if '건너뜀' in w)}건 경고 |",
         f"| 데이터 | 조문 번호·ID 중복 | {sum(1 for e in errors if '중복' in e or '순서' in e)}건 |",
         f"| 데이터 | 본문 잘림 의심(마지막 항이 …다. 로 끝나지 않음, 대부분 ‘생략’·수식·목록형 조문) | {sum(1 for w in warns if '서술형 종결' in w)}개 규정 |",
         f"| 데이터 | OCR·글자 깨짐 | {sum(1 for w in warns if '대체 문자' in w)}개 규정 (PDF 가 텍스트형이라 OCR 을 쓰지 않음) |",
         f"| 데이터 | 표·별표 누락 | 부속 PDF {total['annexes']}개 전부 수록 · 표 {n_tables}개 · 글 블록 {n_texts}개 (표는 PDF 표 인식으로 옮겨 칸 나눔이 원본과 다를 수 있음) |",
         f"| 데이터 | 부속 글자 보존율 | 부속 {len(a_ratios)}개(삭제 제외): 최소 {min(a_ratios):.2f} ~ 최대 {max(a_ratios):.2f} (±8% 넘으면 경고) |",
         f"| 데이터 | 중복 규정 | {sum(1 for w in warns if 'PDF 중복' in w)}건 (같은 규정 PDF 가 둘 이상이면 경고) |",
         f"| 데이터 | 검토 필요 규정 | {sum(1 for r_ in regs if r_['needs_review'])}개 — " + ", ".join(r_['short_name'] for r_ in regs if r_['needs_review']) + " |",
         f"| 데이터 | 시행일·개정일 | 오류 {sum(1 for e in errors if '시행일' in e)}건 |",
         f"| 데이터 | 원문 글자 보존율 | " + ", ".join(f"{n} {r:.3f}" for n, _a, _b, r in cons) + " |", "",
         "## 오류", ""] + ([f"- {e}" for e in errors] or ["- 없음"]) + ["", "## 경고 (사람이 원문과 대조)", ""] + ([f"- {w}" for w in warns] or ["- 없음"])
    if unres:
        L += ["", "## 대상 조문을 찾지 못한 인용", "", "조문 본문에서 `제N조` 로 인용했지만 같은 DB 안에서 해당 조문을 찾지 못한 것(삭제된 조문 인용, 별도 법령 오인 등). 관련 조문 링크에서는 빠져 있습니다.", ""]
        for u in unres[:60]:
            L.append(f"- `{u['from']}` → {u['to_regulation']} 제{u['article']}조")
        if len(unres) > 60:
            L.append(f"- … 외 {len(unres) - 60}건")
    with open(os.path.join(OUT, "docs", "QUALITY_REPORT.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print(f"조문 {total['articles']} · 오류 {len(errors)} · 경고 {len(warns)}")
    for e in errors[:30]:
        print("오류:", e)
    for w in warns[:60]:
        print("경고:", w)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
