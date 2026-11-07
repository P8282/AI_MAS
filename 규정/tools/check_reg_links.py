#!/usr/bin/env python3
"""규정 ID 참조 무결성 검사 — 규정/tools/check_reg_links.py

저장소 전체에서 규정 DB 의 내부 ID 를 가리키는 연결(간선)을 모아, 지금의 규정 DB 로 열리는지 확인한다.
규정 마이그레이션(Sites 일괄 갱신 등) 전후에 실행해 '새로 끊어진 연결'이 0 건인지 보는 데 쓴다.

모으는 연결
  · quiz_reg     : 문제 JSON 의 "reg" 글(예: "국가계약법 시행령 제52조제1항") → lib/refs.js 의 parseReg 와 같은 규칙으로 만든 조문 ID (node 필요)
  · literal      : 규정 DB 밖의 JSON/JS/HTML(표준교재·법령해석·문제·도구 설정 등)에 그대로 적힌 ID
  · internal     : 규정 DB 안의 파생 연결(related_articles, correspondence.json, relations.json, keywords.json)
판정
  · active       : 현행 규정 DB 의 규정·조문·항·호·목·부속 ID
  · alias        : data/id-map.json 의 aliases(증거가 있는 old→new)로 현행 ID 에 닿음
  · tombstone    : data/id-map.json 의 tombstones 로 history/ 의 이전 판에서 열림(현행 본문에는 없음)
  · broken       : 어디에서도 열리지 않음

사용
  python3 규정/tools/check_reg_links.py --out report.json                    # 현재 상태 보고
  python3 규정/tools/check_reg_links.py --baseline before.json --out after.json   # 새로 끊어진 연결이 있으면 종료코드 1
  --root <규정 폴더>  : 다른 위치(스테이징 등)의 규정 DB 로 판정
"""
import argparse, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
TOKEN = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
SUFFIX = re.compile(r"^_(?:ART|SUPP|ANNEX|FORM|ATTACH|PART|API_|C\d|NEXT)")
SKIP_DIRS = {".git", "node_modules", "규정검색rev", "원문", ".wrangler"}
SKIP_PREFIX = ("규정/regulations/", "규정/data/", "규정/markdown/", "규정/docs/", "규정/pdf/", "규정/tools/", "규정/.import")
EXTS = (".json", ".js", ".mjs", ".cjs", ".html")


def jload(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def walk_ids(rows, out):
    for a in rows:
        out.add(a["id"])
        for p in a.get("paragraphs") or []:
            out.add(p["id"])
            for i in p.get("items") or []:
                out.add(i["id"])
                for s in i.get("subitems") or []:
                    out.add(s["id"])


def universe(root):
    regs = jload(os.path.join(root, "data", "regulations.json"))["regulations"]
    ids, reg_ids, records = set(), [], {}
    for r in regs:
        reg_ids.append(r["id"])
        ids.add(r["id"])
        d = os.path.join(root, "regulations", r["slug"])
        arts = jload(os.path.join(d, "articles.json"))
        anns = jload(os.path.join(d, "annexes.json")) if os.path.exists(os.path.join(d, "annexes.json")) else []
        walk_ids(arts, ids)
        walk_ids(anns, ids)
        records[r["id"]] = (arts, anns)
    idmap = {"aliases": {}, "tombstones": {}}
    p = os.path.join(root, "data", "id-map.json")
    if os.path.exists(p):
        idmap = jload(p)
    return ids, sorted(reg_ids, key=len, reverse=True), records, idmap


def classify(i, ids, idmap):
    if i in ids:
        return "active"
    seen = set()
    cur = i
    while cur in idmap.get("aliases", {}) and cur not in seen:
        seen.add(cur)
        cur = idmap["aliases"][cur]
        if cur in ids:
            return "alias"
    if i in idmap.get("tombstones", {}):
        return "tombstone"
    return "broken"


def is_reg_ref(tok, reg_ids):
    for r in reg_ids:
        if tok == r:
            return True
        if tok.startswith(r + "_") and SUFFIX.match(tok[len(r):]):
            return True
    return False


def repo_files():
    for base, dirs, files in os.walk(REPO):
        rel = os.path.relpath(base, REPO)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (rel == "." and d.startswith("."))]
        for fn in files:
            if not fn.endswith(EXTS):
                continue
            p = os.path.join(base, fn)
            r = os.path.relpath(p, REPO).replace(os.sep, "/")
            if r.startswith(SKIP_PREFIX):
                continue
            yield r, p


