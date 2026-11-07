# 화면(HTML) ↔ 데이터 ↔ PDF 감사

조달뽀개기 상세 화면이 실제로 그리는 HTML 이 데이터(JSON)·원문 PDF 와 같은지 확인한다. 필요: node + playwright, `python3 -m http.server 8765` (저장소 루트).

```bash
python3 -m http.server 8765 --bind 127.0.0.1 &
NODE_PATH=$(npm root -g) node 규정/tools/audit_html/render_dom.js   # 조문·부속 전체를 화면으로 열어 DOM 글자를 /tmp/regdb_dom.json 에 저장
python3 규정/tools/audit_html/compare_dom.py                        # DOM ↔ JSON ↔ PDF 대조
```
검사: 조문 DOM 글자 = JSON 항·호·목 글자 · 머리(조 번호+제목) · notes · DOM 이 PDF 에 연속으로 있는가 · 부속 DOM 글자수 ≈ PDF 글자수(±8%) · 표 개수.
