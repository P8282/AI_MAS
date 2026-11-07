const {chromium}=require('playwright');const fs=require('fs');
(async()=>{
 const b=await chromium.launch({executablePath:'/opt/pw-browsers/chromium'});
 const p=await (await b.newContext({viewport:{width:900,height:900}})).newPage();
 await p.goto('http://127.0.0.1:8765/index.html');
 await p.evaluate(()=>{localStorage.setItem('pps.syncCode',JSON.stringify('A1'));localStorage.setItem('pps.plr',JSON.stringify({c:'A1'}));});
 await p.reload();await p.waitForTimeout(800);
 await p.click('#regLink');await p.waitForSelector('#rgIn');await p.waitForTimeout(600);
 const ids=await p.evaluate(async()=>{const d=new RegDB({base:new URL('규정/',document.baseURI).href});await d.init();return d.rows.map(r=>r.id);});
 const out={};let n=0;
 for(const id of ids){
   const r=await p.evaluate(async id=>{
     await PPS_REGSEARCH.openArticle(id);
     const h=document.querySelector('.rgArtH'), o=document.querySelector('.rgOrig');
     if(!h||!o) return null;
     const blk=[...o.querySelectorAll('.rgP,.rgTbl,.rgPre')].map(e=>e.tagName==='TABLE'?'TABLE:'+[...e.rows].map(r=>[...r.cells].map(c=>c.textContent).join('\u0001')).join('\u0002'):e.textContent);
     return {head:h.textContent, blocks:blk, notes:[...o.querySelectorAll('.rgNote')].map(e=>e.textContent)};
   },id);
   out[id]=r; if(++n%500===0) console.log(n);
 }
 fs.writeFileSync(''+(process.env.DOM_OUT||'/tmp/regdb_dom.json')+'',JSON.stringify(out));
 console.log('done',n);
 await b.close();
})();
