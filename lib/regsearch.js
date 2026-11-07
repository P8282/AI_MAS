/* 규정 검색 화면 — lib/regsearch.js
   · 개인 패턴을 등록한 사용자에게만 열린다. index.html 이 규정 카드를 눌렀을 때 이 파일을 처음 불러오고, 그때서야 규정/js/regdb.js 와 규정 데이터를 읽는다(그전에는 아무것도 읽지 않음).
   · 데이터·검색은 규정/js/regdb.js(화면과 무관), 이 파일은 조달뽀개기 화면(오버레이)만 맡는다.
   · 사용: PPS_REGSEARCH.open({q:"계약보증금"}) · PPS_REGSEARCH.open({id:"STATE_CONTRACT_ENFORCEMENT_ART_37"}) (문제 ↔ 규정 연결용) · PPS_REGSEARCH.close()
   · 설명 데이터(원문이 아닌 AI·사람의 해설)는 규정/regulations/<규정>/explanations.json 이 있을 때만 '원문과 구분된 상자'로 보여 준다. */
(function () {
  "use strict";
  if (window.PPS_REGSEARCH) return;

  const BASE = new URL("규정/", window.PPS_ROOT || document.baseURI).href;   // 하위 폴더의 문제 화면에서 열 때는 lib/refs.js 가 PPS_ROOT(저장소 맨 위 주소)를 넣어 줌
  const ef = (u, o) => (window.PPS_ENC ? window.PPS_ENC.fetch : fetch)(u, o);   // 암호화된 파일은 lib/enc.js 가 풀어 줌
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const TAG_RE = /(<[^<>]*?\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.?[^<>]*>)/g;
  const TYPE_CLS = { 법률: "law", 시행령: "decree", 대통령령: "decree", 시행규칙: "rule", 부령: "rule", 계약예규: "yegyu", 법령해석례: "interp" };
  const FILTERS = [["law", "법률"], ["decree", "시행령"], ["rule", "시행규칙"], ["yegyu", "계약예규"], ["pps", "행정규칙"], ["interp", "법령해석례"]];
  const EXAMPLES = ["계약보증금", "선금", "입찰참가자격", "수의계약", "다수공급자계약", "납품요구", "지체상금", "계약해제", "부정당제재", ["대가지급 5일", "대가 지급 5일 이내"], ["협상 40일", "협상 제안서 40일"], ["지체상금 0.075", "지체상금 1천분의 0.75"], "제37조", "국가계약법 시행령"];
  const PAGE = 40;

  let fsRefresh = null, db = null, ov = null, pane = null, bodyEl = null, mainEl = null, loadErr = "";
  const WIDE_Q = "(min-width:1000px)";
  const isWide = () => matchMedia(WIDE_Q).matches;      // PC: 왼쪽 검색·목차 + 오른쪽 본문 2단
  const host = () => (isWide() ? mainEl : bodyEl);      // 상세·읽기 화면을 그리는 곳
  let view = "list";                    // list | detail
  const RS_VER = "2026-10-11.2";      // 화면 코드 버전(바꿀 때마다 올림) — 홈 맨 아래에 표시해 옛 코드가 남았는지 확인할 수 있게 함
  let st = { q: "", types: [], reg: null, supp: false };
  let hits = new Map(), shown = PAGE, token = null, depth = 0, pq = null, progress = null;
  let timer = 0, rafId = 0, detailCtx = null, lastList = null;
  let listScroll = 0;
  const openCh = new Set();      // 펼쳐 둔 장(규정 번호|장 제목) — 상세를 보고 돌아와도 유지
  let vstack = [];      // 목록 위에 쌓인 화면(읽기·상세) — 뒤로가기 때 한 단계씩 되돌리기 위함

  const css = `
.rgOv{position:fixed;inset:0;z-index:450;background:rgba(0,0,0,.45);display:flex;align-items:stretch;justify-content:center;font-family:var(--sans,system-ui,sans-serif)}
.rgOv[hidden]{display:none}
.rgPane{position:relative;display:flex;flex-direction:column;width:100%;max-width:760px;background:var(--paper,#fff);color:var(--ink,#231c1c);padding-top:env(safe-area-inset-top,0px)}
@media(min-width:800px){.rgOv{align-items:center;padding:3vh 0}.rgPane{border-radius:18px;max-height:94vh;box-shadow:0 12px 40px rgba(0,0,0,.3);overflow:hidden}}
.rgHead{flex:none;display:flex;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid var(--line,#e6d6d6);background:var(--paper,#fff)}
.rgHead h2{flex:1;margin:0 0 0 6px;font-family:var(--serif,serif);font-size:1.15rem;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rgIcon{flex:none;width:44px;height:44px;border:0;border-radius:50%;background:var(--rose-pale,#fbeaea);color:var(--crimson,#a8323a);font-size:1.25rem;font-weight:700;line-height:1;cursor:pointer;display:grid;place-items:center;padding:0;-webkit-tap-highlight-color:transparent}
.rgFs{flex:none;width:40px;height:38px;border-radius:10px;border:2px solid var(--crimson,#a8323a);background:var(--paper,#fff);color:var(--crimson,#a8323a);font:700 .8rem/1 system-ui,sans-serif;cursor:pointer;padding:0;-webkit-tap-highlight-color:transparent}.rgFs:active{background:var(--crimson,#a8323a);color:#fff}.rgFs:disabled{opacity:.35}.rgFsG{flex:none;display:flex;gap:5px}
@media(min-width:768px){.rgP,.rgPre,.rgTblWrap,.rgArtH{zoom:var(--rgz,1)}}   /* 태블릿·PC: 글자 크기는 조문 본문만 (목차·버튼·목록은 고정) */
.rgCorrTop{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:8px 0 2px}.rgCorrTop:empty{display:none}.rgCorrTop .lb{font-size:.74rem;font-weight:700;color:var(--muted,#6f6464);margin-right:2px}
.rgCx{--cc:var(--crimson,#a8323a);display:inline-flex;align-items:center;gap:6px;max-width:100%;border:1.5px solid var(--cc);background:var(--paper,#fff);color:var(--cc);border-radius:999px;padding:4px 11px 4px 4px;font:inherit;font-size:.82rem;font-weight:600;cursor:pointer;min-height:34px;-webkit-tap-highlight-color:transparent}.rgCx i{font-style:normal;background:var(--cc);color:#fff;border-radius:999px;padding:2px 8px;font-size:.72rem}.rgCx:active{background:var(--cc);color:#fff}.rgCx:active i{background:#fff;color:var(--cc)}
.rgCx.g1{--cc:#a8323a}.rgCx.g2{--cc:#2b5fa8}.rgCx.g3{--cc:#2f7a4f}.rgCx.g4{--cc:#a56a00}.rgCx.g5{--cc:#7a4fa0}.rgCx.g0{--cc:#6f6464}
.rgCorr{margin:14px 0 0;border:1.5px solid var(--line,#e6d6d6);border-radius:12px;background:var(--paper,#fff)}.rgCorr>summary{cursor:pointer;list-style:none;display:flex;align-items:center;gap:8px;min-height:44px;padding:0 14px;font-size:.92rem;font-weight:700}.rgCorr>summary::-webkit-details-marker{display:none}.rgCorr>summary::after{content:"▾";margin-left:auto;color:var(--muted,#6f6464)}.rgCorr[open]>summary::after{content:"▴"}.rgCorr>summary b{background:var(--crimson,#a8323a);color:#fff;border-radius:999px;padding:1px 9px;font-size:.78rem}
.rgCorr .rgCrow{display:flex;align-items:flex-start;gap:8px;width:100%;text-align:left;border:0;border-top:1px solid var(--line,#f0e4e4);background:none;font:inherit;padding:10px 14px;cursor:pointer;color:inherit}.rgCorr .rgCrow:active{background:var(--cream,#f6eeee)}.rgCrow .tx{flex:1;min-width:0;font-size:.9rem;line-height:1.4}.rgCrow .tx b{font-weight:700}.rgCrow .tx small{display:block;color:var(--muted,#6f6464);font-size:.74rem;margin-top:1px}.rgCrow .go{flex:none;color:var(--crimson,#a8323a);align-self:center}.rgCrow .gb{flex:none;margin-top:1px;background:var(--cc);color:#fff;border-radius:6px;padding:1px 7px;font-size:.72rem;font-weight:700;--cc:#6f6464}
.rgCrow .gb.g1{--cc:#a8323a}.rgCrow .gb.g2{--cc:#2b5fa8}.rgCrow .gb.g3{--cc:#2f7a4f}.rgCrow .gb.g4{--cc:#a56a00}.rgCrow .gb.g5{--cc:#7a4fa0}
.rgPps .rgPi{display:block;width:100%;text-align:left;border:0;border-top:1px solid var(--line,#f0e4e4);background:none;font:inherit;padding:10px 14px;cursor:pointer;color:inherit;font-size:.88rem;line-height:1.4}.rgPps .rgPi small{display:block;color:var(--muted,#6f6464);font-size:.74rem}.rgPps .rgPb{padding:4px 14px 12px;font-size:.84rem;line-height:1.55}.rgPps .rgPb h5{margin:8px 0 2px;font-size:.8rem;color:var(--crimson,#a8323a)}.rgPps .rgPb p{margin:2px 0}
.rgCorr .rgCnote{margin:0;padding:8px 14px 10px;border-top:1px solid var(--line,#f0e4e4);font-size:.74rem;color:var(--muted,#6f6464)}
.rgGrpBar{margin:10px 0 0;border:1.5px solid var(--crimson,#a8323a);border-radius:12px;background:var(--paper,#fff)}.rgGrpBar>summary{cursor:pointer;list-style:none;display:flex;align-items:center;gap:8px;min-height:44px;padding:0 14px;font-size:.95rem;font-weight:700;color:var(--ink,#231c1c);-webkit-tap-highlight-color:transparent}.rgGrpBar>summary::-webkit-details-marker{display:none}.rgGrpBar>summary::before{content:"";flex:none;width:18px;height:18px;background:var(--crimson,#a8323a);-webkit-mask:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'><path d='M3 5h18l-7 8v6l-4 2v-8z'/></svg>") center/contain no-repeat;mask:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'><path d='M3 5h18l-7 8v6l-4 2v-8z'/></svg>") center/contain no-repeat}.rgGrpBar>summary b{margin-left:auto;display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;background:var(--crimson,#a8323a);color:#fff;font-size:.82rem}.rgGrpBar>summary b::after{content:"▾";font-size:.8rem}.rgGrpBar[open]>summary b::after{content:"▴"}.rgGrpBar[open]>summary{border-bottom:1px solid var(--line,#e6d6d6)}.rgGrpBar>.rgChips,.rgGrpBar>.rgGrpAct{padding:0 12px}.rgGrpBar>.rgChips{margin:10px 0 0}.rgGrpBar>.rgGrpAct{margin:8px 0 12px}.rgGrpBar .rgChip small{opacity:.6}.rgGrpSave{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:0 12px 12px;padding-top:10px;border-top:1px dashed var(--line,#e6d6d6)}.rgGrpSave .rgBtn{min-height:38px;flex:none}.rgSaved{font-size:.8rem;color:var(--muted,#6f6464)}.rgGrpAct{display:flex;gap:6px}
.rgFLabel{display:inline-flex;align-items:center;min-height:34px;margin-right:2px;font-size:.78rem;font-weight:700;color:var(--muted,#6f6464)}
.rgChips{align-items:center}
.rgChip.rgEx{border-color:transparent;background:var(--cream,#f6eeee);color:var(--crimson,#a8323a);font-size:.82rem}
.rgSecTtl{margin:22px 0 4px;font-size:.78rem;font-weight:700;letter-spacing:.02em;color:var(--muted,#6f6464)}
.rgSecTtl+.rgHint{margin-top:0}
.rgIcon:focus-visible,.rgHit:focus-visible,.rgChip:focus-visible,.rgBtn:focus-visible,.rgReg2:focus-visible{outline:2px solid var(--crimson,#a8323a);outline-offset:2px}
.rgCols{flex:1 1 auto;min-height:0;display:flex;flex-direction:column}.rgMain{display:none}.rgEmpty{display:flex;align-items:center;justify-content:center;height:100%;min-height:200px;text-align:center}.rgBody{flex:1 1 auto;overflow-y:auto;overscroll-behavior:contain;padding:0 14px calc(24px + env(safe-area-inset-bottom,0px));-webkit-overflow-scrolling:touch}
.rgSearch{position:sticky;top:0;z-index:2;margin:0 -14px;padding:10px 14px 8px;background:var(--paper,#fff)}
.rgInWrap{position:relative}
.rgIn{width:100%;box-sizing:border-box;height:48px;border:1.5px solid var(--line,#e6d6d6);border-radius:14px;padding:0 44px 0 14px;font:inherit;font-size:1rem;background:var(--sheet,#fdf6f6);color:inherit}
.rgIn:focus{outline:none;border-color:var(--crimson,#a8323a)}
.rgIn::-webkit-search-cancel-button,.rgIn::-webkit-search-decoration{-webkit-appearance:none;appearance:none;display:none}
.rgClr{position:absolute;right:4px;top:4px;width:40px;height:40px;border:0;background:none;color:var(--muted,#6f6464);font-size:1.1rem;cursor:pointer}
.rgChips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 0}
.rgChip{border:1.5px solid var(--line,#e6d6d6);background:var(--paper,#fff);color:var(--ink,#231c1c);border-radius:999px;padding:6px 12px;font:inherit;font-size:.84rem;cursor:pointer;min-height:34px;-webkit-tap-highlight-color:transparent}
.rgChip[aria-pressed="true"]{background:var(--crimson,#a8323a);border-color:var(--crimson,#a8323a);color:#fff}
.rgChip.ghost{border-style:dashed;color:var(--muted,#6f6464)}
.rgStat{margin:10px 2px 6px;font-size:.82rem;color:var(--muted,#6f6464);min-height:1.2em}
.rgHint{margin:14px 2px 6px;font-size:.88rem;color:var(--muted,#6f6464)}
.rgHit{display:block;width:100%;text-align:left;border:1.5px solid var(--line,#e6d6d6);border-radius:14px;background:var(--paper,#fff);color:inherit;font:inherit;padding:11px 13px;margin:0 0 8px;cursor:pointer;-webkit-tap-highlight-color:transparent}
.rgHit:hover{border-color:color-mix(in srgb,var(--crimson,#a8323a) 42%,var(--line,#e6d6d6))}.rgHit:active{background:var(--rose-pale,#fbeaea)}
.rgHitTop{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:3px}
.rgRegTag{font-size:.74rem;font-weight:600;border-radius:6px;padding:2px 7px;background:var(--rose-pale,#fbeaea);color:var(--crimson,#a8323a);word-break:keep-all}
.rgRegTag.decree{background:#e8eef9;color:#2b4f8a}.rgRegTag.rule{background:#e6f3ec;color:#2c7a57}.rgRegTag.yegyu{background:#fdf0dc;color:#8a5a12}.rgRegTag.pps{background:#efe9f7;color:#5d3d8f}.rgRegTag.interp{background:#e3f3f5;color:#1c6b78}
@media(min-width:1000px){.rgOv{padding:2.5vh 0}.rgPane{max-width:1360px;width:96vw;height:95vh;max-height:95vh}.rgCols{flex-direction:row}.rgCols .rgBody{flex:0 0 400px;width:400px;border-right:1px solid var(--line,#e6d6d6)}.rgMain{display:block;flex:1 1 auto;min-width:0;overflow-y:auto;overscroll-behavior:contain;padding:0 36px 48px}.rgMain .rgDetail{max-width:820px;margin:0 auto}.rgMain .rgToc{display:none}.rgMain mark{background:var(--rose-pale,#fbeaea);color:var(--crimson,#a8323a);font-weight:700;border-radius:3px;padding:0 1px}.rgTocRow.cur,.rgHit.cur{background:var(--rose-pale,#fbeaea);box-shadow:inset 3px 0 0 var(--crimson,#a8323a)}}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]) .rgRegTag.decree{background:#1f2b42;color:#9bbbed}:root:not([data-theme="light"]) .rgRegTag.rule{background:#1e3a2d;color:#6fcf9f}:root:not([data-theme="light"]) .rgRegTag.yegyu{background:#3d2f17;color:#e8b96a}:root:not([data-theme="light"]) .rgRegTag.pps{background:#2e2540;color:#c4a8ee}:root:not([data-theme="light"]) .rgRegTag.interp{background:#173a40;color:#7fd0dc}}
.rgBadge{font-size:.72rem;border-radius:6px;padding:1px 6px;border:1px solid var(--line,#e6d6d6);color:var(--muted,#6f6464)}
.rgBadge.warn{border-color:#c97a00;color:#a45f00}.rgBadge.dim{opacity:.85}
.rgHitTtl{font-size:1rem;line-height:1.4;word-break:keep-all}.rgHitTtl b{font-weight:700;margin-right:4px}
.rgSnip{margin-top:4px;font-size:.88rem;line-height:1.5;color:var(--muted,#6f6464);overflow:hidden;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;word-break:break-all}
.rgMeta{margin-top:5px;font-size:.76rem;color:var(--muted,#6f6464)}
.rgBody mark{background:var(--rose-pale,#fbeaea);color:var(--crimson,#a8323a);font-weight:700;border-radius:3px;padding:0 1px}
.rgMore{display:block;width:100%;margin:6px 0 12px;padding:12px;border:1.5px solid var(--line,#e6d6d6);border-radius:12px;background:var(--sheet,#fdf6f6);color:var(--crimson,#a8323a);font:inherit;font-weight:600;cursor:pointer}
.rgReg2{display:flex;align-items:center;gap:10px;width:100%;text-align:left;border:1.5px solid var(--line,#e6d6d6);border-radius:14px;background:var(--sheet,#fdf6f6);color:inherit;font:inherit;padding:11px 13px;margin:0 0 8px;cursor:pointer}
.rgReg2 b{display:block;font-size:.98rem}.rgReg2 small{display:block;margin-top:2px;color:var(--muted,#6f6464);font-size:.78rem;line-height:1.4}.rgReg2 .go{margin-left:auto;color:var(--crimson,#a8323a);font-weight:700}
.rgGrp{margin:14px 2px 6px;font-size:.8rem;font-weight:700;color:var(--muted,#6f6464);word-break:keep-all}
.rgGrpSub{margin:18px 2px 6px;padding-bottom:4px;border-bottom:1px solid var(--line,#e6dede);font-size:.9rem;color:var(--text,#2b2222)}.rgGrpSub small{font-weight:500;color:var(--muted,#6f6464);margin-left:4px}
.rgDetail{padding-top:12px}
.rgCrumb{font-size:.8rem;color:var(--muted,#6f6464);line-height:1.5;word-break:keep-all}
.rgArtH{margin:6px 0 4px;font-family:var(--serif,serif);font-size:1.35rem;line-height:1.4;word-break:keep-all}.rgArtH .no{color:var(--crimson,#a8323a);margin-right:6px}
.rgOrig{margin:12px 0;border:1.5px solid var(--line,#e6d6d6);border-radius:14px;padding:12px 14px 14px;background:var(--paper,#fff)}
.rgLabel{display:inline-block;margin:0 0 8px;font-size:.72rem;font-weight:700;letter-spacing:.02em;border-radius:6px;padding:2px 8px;background:var(--rose-pale,#fbeaea);color:var(--crimson,#a8323a)}
.rgP{margin:0 0 8px;line-height:1.75;font-size:.98rem;word-break:keep-all;overflow-wrap:anywhere;white-space:pre-line}
.rgP.i1{padding-left:1.1em}.rgP.i2{padding-left:2.2em}
.rgP .mk{font-weight:700;margin-right:.35em}
.rgTag{color:var(--muted,#6f6464);font-size:.8em;opacity:.85}
.rgNote{margin:6px 0 0;font-size:.8rem;color:var(--muted,#6f6464)}
.rgAi{margin:12px 0;border:1.5px dashed #6b7fb8;border-radius:14px;padding:12px 14px;background:color-mix(in srgb,#6b7fb8 8%,var(--paper,#fff))}
.rgAi .rgLabel{background:#e1e7f7;color:#2b3f7a}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]) .rgAi .rgLabel{background:#26304d;color:#aebfee}}
.rgPre{margin:0;overflow-x:auto;font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:.78rem;line-height:1.55;white-space:pre;-webkit-overflow-scrolling:touch}
.rgTblWrap{overflow-x:auto;margin:8px 0 12px;-webkit-overflow-scrolling:touch;border:1px solid var(--line,#e6d6d6);border-radius:8px}
.rgTbl{border-collapse:collapse;min-width:100%;font-size:.8rem;line-height:1.5}
.rgTbl th,.rgTbl td{border:1px solid var(--line,#e6d6d6);padding:5px 7px;vertical-align:top;white-space:pre-line;min-width:4.4em;word-break:keep-all}
.rgTbl th{background:var(--rose-pale,#fbeaea);font-weight:700;text-align:center}
.rgSec{margin:16px 0 0}.rgSec h4{margin:0 0 6px;font-size:.88rem}
.rgKw{display:flex;flex-wrap:wrap;gap:6px}
.rgRel{display:flex;flex-direction:column;gap:6px}
.rgRelBtn{display:flex;gap:8px;align-items:baseline;text-align:left;width:100%;border:1.5px solid var(--line,#e6d6d6);border-radius:10px;background:var(--paper,#fff);color:inherit;font:inherit;font-size:.9rem;padding:8px 11px;cursor:pointer}
.rgRelBtn small{color:var(--muted,#6f6464);flex:none}
.rgSrc{margin:16px 0 0;border-top:1px solid var(--line,#e6d6d6);padding-top:10px;font-size:.8rem;color:var(--muted,#6f6464);line-height:1.6;word-break:break-all}
.rgSrc div{display:flex;gap:8px}.rgSrc dt{flex:none;width:5.2em;font-weight:600}.rgSrc dd{margin:0;min-width:0}
.rgSrc code{font-size:.92em}
.rgUpd{margin:0 0 12px;border:1.5px solid #c97a00;border-radius:12px;background:color-mix(in srgb,#c97a00 7%,var(--paper,#fff))}.rgUpd>summary{cursor:pointer;list-style:none;display:flex;align-items:center;gap:8px;min-height:44px;padding:6px 14px;font-size:.9rem;font-weight:700}.rgUpd>summary::-webkit-details-marker{display:none}.rgUpd>summary::after{content:"▾";margin-left:auto}.rgUpd[open]>summary::after{content:"▴"}.rgUpd .rgUrow{display:block;width:100%;text-align:left;border:0;border-top:1px solid var(--line,#f0e4e4);background:none;font:inherit;padding:9px 14px;cursor:pointer;color:inherit;font-size:.86rem;line-height:1.4}.rgUpd .rgUrow small{display:block;color:var(--muted,#6f6464);font-size:.74rem}.rgUpd .rgUnote{margin:0;padding:8px 14px 10px;border-top:1px solid var(--line,#f0e4e4);font-size:.74rem;color:var(--muted,#6f6464)}
.rgP.rgFocus{background:color-mix(in srgb,#c97a00 14%,transparent);border-radius:6px}.rgWarn{margin:10px 0 0;padding:8px 11px;border-left:4px solid #c97a00;background:color-mix(in srgb,#c97a00 9%,var(--paper,#fff));font-size:.82rem;line-height:1.55;border-radius:0 8px 8px 0}
.rgCh{border-bottom:1px solid var(--line,#e6d6d6)}.rgCh>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:8px;padding:12px 4px;font-weight:700;font-size:.95rem;word-break:keep-all}.rgCh>summary::-webkit-details-marker{display:none}.rgCh>summary::before{content:'▸';color:var(--crimson,#a8323a);transition:transform .15s}.rgCh[open]>summary::before{transform:rotate(90deg)}.rgCh>summary span{flex:1;min-width:0}.rgCh>summary small{color:var(--muted,#6f6464);font-weight:400}.rgTocRow{display:flex;align-items:baseline;gap:8px;width:100%;text-align:left;border:0;background:none;font:inherit;font-size:.9rem;padding:8px 6px 8px 22px;border-top:1px solid var(--line,#f0e4e4);cursor:pointer;color:inherit;min-height:40px}.rgTocRow b{flex:none;color:var(--crimson,#a8323a);font-family:var(--serif,serif)}.rgTocRow span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.rgTocRow:active{background:var(--cream,#f6eeee)}.rgTag{flex:none;font-style:normal;font-size:.7rem;color:var(--muted,#6f6464);border:1px solid var(--line,#e6d6d6);border-radius:6px;padding:0 5px}.rgTag.w{color:#9a6a00;border-color:#d9b44a}.rgChap{margin:18px 0 4px;padding:6px 10px;border-radius:8px;background:var(--cream,#f6eeee);font-size:.82rem;font-weight:700;color:var(--muted,#6f6464);word-break:keep-all}.rgRd{padding:10px 0 12px;border-bottom:1px solid var(--line,#e6d6d6)}.rgRdMore{border:0;background:none;color:var(--crimson,#a8323a);font:inherit;font-size:.82rem;padding:6px 0;cursor:pointer}.rgToc{margin:10px 0;font-size:.9rem}.rgToc summary{cursor:pointer;font-weight:700;padding:6px 0}.rgTocL{display:flex;flex-wrap:wrap;gap:6px;max-height:40vh;overflow:auto;padding:4px 0}.rgNav{display:flex;gap:8px;margin:18px 0 0}
.rgBtn{flex:1;min-height:44px;border:1.5px solid var(--crimson,#a8323a);border-radius:12px;background:transparent;color:var(--crimson,#a8323a);font:inherit;font-size:.9rem;font-weight:600;cursor:pointer;padding:6px 8px}
.rgBtn[disabled]{opacity:.35;cursor:default}
.rgBtn.fill{background:var(--crimson,#a8323a);color:#fff}
.rgToast{position:absolute;left:50%;bottom:calc(20px + env(safe-area-inset-bottom,0px));transform:translateX(-50%);background:#2b2020;color:#fff;border-radius:999px;padding:9px 16px;font-size:.88rem;z-index:5}
.rgErr{margin:16px 0;padding:14px;border-left:4px solid var(--crimson,#a8323a);background:var(--sheet,#fdf6f6);border-radius:0 8px 8px 0;font-size:.9rem;line-height:1.6}
@media(max-width:420px){.rgArtH{font-size:1.2rem}}
`;

  /* ───── 도구 ───── */
  /* 호·목 번호 표기: '1' → '1.', '가)' 처럼 이미 표지가 붙은 것은 그대로 */
  const mkNo = n => (/[.)]$/.test(n) ? n : n + ".");
  const fmtDate = d => d ? d.replace(/^(\d{4})-0?(\d+)-0?(\d+)$/, "$1.$2.$3") : "";
  /* AI_MAS: 법제처 원문 주소 — PPS_cert lib/regpop.js 의 lawUrl 과 같은 규칙.
     법령(법률·시행령·시행규칙 등)은 /법령/<이름>/제N조, 행정규칙은 저장된 법제처 주소(source_url), 없으면 /행정규칙/<이름>, 법령해석례는 링크 없음 */
  const LAW_TYPES = new Set(["법률", "시행령", "시행규칙", "부령", "대통령령", "헌법"]);
  function officialUrl(u) { try { const x = new URL(u); return x.protocol === "https:" && /(^|\.)law\.go\.kr$/.test(x.hostname) ? x.href : ""; } catch (e) { return ""; } }
  function lawUrl(reg, a) {
    if (!reg || reg.document_type === "법령해석례") return "";
    const nm = String(reg.name || "").replace(/^\([^)]*\)\s*/, "").replace(/\s+/g, "");
    if (LAW_TYPES.has(reg.document_type)) {
      if (!nm) return "";
      const art = a && !a.is_supplementary && /^제\d+조(의\d+)?$/.test(a.article_label || "") ? "/" + encodeURIComponent(a.article_label) : "";
      return "https://www.law.go.kr/" + encodeURIComponent("법령") + "/" + encodeURIComponent(nm) + art;
    }
    const su = officialUrl((a && a.source_url) || "") || officialUrl(reg.source_url || "");
    if (su) return su;
    return nm ? "https://www.law.go.kr/" + encodeURIComponent("행정규칙") + "/" + encodeURIComponent(nm) : "";
  }
  const typeClass = t => TYPE_CLS[t] || "pps";
  /* 서버에 새 버전이 있는지 확인(no-cache)하며 불러옴 — 버전 숫자를 올리지 않아도 옛 코드가 남지 않음 */
  function loadScript(src) {
    return fetch(src.replace(/\?.*$/, ""), { cache: "no-cache" }).then(r => { if (!r.ok) throw new Error(src); return r.text(); }).then(code => new Promise((ok, no) => {
      const s = document.createElement("script");
      s.src = URL.createObjectURL(new Blob([code], { type: "text/javascript" }));
      s.onload = () => { URL.revokeObjectURL(s.src); ok(); };
      s.onerror = () => no(new Error(src));
      document.head.appendChild(s);
    }));
  }
  function toast(msg) {
    if (!pane) return;
    const t = document.createElement("div"); t.className = "rgToast"; t.textContent = msg; pane.appendChild(t); setTimeout(() => t.remove(), 1800);
  }
  /* 검색어 강조 + 개정 표시(<개정 …>)는 흐리게. text 는 원문 그대로 받아 안전하게 이스케이프한다 */
  function rich(text, re) {
    return String(text).replace(/[\ue000-\uf8ff]/g, "▫").split(TAG_RE).map((seg, i) => {
      if (i % 2) return `<span class="rgTag">${esc(seg)}</span>`;
      if (!re) return esc(seg);
      re.lastIndex = 0;
      let out = "", last = 0, m;
      while ((m = re.exec(seg))) {
        if (!m[0]) { re.lastIndex++; continue; }
        out += esc(seg.slice(last, m.index)) + "<mark>" + esc(m[0]) + "</mark>";
        last = m.index + m[0].length;
      }
      return out + esc(seg.slice(last));
    }).join("");
  }
  const hlRegex = q => {
    if (!q || !q.tokens.length) return null;
    return RegDB.highlightRegex([...new Set(q.tokens.flatMap(t => [t.n, ...t.alts]))]);
  };

  /* ───── 오버레이 뼈대 ───── */
  function build() {
    if (ov) return;
    const style = document.createElement("style"); style.textContent = css; document.head.appendChild(style);
    ov = document.createElement("div");
    ov.className = "rgOv"; ov.hidden = true; ov.id = "regOv";
    ov.setAttribute("role", "dialog"); ov.setAttribute("aria-modal", "true"); ov.setAttribute("aria-label", "규정 검색");
    ov.innerHTML = `<div class="rgPane"><div class="rgHead"><button type="button" class="rgIcon" id="rgBack" aria-label="뒤로">‹</button><h2 id="rgTitle">규정 검색</h2><div class="rgFsG" role="group" aria-label="글자 크기"><button type="button" class="rgFs" id="rgFsDn" aria-label="글자 작게">가−</button><button type="button" class="rgFs" id="rgFsUp" aria-label="글자 크게">가＋</button></div><button type="button" class="rgIcon" id="rgX" aria-label="닫기">✕</button></div><div class="rgCols"><div class="rgBody" id="rgBody"></div><div class="rgMain" id="rgMain"></div></div></div>`;
    document.body.appendChild(ov);
    pane = ov.querySelector(".rgPane"); bodyEl = ov.querySelector("#rgBody"); mainEl = ov.querySelector("#rgMain");
    ov.addEventListener("mousedown", e => { if (e.target === ov) closeUser(); });
    ov.querySelector("#rgX").onclick = closeUser;
    /* 글자 크기 (절차 쌓기와 같은 16~28, 기본 20, 2씩 · 이 기기에 기억 · 개인 패턴을 만든 학생은 다른 기기와도 맞춤) */
    { const MIN = 16, MAX = 28, DEF = 20, KEY = "pps.regFont", dn = ov.querySelector("#rgFsDn"), up = ov.querySelector("#rgFsUp"); let v = DEF;
      const read = () => { v = DEF; try { const x = parseInt(localStorage.getItem(KEY), 10); if (x >= MIN && x <= MAX) v = x; } catch (e) {} };
      const WQ = matchMedia("(min-width:768px)");   // 태블릿·PC는 본문 글자만(CSS 변수), 폰은 화면 전체
      const apply = () => { const z = v === DEF ? "" : String(v / DEF), wide = WQ.matches;
        ov.style.setProperty("--rgz", wide && z ? z : "1"); bodyEl.style.zoom = mainEl.style.zoom = wide ? "" : z;
        dn.disabled = v <= MIN; up.disabled = v >= MAX; };
      const save = () => { try { localStorage.setItem(KEY, String(v)); localStorage.setItem(KEY + "At", String(Date.now())); } catch (e) {} apply(); if (window.PPS_PLOCK && PPS_PLOCK.pushPrefs) PPS_PLOCK.pushPrefs(); };
      fsRefresh = () => { read(); apply(); };   // 열 때마다 저장된(다른 기기에서 받은) 값으로
      WQ.addEventListener("change", apply);
      dn.onclick = () => { v = Math.max(MIN, v - 2); save(); }; up.onclick = () => { v = Math.min(MAX, v + 2); save(); }; fsRefresh(); }
    ov.querySelector("#rgBack").onclick = back;
    document.addEventListener("keydown", e => { if (ov && !ov.hidden && e.key === "Escape") { e.preventDefault(); back(); } });
    window.addEventListener("popstate", () => {
      if (!ov || ov.hidden) return;
      const n = (history.state && history.state.ppsReg) || 0;
      depth = n;
      if (!n) hide();
      else popTo(n);
    });
    bodyEl.addEventListener("click", onClick);
    mainEl.addEventListener("click", onClick);
    matchMedia(WIDE_Q).addEventListener("change", () => { if (!ov || ov.hidden) return; rd = null; detailCtx = null; vstack = []; view = "list"; showList(); emptyMain(); });
    bodyEl.addEventListener("toggle", e => { const d = e.target; if (d.classList && d.classList.contains("rgCh")) { if (d.open) openCh.add(d.dataset.k); else openCh.delete(d.dataset.k); } }, true);
    bodyEl.addEventListener("input", e => { if (e.target.id === "rgIn") { st.q = e.target.value; schedule(); syncClear(); } });
    bodyEl.addEventListener("keydown", e => { if (e.target.id === "rgIn" && e.key === "Enter") { e.preventDefault(); clearTimeout(timer); runSearch(); e.target.blur(); } });
    bodyEl.addEventListener("compositionend", e => { if (e.target.id === "rgIn") { st.q = e.target.value; schedule(); } });
  }

  function hide() {
    if (!ov) return;
    cancelSearch(); ov.hidden = true; document.documentElement.style.overflow = ""; depth = 0; vstack = [];
  }
  function closeUser() {
    if (history.state && history.state.ppsReg) { const n = history.state.ppsReg; history.go(-n); setTimeout(hide, 30); } else hide();
  }
  function back() {
    if (view === "detail" || view === "read") { if (history.state && history.state.ppsReg >= 2) history.back(); else { vstack = []; showList(); } }
    else if (view === "list" && st.reg != null) { if (vstack.length && history.state && history.state.ppsReg >= 2) history.back(); else { st.reg = null; st.q = ""; showList(); } }
    else closeUser();
  }

  /* ───── 열기 ───── */
  async function open(opts) {
    opts = opts || {};
    if (window.PPS_FEAT && !PPS_FEAT.on("reg")) return false;    // 관리자가 이 코드에서 규정 검색을 숨겼으면 열지 않음 (학생이 학생 설정에서 켤 수 있음)
    build();
    if (fsRefresh) fsRefresh();
    ov.hidden = false; document.documentElement.style.overflow = "hidden";
    vstack = [];
    if (!(history.state && history.state.ppsReg)) history.pushState({ ppsReg: 1 }, "");
    else if (history.state.ppsReg !== 1) history.replaceState({ ppsReg: 1 }, "");      // 새로고침 뒤 남은 깊은 상태를 목록 단계로 정리
    depth = 1;
    if (opts.q != null) st.q = opts.q;
    emptyMain();
    showList(true);
    try {
      if (!window.RegDB) await loadScript(BASE + "js/regdb.js?v=6");
      if (!db) db = new RegDB({ base: BASE });
      await db.init();
      sessOff = null; applyScope();
    } catch (e) {
      loadErr = e && e.code === "PPS_NOKEY" ? e.message : "규정 데이터를 불러오지 못했어요. 인터넷 연결을 확인하고 다시 열어 주세요.";
      bodyEl.innerHTML = `<div class="rgErr">${esc(loadErr)}</div>`;
      return true;
    }
    loadErr = "";
    if (opts.id) await openArticle(opts.id, { pushed: false });
    else if (opts.reg) {      // 홈 배너 '시행 안내' 목록에서 규정명으로 열기: 그 규정의 조문 목록(못 찾으면 이름으로 검색)
      const g = db.regs.find(r => r.short_name === opts.reg || r.name === opts.reg || r.id === opts.reg);
      if (g) { st.reg = g.i; st.q = ""; st.types = []; showList(); pushView({ v: "listreg" }); drawChips(); syncClear(); runSearch(); }
      else { st.q = opts.reg; showList(); runSearch(); }
    }
    else { showList(); if (st.q.trim()) runSearch(); }
    return true;
  }

  /* 분야 필터(규정 검색 위쪽): 꺼 둔 분야는 목록·검색에서 뺀다. 기본은 모든 분야 포함 · 이 기기에 저장(pps.regGrp = 제외한 분야 이름 배열) */
  /* 칩을 누른 선택은 이번에만 적용되고(sessOff), "기본값으로 저장"을 눌러야 다음에도 유지된다(pps.regGrp = 저장된 기본값). 규정 검색을 새로 열면 기본값부터 시작 */
  let sessOff = null;
  const ownGroups = () => { try { const a = JSON.parse(localStorage.getItem("pps.regGrp") || "[]"); return Array.isArray(a) ? a : []; } catch (e) { return []; } };
  /* 관리자(코드 조회의 조달청·지방계약·방사청 스위치)와 학생(분야 필터 '기본값으로 저장') 중 마지막으로 바꾼 쪽이 이 기기의 분야 기본값 */
  const savedGroups = () => {
    try { if (window.Sync && Sync.regGrpEff) return Sync.regGrpEff(); } catch (e) { /* 관리자 값을 못 읽으면 학생 값 */ }
    return ownGroups();
  };
  const offGroups = () => sessOff || savedGroups();
  const sameSet = (a, b) => a.length === b.length && a.every(x => b.includes(x));
  function applyScope() { db.setScope(offGroups()); }
  function drawGroups() {
    const chips = bodyEl.querySelector("#rgChips");
    if (!chips || !db || !db.regs) return;
    let box = bodyEl.querySelector("#rgGrpBar");
    if (!box) { box = document.createElement("details"); box.id = "rgGrpBar"; box.className = "rgGrpBar"; chips.before(box); }
    const names = [...new Set(db.regs.map(r => r.group))], offArr = offGroups(), off = new Set(offArr), on = names.filter(n => !off.has(n)), same = sameSet(offArr, savedGroups().filter(n => names.includes(n)));
    const count = n => db.regs.filter(r => r.group === n).length;
    box.innerHTML = `<summary>분야 필터<b>${on.length === names.length ? "전체" : on.length + "/" + names.length + "개 선택"}</b></summary><div class="rgChips">${names.map(n => `<button type="button" class="rgChip" data-act="grp" data-v="${esc(n)}" aria-pressed="${!off.has(n)}">${esc(n)} <small>${count(n)}</small></button>`).join("")}</div><div class="rgGrpAct"><button type="button" class="rgChip ghost" data-act="grpAll">전체 선택</button><button type="button" class="rgChip ghost" data-act="grpNone">전체 해제</button></div><div class="rgGrpSave">${same ? `<span class="rgSaved">✓ 지금 선택이 기본값이에요</span>` : `<button type="button" class="rgBtn fill" data-act="grpSave">기본값으로 저장</button><button type="button" class="rgChip ghost" data-act="grpReset">저장된 기본값으로</button>`}</div>`;
  }
  function setGroups(off) { sessOff = off; applyScope(); drawGroups(); if (view === "list") { if (st.q.trim() || st.reg != null) runSearchSoon(); else renderHome(); } }

  /* ───── 목록 화면 ───── */
  /* 화면 쌓기: 목록(1) → 읽기·상세(2) → 상세(3) … 뒤로가기는 한 단계씩 되돌린다 */
  function pushView(entry) {
    if (vstack.length) vstack[vstack.length - 1].scroll = bodyEl.scrollTop; else listScroll = bodyEl.scrollTop;
    vstack.push(entry);
    history.pushState({ ppsReg: vstack.length + 1 }, "");
  }
  function popTo(n) {
    if (rd && rd.io) rd.io.disconnect();
    if (n <= 1) {
      vstack = [];
      if (st.reg != null) { st.reg = null; st.q = ""; }          // 규정 조문 목록에서 뒤로 → 규정 목록 첫 화면
      showList();
      const y = listScroll; setTimeout(() => { bodyEl.scrollTop = y; }, 90);
      return;
    }
    while (vstack.length > n - 1) vstack.pop();
    const e = vstack[vstack.length - 1];
    if (!e) { showList(); return; }
    if (e.v === "listreg") { showList(); const y = e.scroll || 0; setTimeout(() => { bodyEl.scrollTop = y; }, 90); }
    else if (e.v === "read") restoreReader(e);
    else openArticle(e.id, { pushed: false, restore: true, scroll: e.scroll });
  }
  function toList(cb) {            // 쌓인 화면을 모두 닫고 목록으로
    const n = vstack.length;
    if (n && history.state && history.state.ppsReg > 1) { history.go(-n); setTimeout(cb, 60); } else { vstack = []; cb(); }
  }

  function showList(early) {
    view = "list";
    ov.querySelector("#rgTitle").textContent = "규정 검색";
    ov.querySelector("#rgBack").style.display = st.reg != null ? "" : "none";
    bodyEl.innerHTML = `<div class="rgSearch"><div class="rgInWrap"><input id="rgIn" class="rgIn" type="search" inputmode="search" enterkeyhint="search" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="규정명 · 제37조 · 계약보증금 …" aria-label="규정 검색어" value="${esc(st.q)}"><button type="button" class="rgClr" id="rgClr" aria-label="지우기" ${st.q ? "" : "hidden"}>✕</button></div><div class="rgChips" id="rgChips"></div></div><div id="rgOut"></div>`;
    drawChips();
    if (early && !db) { bodyEl.querySelector("#rgOut").innerHTML = `<p class="rgHint">규정 데이터를 불러오는 중…</p>`; return; }
    if (lastList && st.q.trim() === lastList.q) { restoreList(); } else renderHome();
    if (!early) { const inp = bodyEl.querySelector("#rgIn"); if (inp && !matchMedia("(pointer:coarse)").matches) inp.focus(); }
  }
  function syncClear() { const b = bodyEl.querySelector("#rgClr"); if (b) b.hidden = !st.q; }

  function drawChips() {
    const el = bodyEl.querySelector("#rgChips");
    if (!el) return;
    if (view === "list") ov.querySelector("#rgBack").style.display = st.reg != null ? "" : "none";
    let h = `<span class="rgFLabel">종류</span>` + FILTERS.map(([k, t]) => `<button type="button" class="rgChip" data-act="type" data-v="${k}" aria-pressed="${st.types.includes(k)}">${t}</button>`).join("");
    h += `<button type="button" class="rgChip ghost" data-act="supp" aria-pressed="${st.supp}">부칙·삭제 포함</button>`;
    if (st.reg != null && db) h += `<button type="button" class="rgChip" data-act="unreg" aria-pressed="true" title="이 규정 안에서만 찾는 중 — 누르면 해제">${esc(db.regs[st.reg].short_name)} ✕</button>`;
    el.innerHTML = h;
    drawGroups();
  }

  /* 개정 알림: 규정/data/update-status.json (규정/tools/check_updates.py 가 법제처 API 로 만든 파일). 없거나 못 읽으면 아무것도 보이지 않음 */
  // 점검 서버의 저장 결과보다 Sites 이관 데이터가 새로울 수 있다.
  // 시행일과 공포번호가 모두 확인된 경우에만 이미 반영된 알림을 제외한다.
  function currentUpdates(updates) {
    const date = v => {
      const s = String(v || "").replace(/[-.\s]/g, "");
      return /^\d{8}$/.test(s) ? s : "";
    };
    const number = v => (String(v || "").match(/\d+/g) || []).map(n => String(Number(n))).join("-");
    const regs = new Map(((db && db.regs) || []).map(r => [r.id, r]));
    return updates.flatMap(u => {
      const reg = regs.get(u.id);
      if (!reg) return [u];
      const stored = date(reg.effective_date), official = date(u.api_effective);
      const storedNo = number(reg.number), officialNo = number(u.api_number);
      if (stored && official && stored >= official && storedNo && storedNo === officialNo) return [];
      return [{ ...u, db_effective: stored ? reg.effective_date : u.db_effective }];
    });
  }
  let upd = null, updP = null;
  function loadUpd() {
    if (upd) return Promise.resolve(upd);
    /* AI_MAS: 개정 알림은 규정/data/update-status.json 만 본다(원래 사이트의 점검 서버는 부르지 않음) */
    if (!updP) updP = ef(BASE + "data/update-status.json?v=" + (db && db.build || ""), { cache: "no-cache" }).then(r => r.ok ? r.json() : null).catch(() => null)
      .then(j => {
        const list = currentUpdates((j && j.updates) || []);
        return (upd = { at: (j && j.checked_at) || "", list, byId: new Map(list.map(u => [u.id, u])) });
      });
    return updP;
  }
  const updTxt = u => `${u.upcoming ? (u.phased ? "다음 시행일 예정" : "개정 시행 예정") : (u.phased ? "다음 시행일 도래(같은 개정의 단계 시행)" : "개정됨")} · 법제처 시행 ${fmtDate(u.api_effective)} / 이 DB ${fmtDate(u.db_effective)}`;
  async function fillUpd() {
    const U = await loadUpd(), box = bodyEl && bodyEl.querySelector("#rgUpdBox");
    if (!db || !U.list.length) return;
    const items = U.list.map(u => ({ u, reg: db.regs.find(r => r.id === u.id) })).filter(x => x.reg && !db.hide.has(x.reg.i));
    if (!items.length) return;
    if (box) box.innerHTML = `<details class="rgUpd"><summary>🔔 법제처 기준 개정된 규정 ${items.length}건 <small style="font-weight:400">· 확인 ${esc(U.at)}</small></summary>${items.map(({ u, reg }) => `<button type="button" class="rgUrow" data-act="reg" data-v="${reg.i}"><b>${esc(reg.short_name)}</b><small>${esc(updTxt(u))}</small></button>`).join("")}<p class="rgUnote">이 DB 에는 아직 반영되지 않았을 수 있어요. 시험·업무에 쓰기 전에 국가법령정보센터에서 최신 내용을 확인하세요.</p></details>`;
    items.forEach(({ reg }) => { const sm = bodyEl.querySelector(`.rgReg2[data-v="${reg.i}"] small`); if (sm && !sm.textContent.includes("개정 알림")) sm.textContent += " · 🔔 개정 알림"; });
  }
  async function fillUpdWarn(reg) {
    const U = await loadUpd(), el = host().querySelector("#rgUpdWarn"), u = U.byId.get(reg.id);
    if (el && u) el.innerHTML = `<div class="rgWarn">🔔 ${esc(updTxt(u))}. 이 화면의 내용은 아직 최신 개정이 반영되지 않았을 수 있어요. 국가법령정보센터에서 확인하세요.</div>`;
  }

  function renderHome() {
    const out = bodyEl.querySelector("#rgOut");
    if (!db) return;
    const exs = EXAMPLES.map(x => { const [label, q] = Array.isArray(x) ? x : [x, x]; return `<button type="button" class="rgChip rgEx" data-act="ex" data-v="${esc(q)}">${esc(label)}</button>`; }).join("");   // [칩에 보이는 말, 실제 검색어] — 숫자 예시는 조문에 적힌 표현(1천분의 0.75 등)으로 찾음
    const groups = {};
    db.regs.filter(r => !db.hide.has(r.i)).forEach(r => (groups[r.group] = groups[r.group] || []).push(r));
    let h = `<div id="rgUpdBox"></div><div class="rgSecTtl">추천 검색어</div><p class="rgHint">규정명, 조 번호(제37조), 제목, 키워드, 본문 내용, 초성(ㄱㅇㅂㅈㄱ)으로 찾을 수 있어요</p><div class="rgChips">${exs}</div>`;
    h += `<div class="rgGrp">규정 목록 (${db.regs.length - db.hide.size}개 · 조문 ${db.rows.filter(r => r.kind === "article" && !db.hide.has(r.r)).length.toLocaleString()}개)</div>`;
    const vis = db.regs.filter(r => !db.hide.has(r.i));      // 규정은 분야(group) 순서·분야 안 서열(법률 → 시행령 → 시행규칙 → 예규·고시·훈령 → 지침·공고)대로 이미 정렬돼 있음
    let curG = null;
    h += vis.map(r => {
      const head = r.group !== curG ? `<div class="rgGrp rgGrpSub">${esc(r.group)} <small>${vis.filter(x => x.group === r.group).length}</small></div>` : "";
      curG = r.group;
      return head + regCard(r);
    }).join("");
    h += `<p class="rgHint">규정 원문은 국가법령정보센터 등에서 받은 PDF 에서 옮겨 온 것이에요. 시험·업무에 쓰기 전에는 반드시 원본으로 한 번 더 확인하세요.</p>`;
    h += `<p class="rgNote">규정 DB ${esc(String(db.generated || "").slice(0, 10))} · 데이터 ${esc(String(db.build || "").slice(0, 8))} · 화면 ${RS_VER}<br><button type="button" class="rgBtn" style="margin-top:6px;min-height:38px;flex:none" data-act="hardrefresh">⟳ 최신으로 새로고침</button></p>`;
    out.innerHTML = h;
    fillUpd();
  }

  function regCard(r) {
    const sub = [r.document_type, `${r.document_type === "법령해석례" ? "회신" : "시행"} ${fmtDate(r.effective_date)}`, `조문 ${r.articles}`, r.annexes ? `별표·별지 ${r.annexes}` : ""].filter(Boolean).join(" · ");
    return `<button type="button" class="rgReg2" data-act="reg" data-v="${r.i}"><span class="rgRegTag ${typeClass(r.document_type)}">${esc(r.document_type)}</span><span style="min-width:0"><b>${esc(r.short_name)}${r.status === "예정" ? " (예정)" : ""}</b><small>${esc(sub)}${r.needs_review ? " · ⚠ 최신본 검토 필요" : ""}</small></span><span class="go">›</span></button>`;
  }

  /* ───── 검색 ───── */
  function schedule() { clearTimeout(timer); timer = setTimeout(runSearch, 230); }
  function cancelSearch() { if (token) token.aborted = true; token = null; cancelAnimationFrame(rafId); }

  function runSearch() {
    if (!db) return;
    cancelSearch();
    const q = st.q.trim();
    const filt = { types: st.types, reg: st.reg, includeSupp: st.supp };
    lastList = null;
    if (!q && st.reg == null) { renderHome(); return; }
    pq = db.parse(q, filt);
    hits = new Map(); shown = PAGE; progress = null;
    for (const h of db.searchIndex(pq)) hits.set(h.id, h);
    const regs = q ? db.searchRegulations(pq) : [];
    const myToken = token = { aborted: false, q };
    const need = pq.tokens.length > 0;
    if (need) {
      const pri = new Set([...hits.values()].map(h => h.row.r));
      progress = { done: 0, total: 1 };
      db.searchBody(pq, { signal: myToken, priority: pri }, (nh, p) => {
        if (myToken.aborted) return;
        for (const h of nh) {
          const old = hits.get(h.id);
          if (old) { old.score = Math.max(old.score, h.score) + (old.via.includes("본문") ? 0 : 20); if (!old.via.includes("본문")) old.via.push("본문"); old.text = h.text; }
          else hits.set(h.id, h);
        }
        progress = p;
        draw(regs, pq);
      }).then(() => { if (!myToken.aborted) { progress = null; draw(regs, pq); } }).catch(() => { progress = null; draw(regs, pq); });
    }
    draw(regs, pq);
  }

  function draw(regs, q) {
    cancelAnimationFrame(rafId);
    rafId = requestAnimationFrame(() => paintResults(regs, q));
  }

  function sorted() { return [...hits.values()].sort((a, b) => b.score - a.score || a.row.order - b.row.order); }

  function paintResults(regs, q) {
    if (view !== "list") return;
    const out = bodyEl.querySelector("#rgOut"); if (!out) return;
    const re = hlRegex(q);
    let h = "";
    const browse = q.regAliasOnly || (!q.tokens.length && !q.art && !q.cho && st.reg != null);
    const list = browse ? sorted().sort((a, b) => a.row.order - b.row.order) : sorted();      // 목차 보기는 원문 순서(조 → 별표)
    const stat = progress ? `본문 검색 중… ${progress.done}/${progress.total} 규정` : "";
    if (regs && regs.length && !(st.reg != null)) h += `<div class="rgGrp">규정</div>` + regs.slice(0, 6).map(regCard).join("");
    if (browse) {
      const ri = q.regAliasOnly && q.regs && q.regs.length === 1 ? q.regs[0] : st.reg;
      if (ri != null && db.regs[ri]) h += `<button type="button" class="rgBtn fill" style="width:100%;margin:8px 0 2px" data-act="read" data-v="${ri}">📖 ${esc(db.regs[ri].short_name)} 처음부터 순서대로 읽기 · 목차</button>`;
      h += `<div class="rgStat">조문 ${list.length}개</div>`;
      /* 장·절 단위로 접어 두고, 한 줄짜리 목록으로 보여 줌 (펼친 장만 길어짐) */
      const groups = []; let cur = null;
      for (const x of list.slice(0, 2500)) {
        const key = x.row.kind === "annex" ? "별표·별지·별첨" : (cleanC(x.row.chapter || "") || "(앞부분)");
        if (!cur || cur.key !== key) { cur = { key, items: [] }; groups.push(cur); }
        cur.items.push(x);
      }
      const rowHtml = x => {
        const r = x.row;
        const tag = r.future ? `<em class="rgTag w">예정</em>` : r.deleted ? `<em class="rgTag">삭제</em>` : r.supp ? `<em class="rgTag">부칙</em>` : "";
        return `<button type="button" class="rgTocRow" data-act="open" data-v="${esc(r.id)}"><b>${esc(r.label)}</b><span>${esc(r.title || "")}</span>${tag}</button>`;
      };
      h += groups.map((g, gi) => `<details class="rgCh" data-k="${esc(st.reg + "|" + g.key)}"${groups.length === 1 || openCh.has(st.reg + "|" + g.key) ? " open" : ""}><summary><span>${esc(g.key)}</span><small>${g.items.length}</small></summary>${g.items.map(rowHtml).join("")}</details>`).join("");
    } else {
      h += `<div class="rgStat" aria-live="polite">${list.length ? `조문·별표 ${list.length}건` : ""}${stat ? (list.length ? " · " : "") + stat : ""}${!list.length && !stat ? "" : ""}</div>`;
      h += list.slice(0, shown).map(x => hitCard(x, q, re)).join("");
      if (list.length > shown) h += `<button type="button" class="rgMore" data-act="more">더 보기 (${list.length - shown}건 남음)</button>`;
      if (!list.length && !stat) h += `<p class="rgHint">찾은 조문이 없어요. 띄어쓰기를 줄이거나, 더 짧은 낱말(예: <b>보증금</b>)로 찾아 보세요. 필터가 켜져 있으면 풀어 보세요.</p>`;
    }
    out.innerHTML = h;
    lastList = { q: st.q.trim() };
  }

  function restoreList() { runSearch(); }

  function hitCard(x, q, re) {
    const row = x.row, reg = db.regOf(row);
    let snip = "";
    const text = x.text || db.textOf(row);
    if (text && q.tokens.length) { const s = RegDB.snippet(text, q); snip = rich(s.text, hlRegexOf(q)); }
    else if (row.chapter) snip = esc(row.chapter.replace(/\s*<[^>]*>/g, ""));
    const badges = [];
    if (row.future) badges.push(`<span class="rgBadge warn">시행 예정 ${esc(fmtDate(row.future))}</span>`);
    if (row.deleted) badges.push(`<span class="rgBadge dim">삭제</span>`);
    if (row.supp) badges.push(`<span class="rgBadge dim">부칙</span>`);
    if (row.kind === "annex") badges.push(`<span class="rgBadge">${esc(row.annexKind)}</span>`);
    const rel = (row.related || []).map(i => db.regs[i] && db.regs[i].short_name).filter(Boolean);
    const meta = [`${reg.document_type === "법령해석례" ? "회신" : "시행"} ${fmtDate(reg.effective_date)}`, rel.length ? `관련 규정: ${rel.slice(0, 3).join(", ")}` : ""].filter(Boolean).join(" · ");
    const titleHtml = rich(row.title || "", re);
    return `<button type="button" class="rgHit" data-act="open" data-v="${esc(row.id)}"><div class="rgHitTop"><span class="rgRegTag ${typeClass(reg.document_type)}">${esc(reg.short_name)}</span>${badges.join("")}</div><div class="rgHitTtl"><b>${esc(row.label)}</b>${titleHtml}</div>${snip ? `<div class="rgSnip">${snip}</div>` : ""}<div class="rgMeta">${esc(meta)}</div></button>`;
  }
  const hlRegexOf = q => hlRegex(q);

  function blockHtml(b, re) {
    if (b.type === "text") return `<p class="rgP">${rich(b.text, re)}</p>`;
    const rows = b.rows || [];
    return `<div class="rgTblWrap" tabindex="0"><table class="rgTbl">${rows.map((r, i) => `<tr>${r.map(c => `<${i ? "td" : "th"}>${rich(c, re)}</${i ? "td" : "th"}>`).join("")}</tr>`).join("")}</table></div>`;
  }

  /* 조문 본문(항·호·목 순서대로) */
  /* 이전 판에서 여는 사유(data/id-map.json tombstones[].why) */
  const ARCH_WHY = { "structure-changed": "공식 API 본문에서는 이 항·호·목이 따로 나뉘지 않거나 구조가 달라요.", "supplement-renumbered": "부칙은 공식 API 기준으로 번호를 다시 매겼어요.",
    "format-changed": "문서 형식(목록형→조문형)이 바뀌었어요.", "scheduled-in-force": "시행 예정본이 시행되어 현행 조문에 반영됐어요.", "not-in-source": "새 원문에 이 단위가 없어요." };
  function paraHtml(a, re) {
    return a.paragraphs.map((p, pi) => {
      let s = `<p class="rgP" id="rgP${pi + 1}" data-u="${esc(p.id)}"><span class="mk">${esc(p.paragraph_number || "")}</span>${rich(p.content, re)}</p>`;
      for (const it of p.items) {
        s += `<p class="rgP i1" data-u="${esc(it.id)}"><span class="mk">${esc(mkNo(it.item_number))}</span>${rich(it.content, re)}</p>`;
        for (const sb of it.subitems) s += `<p class="rgP i2" data-u="${esc(sb.id)}"><span class="mk">${esc(mkNo(sb.subitem_number))}</span>${rich(sb.content, re)}</p>`;
      }
      return s;
    }).join("");
  }

  /* ───── 상세 화면 ───── */
  async function openArticle(id, o) {
    o = o || {};
    if (!db) return;
    const wide = isWide();
    if (!wide) {
      cancelSearch();
      view = "detail";
      ov.querySelector("#rgBack").style.display = "";
      if (o.pushed !== false) pushView({ v: "detail", id });
    }
    host().innerHTML = `<p class="rgHint">불러오는 중…</p>`;
    if (!wide && o.pushed === false && vstack.length && vstack[vstack.length - 1].v === "detail") vstack[vstack.length - 1].id = id;
    let d;
    try { d = await db.getArticle(id); } catch (e) { d = null; }
    if (!d) { host().innerHTML = `<div class="rgErr">이 조문을 불러오지 못했어요.</div>`; return; }
    detailCtx = d;
    let expl = null;
    if (d.reg.explanations && !d.archived) { try { expl = await (await ef(`${BASE}regulations/${encodeURI(d.reg.slug)}/explanations.json?v=${db.build}`)).json(); } catch (e) { expl = null; } }
    paintDetail(d, expl);
    if (o.scroll) host().scrollTop = o.scroll;
    else if (d.focus && !d.focusMissing) {      // 항·호·목 ID 로 연 경우 그 자리로
      const el = [...host().querySelectorAll("[data-u]")].find(x => x.dataset.u === d.focus);
      if (el) { el.classList.add("rgFocus"); el.scrollIntoView({ block: "center" }); }
    }
  }


  /* ───── 대응 조문: 국가계약법 ↔ 지방계약법 ↔ 방위사업법, 적격심사·이행능력심사 규정끼리 같은 내용을 다루는 조문 ─────
     표는 규정/data/correspondence.json(규정/tools/build_correspondence.py 가 만듦). 조문 상세에서 위쪽 바로가기 칩 + 접힌 목록으로 보여 준다. */
  let corr = null, corrP = null;
  const CORR_G = { "국가계약": ["g1", "국가"], "지방계약": ["g2", "지방"], "방위사업": ["g3", "방위"], "조달사업·조달청": ["g4", "조달청"], "중소기업": ["g5", "중기"] };
  const CORR_ORDER = ["국가계약", "지방계약", "방위사업", "조달사업·조달청", "중소기업"];
  const CORR_TERMS = [["지체상금", "지연배상금"]];
  function loadCorr() {
    if (corr) return Promise.resolve(corr);
    if (!corrP) corrP = ef(BASE + "data/correspondence.json", { cache: "no-cache" }).then(r => r.ok ? r.json() : null).then(j => (corr = (j && j.links) || {})).catch(() => (corr = {}));
    return corrP;
  }
  function corrTerm(a, b) { for (const [x, y] of CORR_TERMS) { if ((a.includes(x) && b.includes(y)) || (a.includes(y) && b.includes(x))) return `용어가 달라요: ${x} ↔ ${y}`; } return ""; }
  async function fillCorr(a) {
    const links = await loadCorr();
    const top = host().querySelector("#rgCorrTop"), box = host().querySelector("#rgCorrBox");
    if (!db || !(top || box) || !links[a.id]) return;
    const items = (links[a.id] || []).map(id => db.byId.get(id)).filter(r => r && !db.hide.has(r.r)).map(r => ({ row: r, reg: db.regs[r.r] }));
    if (!items.length) return;
    items.sort((x, y) => (CORR_ORDER.indexOf(x.reg.group) + 9) % 9 - (CORR_ORDER.indexOf(y.reg.group) + 9) % 9 || x.row.r - y.row.r || x.row.order - y.row.order);
    const gc = g => CORR_G[g] || ["g0", g];
    const MAXC = 6;
    const chips = items.slice(0, MAXC).map(({ row, reg }) => { const [c, nm] = gc(reg.group); return `<button type="button" class="rgCx ${c}" data-act="open" data-v="${esc(row.id)}" title="${esc(reg.short_name + " " + row.label + " " + row.title)}"><i>${esc(nm)}</i>${esc(reg.short_name + " " + row.label)}</button>`; }).join("");
    if (top) top.innerHTML = `<span class="lb">대응 조문</span>${chips}${items.length > MAXC ? `<button type="button" class="rgCx g0" data-act="corrmore"><i>+${items.length - MAXC}</i>더 보기</button>` : ""}`;
    const myTitle = a.article_title || "";
    if (box) box.innerHTML = `<details class="rgCorr" id="rgCorr"><summary>대응 조문 <b>${items.length}건</b></summary>${items.map(({ row, reg }) => { const [c, nm] = gc(reg.group); const note = corrTerm(myTitle, row.title || ""); return `<button type="button" class="rgCrow" data-act="open" data-v="${esc(row.id)}"><span class="gb ${c}">${esc(nm)}</span><span class="tx"><b>${esc(reg.short_name)} ${esc(row.label)}</b> ${esc(row.title || "")}${note ? `<small>※ ${esc(note)}</small>` : ""}</span><span class="go">›</span></button>`; }).join("")}<p class="rgCnote">같은 내용을 다루는 다른 규정의 조문을 이어 둔 학습용 안내예요. 내용이 완전히 같지 않을 수 있으니 원문으로 확인하세요.</p></details>`;
  }

  /* 관련 조달청 해석(법령해석/조달청): 조문 ID → 안건. 목록만 먼저, 본문은 누를 때 연도 파일에서 */
  const PPS_BASE_LAW = new URL("법령해석/", window.PPS_ROOT || document.baseURI).href;
  let pps = null, ppsP = null; const ppsYear = {};
  function loadPps() {
    if (pps) return Promise.resolve(pps);
    if (!ppsP) ppsP = Promise.all(["links", "index"].map(n => ef(PPS_BASE_LAW + "조달청/" + n + ".json?v=" + (db && db.build || ""), { cache: "no-cache" }).then(r => r.ok ? r.json() : null).catch(() => null)))
      .then(([l, i]) => (pps = { links: (l && l.links) || {}, items: new Map(((i && i.items) || []).map(x => [x.id, x])), note: (i && i.disclaimer) || "" }));
    return ppsP;
  }
  async function fillPps(a) {
    const P = await loadPps(), box = host().querySelector("#rgPpsBox");
    const ids = P.links[a.id];
    if (!box || !ids || !ids.length) return;
    box.innerHTML = `<details class="rgCorr rgPps"><summary>관련 조달청 해석 <b>${ids.length}건</b></summary>${ids.map(id => { const it = P.items.get(id); return it ? `<button type="button" class="rgPi" data-act="pps" data-v="${esc(id)}"><b>${esc(it.t)}</b><small>${esc(it.d)} · 조달청 법령해석 ${esc(id)}</small></button><div class="rgPb" hidden></div>` : ""; }).join("")}<p class="rgCnote">${esc(P.note || "조달청이 민원 질의에 회신한 내용으로 법적 효력이 없어요.")}</p></details>`;
  }
  async function togglePps(btn, id) {
    const body = btn.nextElementSibling;
    if (!body) return;
    if (!body.hidden) { body.hidden = true; return; }
    if (!body.dataset.done) {
      const it = pps && pps.items.get(id), y = (it && it.d || "0000").slice(0, 4);
      if (!ppsYear[y]) ppsYear[y] = ef(PPS_BASE_LAW + "조달청/by-year/" + y + ".json?v=" + (db && db.build || ""), { cache: "no-cache" }).then(r => r.ok ? r.json() : {}).catch(() => ({}));
      const t = (await ppsYear[y])[id];
      body.innerHTML = t ? `<h5>질의요지</h5>${(t.q || []).map(x => `<p>${esc(x)}</p>`).join("")}<h5>회답</h5>${(t.a || []).map(x => `<p>${esc(x)}</p>`).join("")}` : `<p>본문을 불러오지 못했어요.</p>`;
      if (t) body.dataset.done = "1";
    }
    body.hidden = false;
  }

  function paintDetail(d, expl) {
    const a = d.article, reg = d.reg, isAnnex = !!a.annex_kind;
    const q = pq && pq.tokens && pq.tokens.length ? pq : null;
    const re = hlRegex(q);
    ov.querySelector("#rgTitle").textContent = reg.short_name;
    const crumbParts = [reg.name, !isAnnex ? [a.part, a.chapter, a.section, a.subsection].filter(Boolean).join(" › ").replace(/\s*<[^>]*>/g, "") : ""].filter(Boolean);
    const label = isAnnex ? `[${a.annex_kind}${a.annex_number ? " " + a.annex_number : ""}]` : a.article_label;
    const title = isAnnex ? a.annex_title : a.article_title;
    const badges = [];
    if (a.is_scheduled) badges.push(`<span class="rgBadge warn">시행 예정${a.scheduled_effective_date ? " " + esc(fmtDate(a.scheduled_effective_date)) : ""} — 현행 조문과 함께 원문에 실린 개정본</span>`);
    if (a.is_deleted) badges.push(`<span class="rgBadge dim">삭제된 조문</span>`);
    if (a.is_supplementary) badges.push(`<span class="rgBadge dim">부칙</span>`);
    if (reg.status === "예정") badges.push(`<span class="rgBadge warn">시행 예정 규정 (시행 ${esc(fmtDate(reg.effective_date))})</span>`);
    if (reg.needs_review) badges.push(`<span class="rgBadge warn">최신본 검토 필요</span>`);
    if (d.archived) badges.push(`<span class="rgBadge warn">이전 판 (${esc(d.archived.version)}) — 현행 본문에 없음</span>`);
    let h = `<div class="rgDetail"><div class="rgCrumb">${esc(crumbParts.join(" › "))}</div><h3 class="rgArtH"><span class="no">${esc(label)}</span>${esc(title || "")}</h3>${badges.length ? `<div class="rgHitTop">${badges.join("")}</div>` : ""}<div id="rgUpdWarn"></div>`;

    if (d.archived) h += `<div class="rgWarn">ℹ 이 ID(<code>${esc(d.archived.from)}</code>)는 현행 규정 본문에는 없어 보관 중인 이전 판에서 보여 줘요. ${esc(ARCH_WHY[d.archived.why] || "")} 현재 효력은 현행 본문으로 확인하세요.${d.archived.current ? ` <button type="button" data-act="open" data-v="${esc(d.archived.current)}" style="border:0;background:none;padding:0;font:inherit;cursor:pointer;color:var(--crimson,#a8323a);font-weight:600;text-decoration:underline">현행 조문 보기 ›</button>` : ` <button type="button" data-act="reg" data-v="${reg.i}" style="border:0;background:none;padding:0;font:inherit;cursor:pointer;color:var(--crimson,#a8323a);font-weight:600;text-decoration:underline">현행 규정 목록 ›</button>`}</div>`;
    if (d.focus && d.focusMissing) h += `<div class="rgWarn">ℹ 요청한 항·호·목(<code>${esc(d.focus)}</code>)은 현행 본문에서 따로 나뉘어 있지 않아 조문 전체를 보여 줘요.</div>`;
    if (a.source_note) h += `<div class="rgWarn">ℹ ${esc(a.source_note)}</div>`;
    if (!isAnnex && !d.archived) h += `<div class="rgCorrTop" id="rgCorrTop"></div>`;
    /* 원문 */
    h += `<section class="rgOrig" aria-label="원문"><span class="rgLabel">원문</span>`;
    if (isAnnex) {
      if (a.is_deleted) h += `<p class="rgP">삭제</p>`;
      else {
        h += (a.content_blocks && a.content_blocks.length ? a.content_blocks.map(b => blockHtml(b, re)).join("") : `<pre class="rgPre">${rich(a.content, re)}</pre>`);
        h += `<p class="rgNote">PDF 의 표·글을 자동으로 옮긴 것이라 칸 나눔·띄어쓰기·수식 기호가 원본과 다를 수 있어요. 정확한 내용은 원본 PDF 로 확인하세요.</p>`;
      }
    } else {
      h += paraHtml(a, re);
      if (a.notes && a.notes.length) h += `<p class="rgNote">${a.notes.map(esc).join(" · ")}</p>`;
    }
    h += `</section>`;

    /* 설명 데이터 (있을 때만, 원문과 구분) */
    const ex = expl && expl[a.id];
    if (ex) {
      const items = Array.isArray(ex) ? ex : [ex];
      h += items.map(e => `<section class="rgAi" aria-label="설명"><span class="rgLabel">${esc(e.author || "AI")} 설명 · 규정 원문 아님</span><p class="rgP">${esc(e.text || "")}</p>${e.date ? `<p class="rgNote">작성 ${esc(e.date)}${e.reviewed ? " · 검토됨" : " · 검토 전"}</p>` : ""}</section>`).join("");
    }

    if (!isAnnex && !d.archived) h += `<div id="rgCorrBox"></div><div id="rgPpsBox"></div>`;
    /* 키워드 */
    if (a.keywords && a.keywords.length) h += `<div class="rgSec"><h4>관련 키워드</h4><div class="rgKw">${a.keywords.map(k => `<button type="button" class="rgChip" data-act="ex" data-v="${esc(k)}">${esc(k)}</button>`).join("")}</div></div>`;

    /* 관련 조문·규정 */
    const rel = (a.related_articles || []).map(id => db.byId.get(id)).filter(Boolean);
    if (rel.length) {
      h += `<div class="rgSec"><h4>관련 조문 <small style="font-weight:400;color:var(--muted)">(본문에서 인용한 조문 · 자동 추출)</small></h4><div class="rgRel">${rel.slice(0, 40).map(r => `<button type="button" class="rgRelBtn" data-act="open" data-v="${esc(r.id)}"><b>${esc(db.regOf(r).short_name)} ${esc(r.label)}</b> <span style="min-width:0">${esc(r.title)}</span></button>`).join("")}</div></div>`;
    }
    if (a.annex_kind && (a.related_articles || []).length === 0 && a.related_articles) { /* 관련 조문 없음 */ }
    const rr = (a.related_regulations || []).map(id => db.regs.find(r => r.id === id)).filter(Boolean);
    if (rr.length) h += `<div class="rgSec"><h4>관련 규정</h4><div class="rgKw">${rr.map(r => `<button type="button" class="rgChip" data-act="reg" data-v="${r.i}">${esc(r.short_name)}</button>`).join("")}</div></div>`;
    if (a.external_references && a.external_references.length) h += `<div class="rgSec"><h4>그 밖에 인용한 법령 <small style="font-weight:400;color:var(--muted)">(이 DB에 없음)</small></h4><p class="rgNote">${a.external_references.map(esc).join(" · ")}</p></div>`;

    /* 출처 */
    const isOp = reg.document_type === "법령해석례";
    h += `<dl class="rgSrc"><div><dt>${isOp ? "회신일" : "시행일"}</dt><dd>${esc(fmtDate(a.effective_date || reg.effective_date))}</dd></div>`;
    h += isOp ? `<div><dt>해석례 번호</dt><dd>${esc(reg.number || "")}</dd></div>` : `<div><dt>공포·개정</dt><dd>${esc(fmtDate(reg.revision_date))}${reg.number ? " · " + esc(reg.number) : ""}</dd></div>`;
    if (a.last_amended) h += `<div><dt>조문 최종 개정</dt><dd>${esc(fmtDate(a.last_amended))}</dd></div>`;
    if (!a.pdf_path) h += `<div><dt>출처</dt><dd>${esc(reg.name)} (${esc(reg.document_type)}) — 법제처 국가법령정보 공동활용 API<br><code>${esc(a.source_path)}</code>${reg.source_url ? `<br><a href="${esc(reg.source_url)}" target="_blank" rel="noopener" style="color:var(--crimson,#a8323a);font-weight:600">법제처 국가법령정보센터에서 보기 ↗</a>` : ""}</dd></div>`;
    else { const lu = lawUrl(reg, a); h += `<div><dt>출처</dt><dd>${esc(reg.name)} (${esc(reg.document_type)})<br><code>${esc(a.source_path)}</code> · ${esc(a.source_page)}쪽${lu ? `<br><a href="${esc(lu)}" target="_blank" rel="noopener" style="color:var(--crimson,#a8323a);font-weight:600">법제처 국가법령정보센터에서 보기 ↗</a>` : ""}</dd></div>`; }   // AI_MAS: 원문 PDF 를 넣지 않으므로 PDF 링크 대신 법제처 링크
    h += `<div><dt>ID</dt><dd><code>${esc(a.id)}</code><br><code>${esc(a.display_id)}</code></dd></div></dl>`;
    if (!reg.needs_review && (reg.review_reasons || []).length) h += `<div class="rgWarn">ℹ ${esc(reg.review_reasons.join(" / "))}</div>`;
    if (reg.needs_review) h += `<div class="rgWarn">⚠ 이 규정은 최신본 여부를 확인해야 해요. ${esc((reg.review_reasons || []).join(" / "))}</div>`;

    if (!isAnnex && d.row) h += `<button type="button" class="rgBtn" style="width:100%;margin-top:14px" data-act="read" data-v="${d.row.r}" data-s="${esc(a.id)}">📖 이 조문부터 이어서 읽기</button>`;
    h += `<div class="rgNav"><button type="button" class="rgBtn" data-act="prev" ${d.prev ? "" : "disabled"}>‹ 이전 ${d.prev ? esc(d.prev.article_label || ("[" + d.prev.annex_kind + (d.prev.annex_number ? " " + d.prev.annex_number : "") + "]")) : ""}</button><button type="button" class="rgBtn fill" data-act="copy">복사</button><button type="button" class="rgBtn" data-act="next" ${d.next ? "" : "disabled"}>${d.next ? esc(d.next.article_label || ("[" + d.next.annex_kind + (d.next.annex_number ? " " + d.next.annex_number : "") + "]")) : ""} 다음 ›</button></div></div>`;
    host().innerHTML = h;
    host().scrollTop = 0;
    if (!isAnnex && !d.archived) { fillCorr(a); fillPps(a); }
    fillUpdWarn(reg);
    markCurrent(a.id);
    if (pq && pq.para) { const el = host().querySelector("#rgP" + pq.para); if (el) { el.scrollIntoView({ block: "center" }); } }
  }


  /* ───── 순서대로 읽기 (목차 · 조→항→호→목 순서로 이어 보기) ───── */
  const READ_CHUNK = 12;
  let rd = null;
  function cleanC(t) { return (t || "").replace(/\s*<[^>]*>/g, ""); }
  async function openReader(regIdx, startId, o) {
    o = o || {};
    if (!db) return;
    const wide = isWide();
    const ent = { v: "read", rd: null };
    if (!wide) {
      cancelSearch();
      view = "read";
      ov.querySelector("#rgBack").style.display = "";
      pushView(ent);
    } else if (st.reg !== regIdx || st.q) {            // 왼쪽을 이 규정의 목차로 맞춤
      st.reg = regIdx; st.q = ""; st.types = [];
      const inp = bodyEl.querySelector("#rgIn"); if (inp) inp.value = "";
      drawChips(); syncClear(); runSearch();
    }
    host().innerHTML = `<p class="rgHint">불러오는 중…</p>`;
    let data;
    try { data = await db.loadRegulation(regIdx); } catch (e) { data = null; }
    if (!data) { host().innerHTML = `<div class="rgErr">규정을 불러오지 못했어요.</div>`; return; }
    const list = data.articles.filter(a => !a.is_deleted || st.supp).filter(a => !a.is_scheduled || st.supp);
    const k = startId ? list.findIndex(a => a.id === startId) : 0;
    rd = { data, list, from: Math.max(0, k), to: Math.max(0, k), reg: data.reg };
    ent.rd = rd;
    ov.querySelector("#rgTitle").textContent = data.reg.short_name + " · 순서대로";
    paintReader(true);
  }

  function restoreReader(e) {
    view = "read"; rd = e.rd;
    ov.querySelector("#rgBack").style.display = "";
    ov.querySelector("#rgTitle").textContent = rd.reg.short_name + " · 순서대로";
    rd.to = Math.max(rd.from, rd.to - READ_CHUNK);       // paintReader 가 한 덩어리를 더 붙이므로 그만큼 빼서 같은 위치까지만 다시 그림
    paintReader(true);
    if (e.scroll) host().scrollTop = e.scroll;
  }

  function readArticleHtml(a, prev) {
    let h = "";
    const ch = [a.part, a.chapter, a.section, a.subsection].map(cleanC).filter(Boolean).join(" › ");
    const pch = prev ? [prev.part, prev.chapter, prev.section, prev.subsection].map(cleanC).filter(Boolean).join(" › ") : null;
    if (ch && ch !== pch) h += `<div class="rgChap">${esc(ch)}</div>`;
    const badges = [];
    if (a.is_scheduled) badges.push(`<span class="rgBadge warn">시행 예정</span>`);
    if (a.is_deleted) badges.push(`<span class="rgBadge dim">삭제</span>`);
    if (a.is_supplementary) badges.push(`<span class="rgBadge dim">부칙</span>`);
    h += `<article class="rgRd" id="rd-${esc(a.id)}"><h3 class="rgArtH"><span class="no">${esc(a.article_label)}</span>${esc(a.article_title || "")}${badges.join("")}</h3>`;
    h += a.is_deleted && !a.paragraphs.length ? `<p class="rgP">삭제</p>` : paraHtml(a, null);
    if (a.notes && a.notes.length) h += `<p class="rgNote">${a.notes.map(esc).join(" · ")}</p>`;
    h += `<button type="button" class="rgRdMore" data-act="open" data-v="${esc(a.id)}">이 조문 상세·관련 조문 ›</button></article>`;
    return h;
  }

  function paintReader(reset) {
    if (!rd || (!isWide() && view !== "read")) return;
    const { data, list } = rd;
    const end = Math.min(list.length, rd.to + READ_CHUNK);
    if (reset) {
      /* 목차: 장·절 단위로 건너뛰기 */
      const toc = []; let last = "";
      list.forEach(a => {
        const c = [a.part, a.chapter].map(cleanC).filter(Boolean).join(" › ") || (a.is_supplementary ? "부칙" : "");
        if (c !== last) { toc.push([c, a.id]); last = c; }
      });
      let h = `<div class="rgDetail"><div class="rgCrumb">${esc(data.reg.name)}</div>`;
      h += `<div class="rgNote">조 → 항 → 호 → 목 순서대로 이어 읽는 화면이에요. 원문은 국가법령정보센터 등의 PDF 에서 옮긴 것이니 중요한 내용은 원본으로 확인하세요.</div>`;
      if (toc.length > 1) h += `<details class="rgToc"><summary>목차 (${toc.length})</summary><div class="rgTocL">${toc.map(([c, id]) => `<button type="button" class="rgChip" data-act="rdjump" data-v="${esc(id)}">${esc(c || "(앞부분)")}</button>`).join("")}</div></details>`;
      else h += "";
      h += `<div id="rgRdBody"></div><div id="rgRdFoot"></div></div>`;
      host().innerHTML = h;
      rd.drawn = rd.from;
    }
    const box = host().querySelector("#rgRdBody");
    if (!box) return;                                  // 읽기 화면이 아니면(상세를 열었으면) 이어 붙이지 않음
    let html = "";
    for (let i = rd.drawn; i < end; i++) html += readArticleHtml(list[i], i > rd.from ? list[i - 1] : null);
    box.insertAdjacentHTML("beforeend", html);
    rd.drawn = end; rd.to = end;
    const foot = host().querySelector("#rgRdFoot");
    if (end < list.length) foot.innerHTML = `<button type="button" class="rgBtn fill" data-act="rdmore">다음 조문 더 보기 (${list.length - end}개 남음)</button>`;
    else {
      const an = data.annexes.filter(a => !a.is_deleted);
      foot.innerHTML = `<p class="rgHint">— 끝 —</p>` + (an.length ? `<div class="rgGrp">별표·별지·별첨 (${an.length})</div>` + an.map(a => `<button type="button" class="rgHit" data-act="open" data-v="${esc(a.id)}"><div class="rgHitTtl"><b>[${esc(a.annex_kind)}${a.annex_number ? " " + esc(a.annex_number) : ""}]</b> ${esc(a.annex_title || "")}</div></button>`).join("") : "");
    }
    if (reset) host().scrollTop = 0;
    if (!rd.io && "IntersectionObserver" in window) { /* 맨 아래 근처에서 자동으로 이어 붙임 */ }
    if ("IntersectionObserver" in window) {
      if (rd.io) rd.io.disconnect();
      const btn = foot.querySelector("[data-act=rdmore]");
      if (isWide()) {
        if (rd.io2) rd.io2.disconnect();
        rd.io2 = new IntersectionObserver(es => { for (const e of es) if (e.isIntersecting) markCurrent(e.target.id.slice(3)); }, { root: host(), rootMargin: "0px 0px -75% 0px" });
        host().querySelectorAll(".rgRd").forEach(el => rd.io2.observe(el));
      }
      if (btn) { rd.io = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) { rd.io.disconnect(); paintReader(false); } }, { root: host(), rootMargin: "400px" }); rd.io.observe(btn); }
    }
  }

  function readJump(id) {
    if (!rd) return;
    const k = rd.list.findIndex(a => a.id === id);
    if (k < 0) return false;
    const el = host().querySelector("#rd-" + (window.CSS && CSS.escape ? CSS.escape(id) : id));
    if (el) { el.scrollIntoView({ block: "start" }); return true; }
    rd.from = rd.to = k;
    paintReader(true);
    return true;
  }

  /* PC: 지금 보는 조문을 왼쪽 목차에서 강조하고 그 장을 펼침 */
  function markCurrent(id) {
    if (!isWide() || !bodyEl) return;
    bodyEl.querySelectorAll(".cur").forEach(x => x.classList.remove("cur"));
    const row = bodyEl.querySelector(`[data-act="open"][data-v="${id}"]`);
    if (!row) return;
    row.classList.add("cur");
    const det = row.closest("details");
    if (det && !det.open) det.open = true;
    row.scrollIntoView({ block: "nearest" });
  }
  function emptyMain() { if (isWide() && mainEl) mainEl.innerHTML = `<div class="rgEmpty"><p class="rgHint">왼쪽에서 규정이나 조문을 고르면<br>여기에 본문이 열려요.</p></div>`; }


  /* 저장된 파일(캐시·서비스 워커)을 모두 비우고 주소에 새 값을 붙여 다시 불러옴 */
  async function hardRefresh() {
    toast("최신 버전으로 새로 불러오는 중…");
    try { if (navigator.serviceWorker) for (const r of await navigator.serviceWorker.getRegistrations()) await r.unregister(); } catch (e) {}
    try { if (window.caches) for (const k of await caches.keys()) await caches.delete(k); } catch (e) {}
    const u = new URL(location.href); u.searchParams.set("r", Date.now().toString(36)); u.hash = "";
    location.replace(u.toString());
  }

  function copyText() {
    if (!detailCtx) return;
    const a = detailCtx.article, reg = detailCtx.reg;
    const body = a.annex_kind ? a.content : RegDB.plainText(a);
    const txt = `${reg.name}\n${a.annex_kind ? `[${a.annex_kind} ${a.annex_number}] ${a.annex_title}` : `${a.article_label}${a.article_title ? "(" + a.article_title + ")" : ""}`}\n\n${body}\n\n출처: ${reg.name} ${fmtDate(reg.effective_date)} 시행 (${a.source_file}${a.source_page ? ", " + a.source_page + "쪽" : ""})`;
    const done = () => toast("복사했어요");
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(txt).then(done, () => fallbackCopy(txt, done));
    else fallbackCopy(txt, done);
  }
  function fallbackCopy(t, done) {
    const ta = document.createElement("textarea"); ta.value = t; ta.style.position = "fixed"; ta.style.opacity = "0"; document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); done(); } catch (e) { toast("복사하지 못했어요"); } ta.remove();
  }

  /* ───── 클릭 처리 ───── */
  function onClick(e) {
    const b = e.target.closest("[data-act]");
    if (!b) { if (e.target.id === "rgClr") { st.q = ""; runSearchSoon(); } return; }
    const act = b.dataset.act, v = b.dataset.v;
    if (act === "open") { if (isWide() && rd && b.classList.contains("rgTocRow") && mainEl.querySelector("#rgRdBody") && readJump(v)) markCurrent(v); else openArticle(v); }
    else if (act === "type") { st.types = st.types.includes(v) ? st.types.filter(x => x !== v) : [...st.types, v]; drawChips(); runSearchSoon(); }
    else if (act === "pps") togglePps(b, v);
    else if (act === "corrmore") { const dt = host().querySelector("#rgCorr"); if (dt) { dt.open = true; dt.scrollIntoView({ block: "center", behavior: "smooth" }); } }
    else if (act === "grp") { const off = offGroups(); setGroups(off.includes(v) ? off.filter(x => x !== v) : [...off, v]); }
    else if (act === "grpSave") { try { localStorage.setItem("pps.regGrp", JSON.stringify(offGroups())); localStorage.setItem("pps.regGrpAt", String(Date.now())); } catch (e) {} sessOff = null; drawGroups(); if (window.PPS_PLOCK && PPS_PLOCK.pushPrefs) PPS_PLOCK.pushPrefs(); toast("기본값으로 저장했어요. 다음에 열 때도 이 분야로 시작해요"); }
    else if (act === "grpReset") setGroups(savedGroups());
    else if (act === "grpAll") setGroups([]);
    else if (act === "grpNone") setGroups([...new Set(db.regs.map(r => r.group))]);
    else if (act === "supp") { st.supp = !st.supp; drawChips(); runSearchSoon(); }
    else if (act === "unreg") { if (vstack.length === 1 && vstack[0].v === "listreg" && history.state && history.state.ppsReg === 2) history.back(); else { st.reg = null; drawChips(); runSearchSoon(); } }
    else if (act === "ex") {
      st.q = v; st.reg = null;
      if (view !== "list") { toList(() => { showList(); runSearch(); }); }
      else { const inp = bodyEl.querySelector("#rgIn"); if (inp) inp.value = v; drawChips(); syncClear(); runSearch(); }
    }
    else if (act === "reg") {
      const ri = +v;
      if (view !== "list") { toList(() => { st.reg = ri; st.q = ""; st.types = []; showList(); drawChips(); runSearch(); if (!vstack.length) pushView({ v: "listreg" }); }); }
      else {
        st.reg = ri; st.q = ""; st.types = [];
        const inp = bodyEl.querySelector("#rgIn"); if (inp) inp.value = "";
        if (!vstack.length) pushView({ v: "listreg" });        // 규정 목록 → 이 규정의 조문 목록 (뒤로가기로 규정 목록에 돌아옴)
        drawChips(); syncClear(); runSearch();
      }
    }
    else if (act === "more") { shown += PAGE; paintResults(db.searchRegulations(pq), pq); }
    else if (act === "prev" && detailCtx && detailCtx.prev) openArticle(detailCtx.prev.id, { pushed: false });
    else if (act === "next" && detailCtx && detailCtx.next) openArticle(detailCtx.next.id, { pushed: false });
    else if (act === "copy") copyText();
    else if (act === "hardrefresh") hardRefresh();
    else if (act === "read") openReader(+v, b.dataset.s || null);
    else if (act === "rdmore") paintReader(false);
    else if (act === "rdjump") readJump(v);
  }
  function runSearchSoon() { if (view !== "list") return; const inp = bodyEl.querySelector("#rgIn"); if (inp) inp.value = st.q; syncClear(); runSearch(); }

  window.PPS_REGSEARCH = { open, close: closeUser, hide, isOpen: () => !!(ov && !ov.hidden), openArticle: id => open({ id }) };
})();
