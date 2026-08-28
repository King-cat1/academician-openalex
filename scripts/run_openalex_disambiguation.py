#!/usr/bin/env python3
import csv
import json
import math
import os
import re
import time
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import requests

API = "https://api.openalex.org"
API_KEY = os.environ["OPENALEX_API_KEY"]
INPUT = Path(os.environ.get("INPUT_CSV", "data/academicians.csv"))
OUTDIR = Path(os.environ.get("OUTPUT_DIR", "output"))
OUTDIR.mkdir(parents=True, exist_ok=True)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "academician-openalex-disambiguation/1.0"})

institution_cache = {}
source_cache = {}
author_singleton_cache = {}
work_singleton_cache = {}

DIVISION_KEYWORDS = {
    "数学物理": {"mathematics","physics","astronomy","mechanics","statistics","mathematical"},
    "化学": {"chemistry","chemical","catalysis","organic","inorganic","polymer","materials"},
    "生命科学": {"biology","biological","medicine","medical","genetics","biochemistry","neuroscience","agriculture","ecology"},
    "医学": {"medicine","medical","clinical","surgery","oncology","cardiology","neuroscience","health"},
    "地学": {"earth","geology","geophysics","geography","climate","atmospheric","ocean","environmental","hydrology"},
    "信息技术": {"computer","computing","information","electrical","electronics","communication","signal","semiconductor","artificial intelligence"},
    "技术科学": {"engineering","materials","mechanical","aerospace","energy","automation","manufacturing","civil"},
    "机械与运载": {"mechanical","automotive","vehicle","aerospace","manufacturing","robotics"},
    "信息与电子": {"electrical","electronics","computer","communication","information","signal","semiconductor"},
    "化工冶金与材料": {"chemical","metallurgy","materials","mining","polymer","ceramic"},
    "能源与矿业": {"energy","mining","petroleum","coal","power","geology"},
    "土木水利与建筑": {"civil","hydraulic","architecture","construction","water","transportation"},
    "农业": {"agriculture","crop","plant","soil","forestry","animal","food"},
    "医药卫生": {"medicine","medical","clinical","pharmacy","health","surgery","oncology"},
    "环境与轻纺": {"environmental","textile","light industry","pollution","ecology","food"},
}

COMMON_CN_EN = {
    "清华大学": ["tsinghua university"],
    "北京大学": ["peking university"],
    "复旦大学": ["fudan university"],
    "浙江大学": ["zhejiang university"],
    "南京大学": ["nanjing university"],
    "武汉大学": ["wuhan university"],
    "四川大学": ["sichuan university"],
    "山东大学": ["shandong university"],
    "中山大学": ["sun yat-sen university"],
    "南开大学": ["nankai university"],
    "天津大学": ["tianjin university"],
    "吉林大学": ["jilin university"],
    "厦门大学": ["xiamen university"],
    "同济大学": ["tongji university"],
    "东南大学": ["southeast university"],
    "北京师范大学": ["beijing normal university"],
    "华中科技大学": ["huazhong university of science and technology"],
    "上海交通大学": ["shanghai jiao tong university", "shanghai jiaotong university"],
    "西安交通大学": ["xi'an jiaotong university", "xian jiaotong university"],
    "中国科学技术大学": ["university of science and technology of china"],
    "哈尔滨工业大学": ["harbin institute of technology"],
    "北京航空航天大学": ["beihang university"],
    "北京理工大学": ["beijing institute of technology"],
    "西北工业大学": ["northwestern polytechnical university"],
    "电子科技大学": ["university of electronic science and technology of china"],
    "北京邮电大学": ["beijing university of posts and telecommunications"],
    "中国科学院": ["chinese academy of sciences"],
    "中国工程院": ["chinese academy of engineering"],
    "中国医学科学院": ["chinese academy of medical sciences"],
    "中国农业科学院": ["chinese academy of agricultural sciences"],
    "中国地质科学院": ["chinese academy of geological sciences"],
}

FOREIGN_SOURCE_EXCLUDE_PATTERNS = [
    r"\bscience china\b",
    r"\bchinese physics\b",
    r"\bchinese journal\b",
    r"\bjournal of semiconductors\b",
    r"\btransactions of nonferrous metals society of china\b",
    r"\bacta .* sinica\b",
    r"\bcell research\b",  # China-hosted; exclude under strict foreign-journal criterion
]

