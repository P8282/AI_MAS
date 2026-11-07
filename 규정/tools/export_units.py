#!/usr/bin/env python3
"""조문·항·호·목 단위로 한 줄씩 펼친(flat) JSON Lines 만들기 — 다른 시스템(문제 생성기, 검증기, AI 질의응답 등)에서 쓰기 쉬운 모양.

  python3 규정/tools/export_units.py > units.jsonl            # 전체
  python3 규정/tools/export_units.py --regulation STATE_CONTRACT_ENFORCEMENT --level article

한 줄 = 한 단위(조 / 항 / 호 / 목). 필드는 규정/docs/DATA_SCHEMA.md '펼친 단위' 참고. 원문(content)은 articles.json 의 값 그대로.
"""
import argparse
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--regulation", help="규정 ID (생략하면 전체)")
    ap.add_argument("--level", choices=["article", "paragraph", "item", "subitem", "all"], default="all")
    a = ap.parse_args()
    regs = json.load(open(os.path.join(ROOT, "data", "regulations.json"), encoding="utf-8"))["regulations"]
    out = sys.stdout
    for reg in regs:
        if a.regulation and reg["id"] != a.regulation:
            continue
        arts = json.load(open(os.path.join(ROOT, reg["files"]["articles"]), encoding="utf-8"))
        for r in arts:
            base = {
                "regulation_id": r["regulation_id"], "regulation_name": r["regulation_name"], "short_name": r["short_name"], "document_type": r["document_type"],
                "chapter": r["chapter"], "section": r["section"], "article_number": r["article_number"], "article_title": r["article_title"],
                "effective_date": r["effective_date"], "revision_date": r["revision_date"], "source_file": r["source_file"], "source_path": r["source_path"],
                "source_page": r["source_page"], "keywords": r["keywords"], "related_articles": r["related_articles"], "related_regulations": r["related_regulations"],
                "is_supplementary": r["is_supplementary"], "is_scheduled": r["is_scheduled"], "is_deleted": r["is_deleted"],
            }

            def emit(level, id_, display, p, i, s, content):
                if a.level in ("all", level):
                    rec = dict(base, id=id_, display_id=display, level=level, paragraph_number=p, item_number=i, subitem_number=s, content=content)
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")

            emit("article", r["id"], r["display_id"], "", "", "", "\n".join(p["content"] for p in r["paragraphs"] if p["content"]))
            for p in r["paragraphs"]:
                emit("paragraph", p["id"], p["display_id"], p["paragraph_number"], "", "", p["content"])
                for it in p["items"]:
                    emit("item", it["id"], it["display_id"], p["paragraph_number"], it["item_number"], "", it["content"])
                    for sb in it["subitems"]:
                        emit("subitem", sb["id"], sb["display_id"], p["paragraph_number"], it["item_number"], sb["subitem_number"], sb["content"])


if __name__ == "__main__":
    main()
