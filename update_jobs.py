#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,re,time
from collections import deque
from datetime import datetime,date
from pathlib import Path
from urllib.parse import urljoin,urlparse,urldefrag
import requests
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parent
CFG=json.loads((ROOT/'sources.json').read_text(encoding='utf-8'))
JOBS_PATH=ROOT/'jobs.json'; META_PATH=ROOT/'meta.json'
HEADERS={'User-Agent':'Mozilla/5.0 (compatible; HangzhouTeacherRadar/2.0; +personal job-search monitor)'}
TIMEOUT=18; MAX_PAGES_PER_SOURCE=90; MAX_EVENT_PAGES_PER_SOURCE=120; MAX_DEPTH=2; SLEEP=.12

def clean_url(u): return urldefrag(u)[0].strip()
def same_domain(a,b):
    da=urlparse(a).netloc.lower().split(':')[0]; db=urlparse(b).netloc.lower().split(':')[0]
    return da==db or da.endswith('.'+db) or db.endswith('.'+da)
def fetch(session,url):
    try:
        r=session.get(url,headers=HEADERS,timeout=TIMEOUT,allow_redirects=True)
        if r.status_code!=200:return None,None
        ctype=r.headers.get('content-type','')
        if 'html' not in ctype and 'xml' not in ctype and not url.lower().endswith(('.htm','.html','.shtml','.jsp','.action','/','jobfair')): return None,None
        r.encoding=r.apparent_encoding or r.encoding
        return r.url,r.text
    except Exception:return None,None

def visible_text(html):
    soup=BeautifulSoup(html,'lxml')
    for tag in soup(['script','style','noscript']):tag.decompose()
    title=soup.title.get_text(' ',strip=True) if soup.title else ''
    h1=soup.find('h1')
    if h1:title=h1.get_text(' ',strip=True) or title
    return title[:500],re.sub(r'\s+',' ',soup.get_text(' ',strip=True))

def all_dates(text):
    out=[]
    for pat in [r'(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?',r'(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日']:
        for y,m,d in re.findall(pat,text[:16000]):
            try:out.append(date(int(y),int(m),int(d)))
            except ValueError:pass
    return out

def extract_date(text):
    ds=all_dates(text)
    if not ds:return ''
    t=date.today(); past=[d for d in ds if d<=t]
    return (max(past) if past else min(ds)).isoformat()
def extract_deadline(text):
    wins=[]
    for m in re.finditer(r'(报名|截止|提交|受理|网上报名)',text):wins.append(text[max(0,m.start()-80):min(len(text),m.start()+280)])
    ds=all_dates(' '.join(wins)[:5000])
    if not ds:return '未自动识别，请查看原公告',''
    d=max(ds);return d.isoformat(),d.isoformat()
def extract_event_date(text):
    ds=all_dates(text)
    if not ds:return ''
    t=date.today(); future=sorted({d for d in ds if d>=t})
    if future:return future[0].isoformat()
    recent=sorted({d for d in ds if (t-d).days<=60},reverse=True)
    return recent[0].isoformat() if recent else ''
def extract_time(text):
    m=re.search(r'([01]?\d|2[0-3])[:：]([0-5]\d)(?:\s*[-~—至]+\s*([01]?\d|2[0-3])[:：]([0-5]\d))?',text[:10000])
    if not m:return ''
    s=f'{int(m.group(1)):02d}:{m.group(2)}'
    return f'{s}-{int(m.group(3)):02d}:{m.group(4)}' if m.group(3) else s
def extract_location(text):
    for pat in [r'(?:宣讲地点|举办地点|活动地点|地点|地址)[：:\s]+([^。；;\n]{4,80})',r'(?:校区)[^。；;\n]{0,50}(?:报告厅|教室|大厅|中心)[^。；;\n]{0,30}']:
        m=re.search(pat,text[:12000])
        if m:return (m.group(1) if m.groups() else m.group(0)).strip()
    return '请查看原页面'