def norm(s):
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKC", s).lower().strip()
    s = re.sub(r"https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)
    return s

def norm_words(s):
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKC", s).lower()
    return set(re.findall(r"[a-z0-9]+", s))

def compact_id(url_or_id):
    if not url_or_id:
        return ""
    return str(url_or_id).rstrip("/").split("/")[-1]

def doi_clean(v):
    if not v:
        return ""
    s = unicodedata.normalize("NFKC", str(v)).strip()
    for ch in ["‑","–","—","−"]:
        s = s.replace(ch, "-")
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    return s

def api_get(path, params=None, singleton=False, tries=6):
    params = dict(params or {})
    params["api_key"] = API_KEY
    url = path if path.startswith("http") else API + path
    delay = 1.0
    for attempt in range(tries):
        r = SESSION.get(url, params=params, timeout=45)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            reset = r.headers.get("Retry-After")
            time.sleep(float(reset) if reset else delay)
            delay = min(delay * 2, 30)
            continue
        raise RuntimeError(f"OpenAlex {r.status_code} for {r.url}: {r.text[:500]}")
    raise RuntimeError(f"OpenAlex failed after retries: {url}")

def get_institution(inst_id):
    oid = compact_id(inst_id)
    if not oid:
        return {}
    if oid not in institution_cache:
        institution_cache[oid] = api_get(f"/institutions/{oid}", singleton=True)
    return institution_cache[oid]

def get_source(source_id):
    oid = compact_id(source_id)
    if not oid:
        return {}
    if oid not in source_cache:
        source_cache[oid] = api_get(f"/sources/{oid}", singleton=True)
    return source_cache[oid]

def get_work_by_doi(doi):
    doi = doi_clean(doi)
    if not doi:
        return {}
    if doi not in work_singleton_cache:
        work_singleton_cache[doi] = api_get(f"/works/doi:{doi}", singleton=True)
    return work_singleton_cache[doi]

def get_author(author_id):
    oid = compact_id(author_id)
    if not oid:
        return {}
    if oid not in author_singleton_cache:
        author_singleton_cache[oid] = api_get(f"/authors/{oid}", singleton=True)
    return author_singleton_cache[oid]

def author_names(a):
    vals = [a.get("display_name","")]
    for key in ("display_name_alternatives","alternative_names"):
        x = a.get(key)
        if isinstance(x, list):
            vals.extend(x)
    return [x for x in vals if x]

def name_score(expected, candidate):
    e = norm(expected)
    if not e:
        return 0.0
    best = 0.0
    for n in author_names(candidate):
        c = norm(n)
        if not c:
            continue
        if c == e:
            best = max(best, 4.0)
        else:
            ratio = SequenceMatcher(None, e, c).ratio()
            best = max(best, 3.0 * ratio)
            ew, cw = norm_words(expected), norm_words(n)
            if ew and ew == cw:
                best = max(best, 3.7)
    return best

def institution_aliases(inst_obj):
    vals = [inst_obj.get("display_name","")]
    for key in ("display_name_alternatives","display_name_acronyms"):
        x = inst_obj.get(key)
        if isinstance(x, list):
            vals.extend(x)
    return [v for v in vals if v]

def target_affiliation_aliases(cn):
    aliases = [cn or ""]
    for k, vals in COMMON_CN_EN.items():
        if k in (cn or ""):
            aliases.extend(vals)
    # CAS institute name: preserve core institute tokens for partial evidence
    return aliases

def affiliation_score(target_cn, author_obj):
    if not target_cn:
        return (0.0, "")
    targets = target_affiliation_aliases(target_cn)
    target_norms = [norm(x) for x in targets if x]
    insts = []
    for aff in author_obj.get("affiliations") or []:
        inst = aff.get("institution") or {}
        if inst.get("id"):
            insts.append(inst["id"])
    for inst in author_obj.get("last_known_institutions") or []:
        if inst and inst.get("id"):
            insts.append(inst["id"])
    seen = set()
    best = 0.0
    evidence = ""
    for iid in insts:
        oid = compact_id(iid)
        if oid in seen: 
            continue
        seen.add(oid)
        try:
            inst = get_institution(oid)
        except Exception:
            continue
        names = institution_aliases(inst)
        for t in target_norms:
            for n in names:
                nn = norm(n)
                if not t or not nn:
                    continue
                if t == nn:
                    return (5.0, n)
                if len(t) >= 5 and (t in nn or nn in t):
                    if 4.0 > best:
                        best, evidence = 4.0, n
        # Chinese substring evidence from alternate native names
        tc = norm(target_cn)
        for n in names:
            nn = norm(n)
            if tc and nn and len(tc) >= 4 and (tc in nn or nn in tc):
                if 4.5 > best:
                    best, evidence = 4.5, n
    return (best, evidence)

def topic_score(division, author_obj):
    text = []
    for t in author_obj.get("topics") or []:
        if isinstance(t, dict):
            text.append(str(t.get("display_name","")))
            for k in ("field","domain","subfield"):
                v=t.get(k)
                if isinstance(v,dict):
                    text.append(str(v.get("display_name","")))
    joined = " ".join(text).lower()
    keys = set()
    for k, vals in DIVISION_KEYWORDS.items():
        if k in (division or ""):
            keys |= vals
    if not keys:
        return (0.0, "")
    hits = sorted([x for x in keys if x in joined])
    if hits:
        return (min(2.0, 0.7 + 0.35*len(hits)), ",".join(hits[:6]))
    return (0.0, "")

def score_author(row, author_obj):
    ns = name_score(row.get("english_name_candidate",""), author_obj)
    afs, afev = affiliation_score(row.get("election_affiliation",""), author_obj)
    ts, tev = topic_score(row.get("election_division",""), author_obj)
    score = ns + afs + ts
    return {
        "score": round(score,3),
        "name_score": round(ns,3),
        "affiliation_score": round(afs,3),
        "topic_score": round(ts,3),
        "affiliation_evidence": afev,
        "topic_evidence": tev,
    }

def candidate_authors(row):
    name = (row.get("english_name_candidate") or "").strip()
    if not name:
        return []
    # Keep calls in cheap List+Filter class instead of full-text Search.
    # Deprecated .search filter is still documented as supported in 2026.
    data = api_get("/authors", {
        "filter": f"display_name.search:{name}",
        "per_page": 15,
    })
    return data.get("results") or []

def authors_from_anchor(row):
    doi = doi_clean(row.get("verified_doi"))
    if not doi:
        return []
    try:
        work = get_work_by_doi(doi)
    except Exception:
        return []
    out=[]
    for auth in work.get("authorships") or []:
        a = auth.get("author") or {}
        if not a.get("id"):
            continue
        try:
            full = get_author(a["id"])
        except Exception:
            full = a
        out.append(full)
    return out

def choose_author(row):
    candidates = authors_from_anchor(row)
    method = "verified_doi_anchor" if candidates else "author_name_filter"
    if not candidates:
        candidates = candidate_authors(row)
    scored = []
    for a in candidates:
        s = score_author(row, a)
        scored.append((s["score"], s, a))
    scored.sort(key=lambda x: (x[0], (x[2].get("cited_by_count") or 0)), reverse=True)
    if not scored:
        return {"status":"no_candidate","method":method,"candidates":[]}
    top_score, details, top = scored[0]
    second = scored[1][0] if len(scored)>1 else -99
    margin = top_score-second
    anchored = bool(row.get("verified_doi"))
    if anchored and details["name_score"] >= 2.6 and (details["affiliation_score"] >= 4 or margin >= 1.5):
        status="A"
    elif top_score >= 8.0 and margin >= 1.0:
        status="A"
    elif top_score >= 6.2 and margin >= 0.6:
        status="B"
    else:
        status="review"
    serial=[]
    for sc, det, a in scored[:5]:
        serial.append({
            "author_id": compact_id(a.get("id")),
            "display_name": a.get("display_name"),
            "score": sc,
            **det,
            "works_count": a.get("works_count"),
            "cited_by_count": a.get("cited_by_count"),
        })
    return {
        "status":status,
        "method":method,
        "author_id":compact_id(top.get("id")),
        "author_display_name":top.get("display_name",""),
        "score":top_score,
        "margin":round(margin,3),
        **details,
        "candidates":serial,
    }

def list_foreign_journal_works(author_id):
    data = api_get("/works", {
        "filter": f"author.id:{author_id},type:article,has_doi:true,language:en,is_retracted:false",
        "sort": "cited_by_count:desc",
        "per_page": 100,
        "select": "id,doi,title,display_name,publication_year,type,language,cited_by_count,primary_location,authorships",
    })
    works = []
    for w in data.get("results") or []:
        pl = w.get("primary_location") or {}
        src0 = pl.get("source") or {}
        sid = src0.get("id")
        if not sid:
            continue
        try:
            src = get_source(sid)
        except Exception:
            src = src0
        if (src.get("type") or "").lower() != "journal":
            continue
        country = (src.get("country_code") or "").upper()
        sname = src.get("display_name") or src0.get("display_name") or ""
        if country == "CN":
            continue
        if any(re.search(p, sname, re.I) for p in FOREIGN_SOURCE_EXCLUDE_PATTERNS):
            continue
        if not w.get("doi"):
            continue
        works.append((w, src))
    return works

def choose_work(row, author_id):
    try:
        works = list_foreign_journal_works(author_id)
    except Exception as e:
        return {"work_status":"error","work_error":str(e)}
    if not works:
        return {"work_status":"no_foreign_english_journal_doi"}
    w, src = works[0]
    doi = doi_clean(w.get("doi"))
    return {
        "work_status":"selected",
        "selected_doi":doi,
        "selected_title":w.get("title") or w.get("display_name") or "",
        "selected_journal":src.get("display_name") or "",
        "selected_year":w.get("publication_year") or "",
        "selected_source_country":src.get("country_code") or "",
        "selected_source_id":compact_id(src.get("id")),
        "selected_work_id":compact_id(w.get("id")),
        "selected_cited_by_count":w.get("cited_by_count") or 0,
        "doi_url":f"https://doi.org/{doi}" if doi else "",
    }

def write_csv(path, rows, fields):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w=csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

def main():
    with open(INPUT, encoding="utf-8-sig", newline="") as f:
        rows=list(csv.DictReader(f))
    limit = int(os.environ.get("MAX_ROWS","0") or 0)
    if limit > 0:
        rows=rows[:limit]

    results=[]
    candidates_out=[]
    for i,row in enumerate(rows,1):
        try:
            m=choose_author(row)
            rec={**row}
            rec.update({k:v for k,v in m.items() if k!="candidates"})
            for c in m.get("candidates",[]):
                candidates_out.append({
                    "person_id":row.get("person_id"),
                    "name_cn":row.get("name_cn"),
                    "english_name_candidate":row.get("english_name_candidate"),
                    **c
                })
            if m.get("status") in ("A","B") and m.get("author_id"):
                rec.update(choose_work(row,m["author_id"]))
            else:
                rec["work_status"]="not_selected_due_to_author_uncertainty"
            results.append(rec)
        except Exception as e:
            rec={**row, "status":"error", "error":repr(e)}
            results.append(rec)
        if i % 50 == 0:
            print(f"processed {i}/{len(rows)}", flush=True)

    fields = [
        "person_id","name_cn","english_name_candidate","academy_memberships",
        "first_domestic_academician_year","election_division","election_affiliation",
        "official_homepage_url","verified_doi",
        "status","method","author_id","author_display_name","score","margin",
        "name_score","affiliation_score","topic_score","affiliation_evidence","topic_evidence",
        "work_status","selected_doi","selected_title","selected_journal","selected_year",
        "selected_source_country","selected_source_id","selected_work_id","selected_cited_by_count",
        "doi_url","error","work_error"
    ]
    write_csv(OUTDIR/"academician_openalex_matches.csv", results, fields)
    cfields=["person_id","name_cn","english_name_candidate","author_id","display_name","score",
             "name_score","affiliation_score","topic_score","affiliation_evidence","topic_evidence",
             "works_count","cited_by_count"]
    write_csv(OUTDIR/"author_candidates_top5.csv", candidates_out, cfields)

    review=[r for r in results if r.get("status") not in ("A","B") or r.get("work_status")!="selected"]
    write_csv(OUTDIR/"review_required.csv", review, fields)

    cnt=Counter(r.get("status","") for r in results)
    wcnt=Counter(r.get("work_status","") for r in results)
    summary={
        "total":len(results),
        "author_status_counts":dict(cnt),
        "work_status_counts":dict(wcnt),
        "selected_foreign_english_journal_doi":sum(1 for r in results if r.get("selected_doi")),
        "api_strategy":"cheap author display_name.search filter + author.id works filter; singleton institution/source lookups are free",
    }
    with open(OUTDIR/"summary.json","w",encoding="utf-8") as f:
        json.dump(summary,f,ensure_ascii=False,indent=2)
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__ == "__main__":
    main()
