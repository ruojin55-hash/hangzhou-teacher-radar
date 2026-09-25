#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import deque
from datetime import datetime, date
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
JOBS_PATH = ROOT / "jobs.json"
META_PATH = ROOT / "meta.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; HangzhouTeacherRadar/1.0; +personal job-search monitor)"
}
TIMEOUT = 18
MAX_PAGES_PER_SOURCE = 80
MAX_DEPTH = 2
SLEEP = 0.15

def clean_url(u: str) -> str:
    u = urldefrag(u)[0].strip()
    return u

def same_domain(a: str, b: str) -> bool:
    da = urlparse(a).netloc.lower().split(":")[0]
    db = urlparse(b).netloc.lower().split(":")[0]
    return da == db or da.endswith("." + db) or db.endswith("." + da)

def fetch(session, url):
    try:
        r = session.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return None, None
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype and "xml" not in ctype and not url.lower().endswith((".htm",".html",".shtml",".jsp",".action","/")):
            return None, None
        r.encoding = r.apparent_encoding or r.encoding
        return r.url, r.text
    except Exception:
        return None, None

def visible_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script","style","noscript"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True) or title
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    return title[:500], text

def extract_date(text: str):
    pats = [
        r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?",
        r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
    ]
    dates = []
    for pat in pats:
        for y,m,d in re.findall(pat, text[:12000]):
            try:
                dates.append(date(int(y),int(m),int(d)))
            except ValueError:
                pass
    if not dates:
        return ""
    today = date.today()
    plausible = [d for d in dates if d <= today]
    d = max(plausible) if plausible else min(dates)
    return d.isoformat()