def infer_level(text):
    a='初中' in text;b='高中' in text or '高级中学' in text
    return '初中 / 高中' if a and b else '高中' if b else '初中' if a else '中学（待核实）'
def infer_employment(text):
    if '事业编制' in text or '事业编' in text:return '事业编'
    if '公办编制' in text:return '公办编制'
    if '劳务派遣' in text:return '劳务派遣/非编'
    if '非编' in text and '在编' in text:return '在编 / 非编均有'
    if '非编' in text:return '非编'
    if '事业单位' in text:return '事业单位招聘（编制待核实）'
    return '待核实'
def infer_eligibility(text):
    if '2027届' in text:return 'yes','页面明确出现“2027届”'
    if any(k in text for k in ['应届毕业生','应届生','优秀毕业生']):return 'unknown','面向应届/优秀毕业生，但需确认是否覆盖2027届'
    return 'unknown','未自动识别到明确的2027届条件'
def infer_status(iso):
    if not iso:return '待核实','watch'
    try:return ('已结束','closed') if date.fromisoformat(iso)<date.today() else ('报名中','open')
    except:return '待核实','watch'
def infer_event_status(iso):
    if not iso:return '持续关注','watch'
    try:
        d=date.fromisoformat(iso)
        if d<date.today():return '已结束','closed'
        return ('即将举行','open') if (d-date.today()).days<=7 else ('待举行','open')
    except:return '持续关注','watch'
def infer_school(title,text,source):
    cs=re.findall(r'[\u4e00-\u9fffA-Za-z0-9·（）()]{2,40}(?:中学|高级中学|学校|教育集团)',title+' '+text[:1600])
    return max(cs,key=len)[:100] if cs else source
def infer_position(title,text):
    return '高中英语教师' if '高中英语' in text else '初中英语教师' if '初中英语' in text else '英语教师' if '英语教师' in text else '英语相关教师岗位'
def infer_event_type(title,text):
    m=title+' '+text[:3000]
    return '双选会' if '双选会' in m else '师资招聘专场' if '师资招聘' in m else '招聘会' if '招聘会' in m else '空中宣讲会' if '空中宣讲' in m else '宣讲会' if '宣讲' in m else '招聘活动'
def is_job_candidate(title,text,r):
    m=title+' '+text
    return any(k in m for k in r['recruitment_keywords']) and any(k in m for k in r['must_contain_any']) and any(k in m for k in r['english_keywords'])
def is_event_candidate(title,text,r):
    if not any(k in title for k in r['event_keywords']):return False
    m=title+' '+text
    return any(k in m for k in r['event_relevance_keywords'])
def discover(session,src,r,event=False):
    root=src['root'];q=deque([(root,0)]);seen=set();out=[];limit=MAX_EVENT_PAGES_PER_SOURCE if event else MAX_PAGES_PER_SOURCE
    keys=r['candidate_event_link_keywords'] if event else r['candidate_link_keywords']
    while q and len(seen)<limit:
        u,d=q.popleft();u=clean_url(u)
        if not u or u in seen:continue
        seen.add(u);fu,html=fetch(session,u);time.sleep(SLEEP)
        if not html:continue
        title,text=visible_text(html)
        ok=is_event_candidate(title,text,r) if event else is_job_candidate(title,text,r)
        if ok:out.append((fu or u,title,text))
        if d>=MAX_DEPTH:continue
        soup=BeautifulSoup(html,'lxml')
        for a in soup.find_all('a',href=True):
            href=clean_url(urljoin(fu or u,a['href']))
            if not href.startswith(('http://','https://')) or not same_domain(href,root):continue
            blob=re.sub(r'\s+',' ',a.get_text(' ',strip=True))+' '+href
            if any(k in blob for k in keys):q.append((href,d+1))
    return out

