import json,re,sys,os,collections
sys.path.insert(0,os.path.join(os.path.dirname(os.path.abspath(__file__)),'..'))
import pymupdf
from refs import ns
R=os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','..')+'/'
dom=json.load(open(os.environ.get('DOM_OUT','/tmp/regdb_dom.json')))
regs=json.load(open(R+'data/regulations.json'))['regulations']
NOTE=re.compile(r"\[(?:본조신설|전문개정|제목개정|본조삭제|조문이동|전부개정|시행일:[^\]]*)[^\]]*\](?:제\d+조[^\[]{0,12})?")
bad=collections.defaultdict(list); n_art=n_ann=0; none=[]
mk=lambda n: n if n[-1:] in '.)' else n+'.'
for reg in regs:
    reg['pdf_path']='pdf/'+reg['source_path'][len('원문/규정/'):]
    pdf=pymupdf.open(R+reg['pdf_path'])
    pages=[ns(pg.get_text()) for pg in pdf]
    full=re.sub(r'법제처\d+국가법령정보센터','',''.join(pages).replace(ns(reg['name']),''))
    fullnn=NOTE.sub('',full)
    for a in json.load(open(R+reg['files']['articles'])):
        d=dom[a['id']]
        if d is None: none.append(a['id']); continue
        n_art+=1
        exp=''
        for p in a['paragraphs']:
            exp+=ns(p['paragraph_number']+p['content'])
            for it in p['items']:
                exp+=ns(mk(it['item_number'])+it['content'])
                for sb in it['subitems']: exp+=ns(mk(sb['subitem_number'])+sb['content'])
        got=ns(re.sub('▫','',''.join(d['blocks'])))      # 화면은 글꼴이 없는 특수 글자(U+E000대)를 ▫ 로 바꿔 보여 준다
        exp=re.sub('[\ue000-\uf8ff▫]','',exp)
        if got!=exp: bad['DOM≠JSON'].append((a['id'],got[:40],exp[:40]))
        head=ns(d['head']); lab=ns(a['article_label']+a['article_title'])
        if head!=lab: bad['머리≠JSON'].append((a['id'],head,lab))
        if ns(''.join(d['notes']).replace('·',''))!=ns(''.join(a['notes']).replace('·','')) and a['notes']: bad['notes'].append(a['id'])
        # DOM → PDF (현행 본문, 부칙 제외)
        if not a['is_supplementary'] and reg.get('format')!='outline':
            t=ns(a['article_label']+('('+a['article_title']+')' if a['article_title'] else ''))+got
            t2=ns(a['article_label']+('['+a['article_title']+']' if a['article_title'] else ''))+got
            if not any(x in full or x in fullnn for x in (t,t2,t[:-4],t2[:-4])):
                bad['DOM→PDF 연속불일치'].append(a['id'])
    for a in json.load(open(R+reg['files']['annexes'])):
        d=dom[a['id']]
        if d is None: none.append(a['id']); continue
        n_ann+=1
        if a['is_deleted']: continue
        # DOM 글자 vs PDF 글자 (PUA 는 ▫ 로 표시되므로 제외)
        txt=''
        for b in d['blocks']:
            txt+= b[6:].replace('\u0001','').replace('\u0002','') if b.startswith('TABLE:') else b
        got=ns(re.sub(r'[▫]','',txt))
        pdoc=pymupdf.open(R+a['pdf_path']); pr=[int(x) for x in a['source_page'].split('-')]
        pgs=[pdoc[i-1] for i in range(pr[0],pr[-1]+1)] if a['pdf_path']==reg['pdf_path'] else list(pdoc)      # 본문 PDF 안의 부속은 해당 쪽만
        src=ns(re.sub('[\ue000-\uf8ff]','',''.join(pg.get_text() for pg in pgs)))
        r=len(got)/max(1,len(src))
        if abs(r-1)>0.08: bad['부속 글자수'].append((a['id'],round(r,3)))
        # 표 칸 수 vs JSON
        nt=sum(1 for b in d['blocks'] if b.startswith('TABLE:'))
        jt=sum(1 for b in a['content_blocks'] if b['type']=='table')
        if nt!=jt: bad['부속 표 수'].append((a['id'],nt,jt))
print('조문',n_art,'부속',n_ann,'렌더 실패',none[:5])
for k,v in bad.items(): print(k,len(v),v[:5])
print('끝')
