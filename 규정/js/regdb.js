/* 규정 DB 접근·검색 엔진 — 규정/js/regdb.js
   · 화면(DOM)·조달뽀개기에 의존하지 않는다. 다른 웹앱에서도 이 파일 하나와 규정/data·규정/regulations 만 있으면 같은 방식으로 쓸 수 있다.
   · 구조: 검색용 경량 인덱스(data/regulation-index.json) 먼저 → 필요한 규정의 상세(regulations/<slug>/articles.json)는 그때그때 불러온다(지연 로딩).
   · 검색은 '질의 해석 → 후보 찾기(matchers) → 점수' 단계로 나뉘어 있어서, 자동완성·유사어·초성·태그·필터 같은 기능을 matcher/필터로 덧붙일 수 있다.

   사용 예)
     const db = new RegDB({ base: "규정/" });
     await db.init();                                   // 인덱스 + 키워드 사전만 읽음
     const q = db.parse("국가계약법 시행령 제37조");     // {regs:[…], art:{num:37,br:0}, tokens:[…]}
     const hits = db.searchIndex(q);                    // 즉시(제목·번호·키워드·규정명)
     await db.searchBody(q, {}, (newHits, prog) => …);  // 본문 검색(규정별로 불러오며 점진적으로)
     const a = await db.getArticle("STATE_CONTRACT_ENFORCEMENT_ART_37");
   공개: RegDB (생성자) · RegDB.norm · RegDB.plainText · RegDB.highlightRegex
*/
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.RegDB = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const nfc = s => String(s == null ? "" : s).normalize("NFC");
  /* 비교용 정규화: 공백·가운뎃점·괄호·따옴표 등을 없애고 소문자로 */
  const norm = s => nfc(s).toLowerCase().replace(/(\d)\.(?=\d)/g, "$1\u2024").replace(/[\s ·ㆍ‧∙・\-–—_.,:;()\[\]{}<>「」『』“”"'‘’!?/\\]+/g, "").replace(/\u2024/g, ".");   // 숫자 사이의 소수점(0.75)은 남김
  /* 비율 표기 검색: 법령의 '1천분의 0.75' 는 백분율로 0.075(%) 이므로 '0.075'·'0.05%' 로도 찾을 수 있게 한다.
     소수 d 를 백분율로 보면 천분율은 d×10, 비율(0.00075)로 보면 d×1000 → '1천분의 N' 꼴로 바꿔 본문에서 같이 찾는다. */
  function permilleForms(w) {
    const m = /^(\d*\.\d+|\d+)\s*%?$/.exec(String(w).trim());
    if (!m || !/\./.test(m[1]) && !/%$/.test(String(w).trim())) return [];
    const d = parseFloat(m[1]), out = [];
    const fmt = x => { const r = Math.round(x * 1e6) / 1e6; return r > 0 && r < 1000 ? String(r) : ""; };
    for (const x of [d * 10, d < 0.01 ? d * 1000 : 0]) { const f = fmt(x); if (f) out.push("1천분의 " + f); }
    return uniq(out);
  }
  const CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ";
  const cho = s => {
    let o = "";
    for (const ch of nfc(s)) {
      const c = ch.charCodeAt(0);
      if (c >= 0xac00 && c <= 0xd7a3) o += CHO[Math.floor((c - 0xac00) / 588)];
      else if (/[ㄱ-ㅎ]/.test(ch)) o += ch;
      else if (/[0-9a-z]/i.test(ch)) o += ch.toLowerCase();
    }
    return o;
  };
  const esc = s => String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const uniq = a => [...new Set(a)];

  const ART_Q = /제?\s*(\d+)\s*조(?:\s*의\s*(\d+))?/;
  const PARA_Q = /제?\s*(\d+)\s*항/;
  const TYPE_GROUPS = {
    law: ["법률"], decree: ["시행령", "대통령령"], rule: ["시행규칙", "부령"], yegyu: ["계약예규"], pps: ["지침", "훈령", "고시", "공고"], interp: ["법령해석례"],
  };

  /* 글자 사이에 공백이 끼어 있어도 찾는 정규식 (원문의 줄 이음 공백 차이를 흡수) */
  function highlightRegex(words) {
    const parts = words.filter(Boolean).map(w => nfc(w).replace(/\s+/g, "").split("").map(esc).join("\\s*"));
    return parts.length ? new RegExp(parts.sort((a, b) => b.length - a.length).join("|"), "g") : null;
  }

  /* 조문 → 평문 (항·호·목 표지를 붙여서) */
  function plainText(a) {
    const out = [];
    for (const p of a.paragraphs || []) {
      out.push(((p.paragraph_number ? p.paragraph_number + " " : "") + p.content).trim());
      for (const it of p.items || []) {
        const mk = n => (/[.)]$/.test(n) ? n : n + ".");      // '1' → '1.', '1)'·'가)' 는 그대로
        out.push("  " + mk(it.item_number) + " " + it.content);
        for (const sb of it.subitems || []) out.push("    " + mk(sb.subitem_number) + " " + sb.content);
      }
    }
    return out.filter(x => x.trim()).join("\n");
  }

  function RegDB(opts) {
    opts = opts || {};
    this.base = (opts.base || "규정/").replace(/\/?$/, "/");
    this.fetchJSON = opts.fetchJSON || null;
    this.build = "";
    this._init = null;
    this._reg = new Map();          // regIdx → Promise<{articles, annexes, byId}>
    this._data = new Map();         // regIdx → 이미 읽은 {articles, annexes, byId}
    this.hide = new Set();          // 숨긴 규정 번호 (학생 설정의 규정 검색 범위: 목록·검색에서 제외)
    this.matchers = [];             // 사용자 추가 matcher: (row, pq) → 점수(0=불일치) 또는 {score, via}
  }

  RegDB.norm = norm;
  RegDB.cho = cho;
  RegDB.plainText = plainText;
  RegDB.highlightRegex = highlightRegex;
  RegDB.TYPE_GROUPS = TYPE_GROUPS;
  /* 규정 검색 분야 필터: 꺼 둔 분야(group: 국가계약·지방계약·방위사업 …)의 규정은 목록·검색에서 뺀다 */
  RegDB.prototype.setScope = function (offGroups) {   // offGroups: 제외할 분야 이름들
    const off = new Set(offGroups || []);
    this.hide = new Set(this.regs.filter(r => off.has(r.group)).map(r => r.i));
  };

  RegDB.prototype._get = async function (path, revalidate) {
    const url = this.base + path + (!revalidate && this.build ? "?v=" + this.build : "");
    if (this.fetchJSON) return this.fetchJSON(url);
    const r = await (typeof window !== "undefined" && window.PPS_ENC ? window.PPS_ENC.fetch : fetch)(url, revalidate ? { cache: "no-cache" } : undefined);   // 암호화된 파일이면 lib/enc.js 가 풀어 줌
    if (!r.ok) throw new Error(path + " " + r.status);
    return r.json();
  };

  /* 인덱스와 키워드 사전을 읽는다 (규정 본문은 읽지 않음) */
  RegDB.prototype.init = function () {
    if (!this._init) this._init = this._load().catch(e => { this._init = null; throw e; });
    return this._init;
  };

  RegDB.prototype._load = async function () {
    const idx = await this._get("data/regulation-index.json", true);
    this.build = idx.build_id || "";
    let kw = { terms: [] };
    try { kw = await this._get("data/keywords.json"); } catch (e) { /* 사전이 없어도 검색은 동작 */ }
    this.regs = idx.regulations.map((r, i) => Object.assign({}, r, { i, nname: norm(r.name), nshort: norm(r.short_name), nofficial: uniq([r.name, r.short_name, r.slug.replace(/-/g, ""), r.display_id_prefix || ""].map(norm)).filter(Boolean),
      naliases: uniq([r.name, r.short_name, r.slug.replace(/-/g, ""), ...r.aliases].map(norm)).filter(x => x.length >= 2) }));
    const rows = [];
    let order = 0;
    for (const a of idx.articles) rows.push(this._row(a, "article", order++));
    for (const a of idx.annexes) rows.push(this._row(a, "annex", order++));
    this.rows = rows;
    this.byId = new Map(rows.map(r => [r.id, r]));
    this.build_id = idx.build_id;
    this.generated = idx.generated;
    /* 유사어: 어느 한 형태로 찾으면 같은 대표어의 다른 형태도 함께 찾는다 */
    this.terms = kw.terms || [];
    this.syn = new Map();
    for (const t of this.terms) {
      const forms = uniq([t.term, ...t.aliases].map(norm)).filter(Boolean);
      for (const f of forms) this.syn.set(f, uniq([...(this.syn.get(f) || []), ...forms]));
    }
    return this;
  };

  RegDB.prototype._row = function (a, kind, order) {
    const reg = a.r;
    const label = kind === "annex" ? a.kind + (a.n ? " " + a.n : "") : a.n;
    return {
      id: a.id, r: reg, kind, label, title: a.t || "", keywords: a.k || [], chapter: a.c || "",
      related: a.rr || [], deleted: !!a.x, supp: a.s || 0, future: a.f || "", annexKind: a.kind || "",
      nlabel: norm(label), ntitle: norm(a.t || ""), nkw: norm((a.k || []).join(" ")), ncho: cho(a.t || ""), order,
      num: kind === "article" ? (/^제(\d+)조(?:의(\d+))?$/.exec(a.n) || []).slice(1).map(x => +x || 0) : null,
    };
  };

  RegDB.prototype.regulation = function (i) { return this.regs[i]; };
  RegDB.prototype.regOf = function (row) { return this.regs[row.r]; };

  /* ───── 질의 해석 ─────
     "국가계약법 시행령 제37조 보증금" → regs=[시행령], art={num:37}, tokens=[보증금] */
  RegDB.prototype.parse = function (raw, filt) {
    raw = nfc(raw).trim();
    const pq = { raw, regs: null, art: null, para: null, tokens: [], cho: null, text: "", regAliasOnly: false };
    let rest = raw;
    // 규정명: 긴 별칭부터. 겹치는 짧은 별칭(국가계약법 ⊂ 국가계약법 시행령)은 긴 것이 이미 차지한 자리면 건너뜀
    const nrest = () => norm(rest);
    const aliases = [];
    for (const r of this.regs) for (const al of r.naliases) aliases.push([al, r.i]);
    aliases.sort((a, b) => b[0].length - a[0].length);
    const hit = new Set();
    let nr = nrest();
    const found = [];
    for (const [al, i] of aliases) {
      if (!nr.includes(al)) continue;
      if (found.some(f => f.al.includes(al) && f.al !== al)) continue;     // 더 긴 별칭의 일부
      if (!found.some(f => f.al === al)) found.push({ al, regs: [] });
      found.find(f => f.al === al).regs.push(i);
    }
    // 주제어 별칭(예: '적격심사')만 쓴 경우는 규정 필터가 아니라 검색어로 본다. 공식 이름이거나 조 번호가 함께 있으면 규정 필터
    const hasArt = ART_Q.test(rest) || /^\s*\d{1,4}\s*$/.test(rest);
    const topical = [];
    for (const f of [...found]) {
      const official = f.regs.some(i => this.regs[i].nofficial.includes(f.al));
      if (!official && !hasArt) { topical.push(f.al); found.splice(found.indexOf(f), 1); }
    }
    for (const f of found) { f.regs.forEach(i => hit.add(i)); }
    pq.topicalRegs = topical.length ? uniq(topical.flatMap(al => this.regs.filter(r => r.naliases.includes(al)).map(r => r.i))) : [];
    if (hit.size) {
      pq.regs = [...hit];
      // 규정명 부분을 질의에서 걷어냄 (공백 무시)
      for (const f of found) rest = rest.replace(new RegExp(f.al.split("").map(esc).join("[\\s\\-·ㆍ]*"), "i"), " ");
    }
    // 조 번호
    const m = ART_Q.exec(rest);
    if (m) {
      pq.art = { num: +m[1], br: m[2] ? +m[2] : 0 };
      rest = rest.replace(m[0], " ");
      const pm = PARA_Q.exec(rest);
      if (pm) { pq.para = +pm[1]; rest = rest.replace(pm[0], " "); }
    } else if (/^\s*\d{1,4}\s*$/.test(rest)) {
      pq.art = { num: +rest.trim(), br: 0 };
      rest = "";
    }
    rest = rest.replace(/[「」『』“”"']/g, " ").trim();
    // 법령 종류 낱말은 필터로: '시행령 제42조' → 시행령만
    const typeWords = { "시행령": "decree", "대통령령": "decree", "시행규칙": "rule", "부령": "rule", "법률": "law", "예규": "yegyu", "계약예규": "yegyu", "훈령": "pps", "고시": "pps", "지침": "pps", "공고": "pps" };
    const kept = [];
    const typeSel = [];
    for (const w of rest.split(/\s+/).filter(Boolean)) { if (typeWords[w] && (pq.art || pq.regs || rest.split(/\s+/).length > 1)) typeSel.push(typeWords[w]); else kept.push(w); }
    if (typeSel.length) rest = kept.join(" ");
    pq.filt = Object.assign({}, filt || {});
    if (typeSel.length) pq.filt.types = uniq([...(pq.filt.types || []), ...typeSel]);
    pq.text = rest;
    if (rest && /^[ㄱ-ㅎ\s]+$/.test(rest) && rest.replace(/\s/g, "").length >= 2) pq.cho = rest.replace(/\s/g, "");
    else pq.tokens = rest.split(/\s+/).filter(w => norm(w)).map(w => {
      const t = norm(w), hl = permilleForms(w);
      return { n: t, alts: uniq([...(this.syn.get(t) || [t]), ...hl.map(norm)]), hl };
    });
    pq.regAliasOnly = !!(pq.regs && !pq.art && !pq.tokens.length && !pq.cho);
    return pq;
  };

  RegDB.prototype._allowed = function (row, pq) {
    const f = pq.filt || {};
    const reg = this.regs[row.r];
    if (this.hide.has(row.r)) return false;
    if (pq.regs && !pq.regs.includes(row.r)) return false;
    if (f.reg != null && f.reg !== row.r) return false;
    if (f.types && f.types.length) {
      const ok = f.types.some(t => (TYPE_GROUPS[t] || [t]).includes(reg.document_type));
      if (!ok) return false;
    }
    if (f.group && reg.group !== f.group) return false;
    if (f.effectiveFrom && reg.effective_date < f.effectiveFrom) return false;
    if (f.effectiveTo && reg.effective_date > f.effectiveTo) return false;
    if (!f.includeSupp && (row.supp || row.deleted) && !(pq.art)) return false;   // 부칙·삭제 조문은 번호로 찾을 때만 (또는 켰을 때)
    if (!f.includeAnnex && row.kind === "annex" && f.includeAnnex === false) return false;
    return true;
  };

  /* ───── 1단계: 인덱스 검색 (즉시) ───── */
  RegDB.prototype.searchIndex = function (pq) {
    const hits = [];
    const toks = pq.tokens;
    for (const row of this.rows) {
      if (!this._allowed(row, pq)) continue;
      let score = 0, via = [];
      if (pq.art) {
        if (row.kind !== "article") continue;
        if (row.num[0] !== pq.art.num || (row.num[1] || 0) !== pq.art.br) continue;
        score = pq.regs ? 1000 : 600;
        via.push("조문 번호");
      }
      if (toks.length) {
        let all = true, s = 0;
        const v = new Set();
        for (const t of toks) {
          let best = 0;
          for (const a of t.alts) {
            if (row.ntitle === a) { best = Math.max(best, 500); v.add("제목"); }
            else if (row.ntitle.startsWith(a)) { best = Math.max(best, 420); v.add("제목"); }
            else if (row.ntitle.includes(a)) { best = Math.max(best, 330); v.add("제목"); }
            if (row.nkw.includes(a)) { best = Math.max(best, row.keywords.some(k => norm(k) === a) ? 350 : 220); v.add("키워드"); }
            if (row.nlabel === a) { best = Math.max(best, 300); v.add("번호"); }
            if (norm(row.chapter).includes(a)) { best = Math.max(best, 90); v.add("장·절"); }
          }
          if (!best) { all = false; break; }
          s += best;
        }
        if (!all) continue;
        score += s / toks.length;
        via.push(...v);
      } else if (pq.cho) {
        if (!(row.ncho.includes(pq.cho) || cho(row.keywords.join("")).includes(pq.cho))) continue;
        score += row.ncho.startsWith(pq.cho) ? 300 : 200;
        via.push("초성");
      } else if (!pq.art) {
        if (!pq.regs && (pq.filt || {}).reg == null) continue;      // 규정명만 입력하거나 규정을 골랐을 때: 그 규정의 전체 조문·별표 목록(탐색)
        score = 50 - row.order / 1e5;
        via.push("규정");
      }
      for (const mt of this.matchers) { const x = mt(row, pq); if (x) score += typeof x === "number" ? x : x.score; }
      score = this._adjust(row, score);
      hits.push({ id: row.id, row, score, via, snippet: "" });
    }
    return this._sort(hits);
  };

  RegDB.prototype._adjust = function (row, score) {
    if (row.deleted) score -= 300;
    if (row.supp) score -= 200;
    if (row.future) score -= 5;
    if (row.kind === "annex") score -= 2;
    return score;
  };

  RegDB.prototype._sort = function (hits) {
    return hits.sort((a, b) => b.score - a.score || a.row.order - b.row.order);
  };

  /* 규정(법령) 자체를 찾는 결과: 규정명·약칭·별칭에 질의가 들어 있는 것 */
  RegDB.prototype.searchRegulations = function (pq) {
    if (pq.regs && !pq.art && (pq.regAliasOnly)) return pq.regs.filter(i => !this.hide.has(i)).map(i => this.regs[i]);
    const q = norm(pq.raw);
    if (q.length < 2 || pq.art || pq.cho) return [];
    return this.regs.filter(r => !this.hide.has(r.i) && r.naliases.some(a => a.includes(q)));
  };

  /* ───── 규정 상세 불러오기 (지연 로딩) ───── */
  RegDB.prototype.loadRegulation = function (i) {
    if (!this._reg.has(i)) {
      const reg = this.regs[i];
      const p = Promise.all([
        this._get(`regulations/${reg.slug}/articles.json`),
        this._get(`regulations/${reg.slug}/annexes.json`).catch(() => []),
      ]).then(([articles, annexes]) => {
        const byId = new Map();
        articles.forEach((a, k) => { a._k = k; byId.set(a.id, a); });
        annexes.forEach((a, k) => { a._k = k; byId.set(a.id, a); });
        const data = { reg, articles, annexes, byId };
        this._data.set(i, data);
        return data;
      });
      p.catch(() => this._reg.delete(i));
      this._reg.set(i, p);
    }
    return this._reg.get(i);
  };

  RegDB.prototype.isLoaded = function (i) { return this._data.has(i); };

  /* 이미 읽어 둔 규정이면 조문 평문을 바로 돌려줌(아니면 ""). 읽지 않은 규정은 건드리지 않는다 */
  RegDB.prototype.textOf = function (row) {
    const d = this._data.get(row.r);
    const a = d && d.byId.get(row.id);
    if (!a) return "";
    return a._t || (a._t = a.annex_kind ? a.annex_title + "\n" + a.content : plainText(a));
  };

  /* 사라진 ID 를 여는 표(data/id-map.json): aliases = 증거가 있는 old→new, tombstones = history/<h>/ 의 이전 판에서 a(조문)를 연다. 없으면 빈 표 */
  RegDB.prototype.idMap = function () {
    if (!this._idmap) this._idmap = this._get("data/id-map.json").then(j => ({ aliases: (j && j.aliases) || {}, tombstones: (j && j.tombstones) || {} })).catch(() => ({ aliases: {}, tombstones: {} }));
    return this._idmap;
  };

  const SUB_ID = /^(.+?)(_P\d+(?:_I\d+(?:_\d+)?(?:_S\d+(?:_\d+)?)?)?)$/;   // 조문 ID + 항·호·목 꼬리표
  const hasUnit = (a, id) => (a.paragraphs || []).some(p => p.id === id || (p.items || []).some(it => it.id === id || (it.subitems || []).some(s => s.id === id)));

  /* 조문(또는 부속) 하나 + 이웃.
     id 가 항·호·목 ID 이면 그 조문을 열고 focus 에 그 ID 를 둔다. 현행에 없는 ID 는 alias(현행 ID 로) → tombstone(이전 판, archived) 순으로 연다. */
  RegDB.prototype.getArticle = async function (id, _depth) {
    await this.init();
    const row = this.byId.get(id);
    if (!row) {
      if ((_depth || 0) > 8) return null;
      const map = await this.idMap();
      const al = map.aliases[id];
      if (al) { const d = await this.getArticle(al, (_depth || 0) + 1); if (d) d.aliasFrom = d.aliasFrom || id; return d; }
      const t = map.tombstones[id];
      if (t) return this.getArchived(id, t);
      const m = SUB_ID.exec(id);
      if (m && m[2] && this.byId.has(m[1])) {
        const d = await this.getArticle(m[1], (_depth || 0) + 1);
        if (d) { d.focus = id; d.focusMissing = !hasUnit(d.article, id); }
        return d;
      }
      return null;
    }
    const data = await this.loadRegulation(row.r);
    const a = data.byId.get(id);
    if (!a) return null;
    const list = a.annex_kind ? data.annexes : data.articles;
    const sib = list.filter(x => !x.is_scheduled);
    const k = sib.indexOf(a);
    return { article: a, reg: data.reg, row, prev: k > 0 ? sib[k - 1] : null, next: k >= 0 && k < sib.length - 1 ? sib[k + 1] : null, data };
  };

  /* 이전 판(history)에서 사라진 조문 열기 — 검색 인덱스에는 넣지 않으므로 현행 본문과 중복 검색되지 않는다 */
  RegDB.prototype.getArchived = async function (id, t) {
    const reg = this.regs.find(r => r.id === t.r);
    if (!reg) return null;
    const base = `regulations/${reg.slug}/history/${t.h}/`;
    const [articles, annexes] = await Promise.all([this._get(base + "articles.json").catch(() => []), this._get(base + "annexes.json").catch(() => [])]);
    const a = articles.find(x => x.id === t.a) || annexes.find(x => x.id === t.a);
    if (!a) return null;
    const current = t.current && this.byId.has(t.current) ? t.current : null;
    return { article: a, reg, row: null, prev: null, next: null, data: null, archived: { from: id, version: t.h, why: t.why || "", current }, focus: id !== t.a ? id : null };
  };

  /* 조 번호로 조문 ID 찾기: "국가계약법-시행령", 37 → ID (현행본) */
  RegDB.prototype.findArticleId = function (regId, num, br) {
    const r = this.regs.find(x => x.id === regId);
    if (!r) return null;
    const row = this.rows.find(x => x.r === r.i && x.kind === "article" && !x.supp && !x.future && x.num && x.num[0] === num && (x.num[1] || 0) === (br || 0));
    return row ? row.id : null;
  };

  RegDB.prototype.listRegulation = function (i, opts) {
    opts = opts || {};
    return this.rows.filter(r => r.r === i && r.kind === "article" && (opts.all || (!r.deleted && !r.supp)));
  };

  /* ───── 2단계: 본문 검색 (규정별로 불러오며 점진적으로) ─────
     onHits(newHits, {done, total, regulation}) 를 규정 하나 끝날 때마다 부른다. signal.aborted 이면 멈춘다. */
  RegDB.prototype.searchBody = async function (pq, opts, onHits) {
    opts = opts || {};
    const toks = pq.tokens;
    if (!toks.length) return;
    const signal = opts.signal || { aborted: false };
    let order = this.regs.map(r => r.i).filter(i => !this.hide.has(i)).filter(i => (!pq.regs || pq.regs.includes(i)) && (pq.filt.reg == null || pq.filt.reg === i));
    if (pq.filt.types && pq.filt.types.length) order = order.filter(i => pq.filt.types.some(t => (TYPE_GROUPS[t] || [t]).includes(this.regs[i].document_type)));
    if (opts.priority) order.sort((a, b) => (opts.priority.has(b) ? 1 : 0) - (opts.priority.has(a) ? 1 : 0));
    let done = 0;
    for (const i of order) {
      if (signal.aborted) return;
      let data;
      try { data = await this.loadRegulation(i); } catch (e) { done++; continue; }
      if (signal.aborted) return;
      const hits = [];
      const scan = (a, row, isAnnex) => {
        if (!row || !this._allowed(row, pq)) return;
        if (pq.art && (isAnnex || row.num[0] !== pq.art.num || (row.num[1] || 0) !== pq.art.br)) return;
        const text = a._t || (a._t = isAnnex ? (a.annex_title + "\n" + a.content) : plainText(a));
        const nt = a._nt || (a._nt = norm(text));
        let n = 0;
        for (const t of toks) {
          let c = 0;
          for (const al of t.alts) {
            let p = nt.indexOf(al);
            while (p >= 0 && c < 50) { c++; p = nt.indexOf(al, p + al.length); }
          }
          if (!c) return;
          n += c;
        }
        const score = this._adjust(row, 100 + Math.min(40, n * 4) + (pq.art ? 800 : 0) + (pq.regs ? 100 : 0));
        hits.push({ id: row.id, row, score, via: ["본문"], snippet: "", text });
      };
      for (const a of data.articles) scan(a, this.byId.get(a.id), false);
      if (!pq.art) for (const a of data.annexes) scan(a, this.byId.get(a.id), true);
      done++;
      onHits(hits, { done, total: order.length, regulation: this.regs[i] });
    }
  };

  /* 본문에서 검색어가 있는 자리 주변을 잘라낸다. 반환: {text, ranges:[[s,e]…]} (text 안의 위치) */
  RegDB.snippet = function (text, pq, width) {
    width = width || 90;
    const words = pq.tokens.flatMap(t => t.alts.length ? [t.n, ...t.alts, ...(t.hl || [])] : [t.n, ...(t.hl || [])]);
    const re = highlightRegex(uniq(words));
    if (!re) return { text: nfc(text).slice(0, width * 2).replace(/\s+/g, " "), ranges: [] };
    const t = nfc(text).replace(/[ \t]*\n[ \t]*/g, " ");
    re.lastIndex = 0;
    const m = re.exec(t);
    const start = m ? Math.max(0, m.index - Math.floor(width / 3)) : 0;
    const end = Math.min(t.length, start + width * 2);
    const piece = (start > 0 ? "…" : "") + t.slice(start, end) + (end < t.length ? "…" : "");
    const off = start > 0 ? 1 : 0;
    const ranges = [];
    re.lastIndex = 0;
    let mm;
    while ((mm = re.exec(t.slice(start, end))) && ranges.length < 8) {
      ranges.push([mm.index + off, mm.index + off + mm[0].length]);
      if (mm[0].length === 0) re.lastIndex++;
    }
    return { text: piece, ranges };
  };

  return RegDB;
});