def make_job(src,url,title,text):
    pub=extract_date(title+' '+text);dtxt,diso=extract_deadline(text);status,stype=infer_status(diso);elig,etxt=infer_eligibility(text);school=infer_school(title,text,src['name'])
    return {'id':hashlib.sha1(('job:'+url).encode()).hexdigest()[:16],'record_type':'job','school':school,'district':src['district'],'level':infer_level(text),'subject':'英语','position':infer_position(title,text),'count':'见原公告','employment':infer_employment(text),'eligibility':elig,'eligibility_text':etxt,'status':status,'status_type':stype,'published':pub or '待核实','deadline':dtxt,'education':'请查看原公告','major':'请查看原公告中的专业目录/岗位表','certificate':'请查看原公告','source_type':'官方来源' if src.get('official') else '其他来源','source_url':url,'official_note':f"由自动任务从 {src['name']} 检出；关键资格请以原公告和附件为准。",'action':'查看原公告并确认27届资格','verification':'auto','official':bool(src.get('official')),'last_checked':date.today().isoformat(),'title':title}
def make_event(src,url,title,text):
    ed=extract_event_date(title+' '+text);tm=extract_time(title+' '+text);status,stype=infer_event_status(ed);elig,etxt=infer_eligibility(text);school=infer_school(title,text,src['name']);typ=infer_event_type(title,text)
    return {'id':hashlib.sha1(('event:'+url).encode()).hexdigest()[:16],'record_type':'event','school':school,'district':'杭州相关 / 校园招聘','level':infer_level(text),'subject':'教师招聘活动','position':title[:180] or f'{school}{typ}','count':'见活动页面','employment':'校园招聘活动；具体编制看招聘单位','eligibility':elig,'eligibility_text':etxt,'status':status,'status_type':stype,'published':extract_date(text) or '待核实','deadline':' '.join(x for x in [ed,tm] if x) or '时间待确认','education':'以活动/招聘单位要求为准','major':'活动可能覆盖多学科，需查看参会单位及岗位表','certificate':'按具体招聘单位要求','source_type':'官方高校就业平台' if src.get('official') else '其他来源','source_url':url,'official_note':f"由自动任务从 {src['name']} 检出。宣讲会/双选会本身不等于正式事业编公告，最终以招聘单位公告为准。",'action':'查看活动详情、报名方式和参会单位','verification':'auto-event','official':bool(src.get('official')),'last_checked':date.today().isoformat(),'title':title,'event_type':typ,'event_date':ed,'event_time':tm,'event_location':extract_location(text),'organizer':school}
def main():
    old=json.loads(JOBS_PATH.read_text(encoding='utf-8')) if JOBS_PATH.exists() else []
    for x in old:x.setdefault('record_type','job')
    by={(x.get('record_type','job'),x.get('source_url'),x.get('position')):x for x in old};session=requests.Session();fj=[];fe=[];errs=[]
    for src in CFG.get('sources',[]):
        try:fj += [make_job(src,*p) for p in discover(session,src,CFG['rules'],False)]
        except Exception as e:errs.append(f"岗位源 {src['name']}: {type(e).__name__}")
    for src in CFG.get('event_sources',[]):
        try:fe += [make_event(src,*p) for p in discover(session,src,CFG['rules'],True)]
        except Exception as e:errs.append(f"活动源 {src['name']}: {type(e).__name__}")
    for j in fj+fe:
        k=(j.get('record_type','job'),j.get('source_url'),j.get('position'));oldj=by.get(k)
        if oldj:
            m=dict(oldj)
            for kk,v in j.items():
                if v not in ('',None,[]):m[kk]=v
            by[k]=m
        else:by[k]=j
    rec=list(by.values());JOBS_PATH.write_text(json.dumps(rec,ensure_ascii=False,indent=2),encoding='utf-8')
    META_PATH.write_text(json.dumps({'updated_at':datetime.now().astimezone().strftime('%Y-%m-%d %H:%M'),'timezone':'Asia/Shanghai','mode':'automatic-v2','message':f'本轮自动发现岗位候选 {len(fj)} 条、宣讲/双选活动候选 {len(fe)} 条；关键资格以原公告为准。','job_count':sum(1 for x in rec if x.get('record_type')!='event'),'event_count':sum(1 for x in rec if x.get('record_type')=='event'),'errors':errs[:30]},ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
