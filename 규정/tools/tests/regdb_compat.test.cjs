/* 규정/js/regdb.js 호환 시험 — 마이그레이션 뒤 기존 링크가 열리는지(브라우저 없이 파일에서 읽음)
   node 규정/tools/tests/regdb_compat.test.cjs [links-report.json]
   links-report.json(check_reg_links.py --out)을 주면 문제·교재·해석 등 저장소의 모든 연결 ID 를 getArticle 로 열어 본다. */
"use strict";
const fs = require("fs"), path = require("path"), assert = require("assert");
const ROOT = path.resolve(__dirname, "../../..");
const RegDB = require(path.join(ROOT, "규정/js/regdb.js"));
const db = new RegDB({ base: path.join(ROOT, "규정") + "/", fetchJSON: async url => JSON.parse(fs.readFileSync(url.replace(/\?v=.*$/, ""), "utf8")) });
const idmap = JSON.parse(fs.readFileSync(path.join(ROOT, "규정/data/id-map.json"), "utf8"));
let n = 0;
const ok = (c, m) => { assert.ok(c, m); n++; };

(async () => {
  await db.init();
  const open = id => db.getArticle(id);
  // 본조 · 가지조 · 항 · 호(가지 호) · 목
  let d = await open("STATE_CONTRACT_ENFORCEMENT_ART_37");
  ok(d && d.article.article_title === "입찰보증금" && !d.archived, "본조");
  d = await open("STATE_CONTRACT_ACT_ART_28_2");
  ok(d && d.article.article_label === "제28조의2", "가지조");
  d = await open("STATE_CONTRACT_ENFORCEMENT_ART_37_P2_I4_S1");
  ok(d && d.focus === "STATE_CONTRACT_ENFORCEMENT_ART_37_P2_I4_S1" && !d.focusMissing && d.article.id === "STATE_CONTRACT_ENFORCEMENT_ART_37", "목 → 조문 + focus");
  d = await open("STATE_CONTRACT_ACT_ART_28_P1_I1_2");
  ok(d && !d.focusMissing && d.article.paragraphs[0].items.some(i => i.id === "STATE_CONTRACT_ACT_ART_28_P1_I1_2" && i.item_number === "1의2"), "가지 호(1의2) ID 유지");
  ok(!(await open("STATE_CONTRACT_ACT_ART_28")).article.paragraphs[0].items.some(i => i.id.endsWith("_I12")), "1의2 가 제12호로 바뀌지 않음");
  d = await open("PPS_PROPOSAL_EVALUATION_GUIDE_ART_9_P3_I2_2");
  ok(d && !d.focusMissing && !d.archived, "행정규칙 호 재분할로 기존 ID 유지");
  // 부칙(번호 다시 매김) → 이전 판
  d = await open("STATE_CONTRACT_ENFORCEMENT_SUPP_ART_1");
  ok(d && d.archived && d.archived.why === "supplement-renumbered" && d.article.id === "STATE_CONTRACT_ENFORCEMENT_SUPP_ART_1" && d.article.pdf_path, "PDF 부칙 → history");
  // 삭제 조문
  d = await open("PPS_GOODS_MAS_RULE_ART_68");
  ok(d && d.article.is_deleted, "삭제 조문 표시 유지");
  // 시행 예정본(현행과 별도 ID, 합치지 않음)
  d = await open("LOCAL_CONTRACT_RULE_ART_25_NEXT1");
  ok(d && d.article.is_scheduled && d.article.variant_of === "LOCAL_CONTRACT_RULE_ART_25" && d.article.source === "pdf-scheduled", "예정본 보존");
  ok((await open("LOCAL_CONTRACT_RULE_ART_25")).article.source === "api", "현행본은 API");
  ok(db.findArticleId("SME_TECH_INNOVATION_ACT", 9) === "SME_TECH_INNOVATION_ACT_ART_9", "전체 예정 규정의 조 번호 찾기");
  ok(db.findArticleId("STATE_CONTRACT_ENFORCEMENT", 37) === "STATE_CONTRACT_ENFORCEMENT_ART_37", "조 번호 찾기");
  // 별표: API 미제공 PDF 부속은 출처 구분
  d = await open("BIDDER_PREQUALIFICATION_RULE_ANNEX_2");
  ok(d && d.article.source === "pdf-attachment" && /시행 버전/.test(d.article.source_note) && d.article.pdf_path, "pdf-attachment 별표");
  // outline: 지방 집행기준은 그대로, 목록형 → 조문형으로 바뀐 고시는 이전 판
  d = await open("LOCAL_BID_CONTRACT_EXECUTION_GUIDELINE_C1_SEC1_N4");
  ok(d && !d.archived, "outline 직접 링크");
  d = await open("SUBCONTRACT_FINE_NOTICE_C2_N1");
  ok(d && d.archived && d.archived.why === "format-changed", "형식이 바뀐 outline ID → history");
  // 구조가 바뀐 항·호·목 → 이전 판 + 현행 조문 안내
  const sc = Object.entries(idmap.tombstones).find(([, t]) => t.why === "structure-changed" && t.current);
  d = await open(sc[0]);
  ok(d && d.archived && d.archived.current === sc[1].current && d.focus === sc[0], "구조 변경 tombstone");
  // 이전 판은 검색 인덱스에 없음(중복 검색 방지)
  ok(!db.byId.has("STATE_CONTRACT_ENFORCEMENT_SUPP_ART_1") && !Object.keys(idmap.tombstones).some(i => db.byId.has(i)), "tombstone ID 는 인덱스에 없음");
  ok((await open("NO_SUCH_REG_ART_1")) === null, "없는 ID 는 null");
  // 모든 tombstone 이 열림
  let tb = 0;
  for (const [i, t] of Object.entries(idmap.tombstones)) { const x = await open(i); assert.ok(x && x.archived && x.article.id === t.a, "tombstone " + i); tb++; }
  ok(tb === Object.keys(idmap.tombstones).length, "tombstone 전부 " + tb);
  // 저장소의 연결(문제 해설 '관련 규정'·교재·법령해석·문제 JSON 의 ID)
  const rep = process.argv[2];
  if (rep) {
    const r = JSON.parse(fs.readFileSync(rep, "utf8"));
    const before = new Set(r.broken.map(x => x.id));
    const ids = [...new Set((r.edges_all || []).map(x => x.id))];
    let opened = 0, failed = [];
    const regIds = new Set(db.regs.map(r => r.id));          // 규정 ID 자체(관련 규정)는 규정 목록으로 연다
    for (const id of ids) { const x = regIds.has(id) || await open(id); if (x) opened++; else if (!before.has(id)) failed.push(id); }
    assert.deepStrictEqual(failed, [], "열리지 않는 연결");
    console.log(`저장소 연결 ID ${ids.length}개 중 ${opened}개 열림 · 기존부터 끊긴 ${before.size}개 제외`);
    n++;
  }
  console.log(`ok ${n}건`);
})().catch(e => { console.error(e); process.exit(1); });