def collect_reg_texts():
    """문제 JSON 의 "reg" 키 글(문자열)을 모은다."""
    out = []

    def walk(v, f, path):
        if isinstance(v, dict):
            for k, x in v.items():
                if k == "reg" and isinstance(x, str) and x.strip():
                    out.append({"file": f, "path": path + "." + k, "text": x})
                else:
                    walk(x, f, path + "." + k)
        elif isinstance(v, list):
            for n, x in enumerate(v):
                walk(x, f, f"{path}[{n}]")
    for r, p in repo_files():
        if not r.endswith(".json"):
            continue
        try:
            walk(jload(p), r, "$")
        except (ValueError, UnicodeDecodeError):
            continue
    return out


NODE = r"""
const R = require(process.argv[1]); let buf = "";
process.stdin.on("data", d => buf += d).on("end", () => {
  const rows = JSON.parse(buf);
  const out = rows.map(x => R.parseReg(x.text).filter(p => p.t === "art").map(p => p.id));
  process.stdout.write(JSON.stringify(out));
});
"""


def parse_quiz_refs(rows):
    if not rows:
        return []
    r = subprocess.run(["node", "-e", NODE, os.path.join(REPO, "lib", "refs.js")], input=json.dumps(rows, ensure_ascii=False), capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def gather(root):
    ids, reg_ids, records, idmap = universe(root)
    edges = []      # (source, file, id)
    rows = collect_reg_texts()
    for row, parsed in zip(rows, parse_quiz_refs(rows)):
        for i in parsed:
            edges.append(("quiz_reg", row["file"], i))
    for r, p in repo_files():
        try:
            text = open(p, encoding="utf-8").read()
        except UnicodeDecodeError:
            continue
        for tok in set(TOKEN.findall(text)):
            if is_reg_ref(tok, reg_ids):
                edges.append(("literal", r, tok))
    for rid, (arts, anns) in records.items():
        for a in arts + anns:
            for t in a.get("related_articles") or []:
                edges.append(("internal", f"regulations/{rid}:related_articles", t))
    cp = os.path.join(root, "data", "correspondence.json")
    if os.path.exists(cp):
        for k, v in (jload(cp).get("links") or {}).items():
            for t in [k] + list(v):
                edges.append(("internal", "data/correspondence.json", t))
    rel = jload(os.path.join(root, "data", "relations.json"))
    for t in (rel.get("article_links") or {}).get("ids", []):
        edges.append(("internal", "data/relations.json", t))
    kw = jload(os.path.join(root, "data", "keywords.json"))
    for v in (kw.get("index") or {}).values():
        for t in v:
            edges.append(("internal", "data/keywords.json", t))
    edges = sorted(set(edges))
    res = []
    for s, f, i in edges:
        res.append({"source": s, "file": f, "id": i, "status": classify(i, ids, idmap)})
    return res, len(ids)


def summarize(res, n_ids):
    from collections import Counter
    c = Counter((x["source"], x["status"]) for x in res)
    return {"ids": n_ids, "edges": len(res), "by_source_status": {f"{s}:{t}": n for (s, t), n in sorted(c.items())}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.join(REPO, "규정"))
    ap.add_argument("--out")
    ap.add_argument("--baseline")
    a = ap.parse_args()
    res, n = gather(a.root)
    rep = {"schema_version": 1, "root": os.path.relpath(a.root, REPO), "summary": summarize(res, n),
           "broken": [x for x in res if x["status"] == "broken"], "compat": [x for x in res if x["status"] in ("alias", "tombstone")],
           "edges_all": [x for x in res if x["source"] != "internal"]}
    code = 0
    if a.baseline:
        base = jload(a.baseline)
        before_broken = {(x["source"], x["file"], x["id"]) for x in base["broken"]}
        new = [x for x in rep["broken"] if (x["source"], x["file"], x["id"]) not in before_broken]
        fixed = sorted(before_broken - {(x["source"], x["file"], x["id"]) for x in rep["broken"]})
        rep["compare"] = {"baseline": os.path.relpath(a.baseline, REPO) if os.path.isabs(a.baseline) else a.baseline,
                          "baseline_broken": len(before_broken), "new_broken": new, "new_broken_count": len(new), "no_longer_broken": len(fixed)}
        code = 1 if new else 0
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
            f.write("\n")
    print(json.dumps(rep["summary"], ensure_ascii=False, indent=1))
    if "compare" in rep:
        print(f"기존 끊김 {rep['compare']['baseline_broken']} · 새로 끊김 {rep['compare']['new_broken_count']} · 해결됨 {rep['compare']['no_longer_broken']}")
    return code


if __name__ == "__main__":
    sys.exit(main())