def extract_deadline(text: str):
    windows = []
    for m in re.finditer(r"(报名|截止|提交|受理|网上报名)", text):
        windows.append(text[max(0,m.start()-80):min(len(text),m.start()+260)])
    target = " ".join(windows)[:4000]
    matches = re.findall(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", target)
    dates=[]
    for y,m,d in matches:
        try:
            dates.append(date(int(y),int(m),int(d)))
        except ValueError:
            pass
    if not dates:
        return "未自动识别，请查看原公告", ""
    d=max(dates)
    return d.isoformat(), d.isoformat()

def infer_level(text):
    has_mid = "初中" in text
    has_high = "高中" in text or "高级中学" in text
    if has_mid and has_high: return "初中 / 高中"
    if has_high: return "高中"
    if has_mid: return "初中"
    return "中学（待核实）"

def infer_employment(text):
    if "事业编制" in text or "事业编" in text:
        return "事业编"
    if "公办编制" in text:
        return "公办编制"
    if "劳务派遣" in text:
        return "劳务派遣/非编"
    if "非编" in text and "在编" in text:
        return "在编 / 非编均有"
    if "非编" in text:
        return "非编"
    if "事业单位" in text:
        return "事业单位招聘（编制待核实）"
    return "待核实"

def infer_eligibility(text):
    if "2027届" in text:
        return "yes", "公告明确出现“2027届”"
    if any(k in text for k in ["应届毕业生","应届生","优秀毕业生"]):
        return "unknown", "公告面向应届/优秀毕业生，但需确认是否覆盖2027届"
    return "unknown", "公告未自动识别到明确的2027届条件"

def infer_status(deadline_iso):
    if not deadline_iso:
        return "待核实", "watch"
    try:
        d=date.fromisoformat(deadline_iso)
        if d < date.today():
            return "已结束", "closed"
        return "报名中", "open"
    except Exception:
        return "待核实", "watch"

def infer_school(title, text, source_name):
    # Prefer names that contain 中学/学校/教育集团.
    candidates = re.findall(r"[\u4e00-\u9fffA-Za-z0-9·（）()]{2,40}(?:中学|高级中学|学校|教育集团)", (title + " " + text[:1200]))
    if candidates:
        return max(candidates, key=len)[:100]
    return source_name

def infer_position(title, text):
    if "高中英语" in text: return "高中英语教师"
    if "初中英语" in text: return "初中英语教师"
    if "英语教师" in text: return "英语教师"
    return "英语相关教师岗位"

def is_candidate(title, text, rules):
    merged = title + " " + text
    return (
        any(k in merged for k in rules["recruitment_keywords"])
        and any(k in merged for k in rules["must_contain_any"])
        and any(k in merged for k in rules["english_keywords"])
    )

def discover_source(session, src, rules):
    root = src["root"]
    q = deque([(root,0)])
    seen = set()
    results = []
    while q and len(seen) < MAX_PAGES_PER_SOURCE:
        url, depth = q.popleft()
        url = clean_url(url)
        if not url or url in seen:
            continue
        seen.add(url)
        final_url, html = fetch(session, url)
        time.sleep(SLEEP)
        if not html:
            continue
        title, text = visible_text(html)
        if is_candidate(title, text, rules):
            results.append((final_url or url, title, text))
        if depth >= MAX_DEPTH:
            continue
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            href = clean_url(urljoin(final_url or url, a["href"]))
            if not href.startswith(("http://","https://")):
                continue
            if not same_domain(href, root):
                continue
            anchor = re.sub(r"\s+"," ",a.get_text(" ",strip=True))
            blob = anchor + " " + href
            if any(k in blob for k in rules["candidate_link_keywords"]):
                q.append((href, depth+1))
    return results

def make_job(src, url, title, text):
    published = extract_date(title + " " + text)
    deadline_text, deadline_iso = extract_deadline(text)
    status, status_type = infer_status(deadline_iso)
    eligibility, eligibility_text = infer_eligibility(text)
    school = infer_school(title, text, src["name"])
    job_id = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return {
        "id": job_id,
        "school": school,
        "district": src["district"],
        "level": infer_level(text),
        "subject": "英语",
        "position": infer_position(title, text),
        "count": "见原公告",
        "employment": infer_employment(text),
        "eligibility": eligibility,
        "eligibility_text": eligibility_text,
        "status": status,
        "status_type": status_type,
        "published": published or "待核实",
        "deadline": deadline_text,
        "education": "请查看原公告",
        "major": "请查看原公告中的专业目录/岗位表",
        "certificate": "请查看原公告",
        "source_type": "官方来源" if src.get("official") else "其他来源",
        "source_url": url,
        "official_note": f"由自动任务从 {src['name']} 检出；关键资格请以原公告和附件为准。",
        "action": "查看原公告并确认27届资格",
        "verification": "auto",
        "official": bool(src.get("official")),
        "last_checked": date.today().isoformat(),
        "title": title
    }

def main():
    old = []
    if JOBS_PATH.exists():
        old = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    by_url = {j.get("source_url"): j for j in old if j.get("source_url")}

    session = requests.Session()
    found = []
    errors = []
    for src in CFG["sources"]:
        try:
            pages = discover_source(session, src, CFG["rules"])
            for url,title,text in pages:
                found.append(make_job(src,url,title,text))
        except Exception as e:
            errors.append(f"{src['name']}: {type(e).__name__}")

    for j in found:
        old_j = by_url.get(j["source_url"])
        if old_j:
            # Keep manually enriched fields while refreshing status/date/source facts.
            merged = dict(old_j)
            for k in ["status","status_type","published","deadline","eligibility","eligibility_text",
                      "employment","last_checked","official","source_type","title"]:
                if j.get(k):
                    merged[k] = j[k]
            by_url[j["source_url"]] = merged
        else:
            by_url[j["source_url"]] = j

    jobs = list(by_url.values())
    jobs.sort(key=lambda x: (x.get("status_type")=="closed", x.get("published","")), reverse=False)

    JOBS_PATH.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    now = datetime.now().astimezone()
    META_PATH.write_text(json.dumps({
        "updated_at": now.strftime("%Y-%m-%d %H:%M"),
        "timezone": "Asia/Shanghai",
        "mode": "automatic",
        "message": f"本轮自动发现 {len(found)} 条候选页面；请以原公告为准。",
        "job_count": len(jobs),
        "errors": errors[:20]
    }, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
