import argparse, hashlib, json, os, re, time
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
STATE = ROOT / "state"; OUTPUT = ROOT / "output"; CONFIG = ROOT / "config"
STATE.mkdir(exist_ok=True); OUTPUT.mkdir(exist_ok=True)


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def key_for(name, address=""):
    return hashlib.sha256((name.strip().lower()+"|"+address.strip().lower()).encode()).hexdigest()[:20]


def google_search(text_query, api_key, max_results=20):
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {"Content-Type":"application/json", "X-Goog-Api-Key":api_key,
               "X-Goog-FieldMask":"places.id,places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.websiteUri,places.googleMapsUri,places.primaryType,places.types"}
    r = requests.post(url, headers=headers, json={"textQuery":text_query, "pageSize":min(max_results,20)}, timeout=30)
    r.raise_for_status()
    return r.json().get("places", [])


def discover():
    api = os.getenv("GOOGLE_PLACES_API_KEY")
    if not api or api == "put_your_key_here":
        raise SystemExit("Set GOOGLE_PLACES_API_KEY in .env first.")
    cities = load_json(CONFIG/"cities.json", [])
    cats = load_json(CONFIG/"categories.json", [])
    db = load_json(STATE/"businesses.json", {})
    settings = load_json(CONFIG/"settings.json", {})["discovery"]
    for city in cities:
        for cat in cats:
            q = f"{cat} in {city['city']}, {city['state']}"
            print(f"[DISCOVER] {q}")
            try:
                places = google_search(q, api, settings.get("max_results_per_query",20))
            except Exception as e:
                print(f"  ERROR {e}"); continue
            for p in places:
                name = p.get("displayName",{}).get("text", "").strip()
                address = p.get("formattedAddress", "")
                if not name: continue
                bid = p.get("id") or key_for(name,address)
                old = db.get(bid, {})
                db[bid] = {**old,
                    "id":bid,"name":name,"address":address,
                    "phone":p.get("nationalPhoneNumber"),"website":p.get("websiteUri"),
                    "maps_url":p.get("googleMapsUri"),"primary_type":p.get("primaryType"),
                    "types":p.get("types",[]),"city":city["city"],"state":city["state"],
                    "discovered_category":cat,"city_priority":city.get("priority",2),
                    "last_discovered":time.strftime("%Y-%m-%dT%H:%M:%SZ")}
            time.sleep(settings.get("request_delay_seconds",0.15))
    save_json(STATE/"businesses.json", db)
    print(f"Saved {len(db)} unique businesses.")


def clean_url(url):
    if not url: return None
    if not url.startswith(("http://","https://")): url="https://"+url
    return url.rstrip("/")


def analyze_site(url, settings):
    url=clean_url(url)
    if not url: return {"status":"NO_WEBSITE"}
    headers={"User-Agent":settings.get("user_agent","LocalLeadDiscoveryAgent/1.0")}
    timeout=settings.get("timeout_seconds",12)
    try:
        r=requests.get(url,headers=headers,timeout=timeout,allow_redirects=True)
        html=r.text[:500000]
        soup=BeautifulSoup(html,"html.parser")
        title=(soup.title.get_text(" ",strip=True) if soup.title else "")[:300]
        meta=soup.find("meta",attrs={"name":re.compile("description",re.I)})
        desc=(meta.get("content","") if meta else "")[:500]
        text=" ".join(soup.stripped_strings)
        links=[urljoin(r.url,a.get("href")) for a in soup.find_all("a",href=True)]
        internal=[x for x in links if urlparse(x).netloc==urlparse(r.url).netloc]
        body=text[:settings.get("max_body_chars",12000)]
        lower=(html+" "+text).lower()
        year_match=re.findall(r"(?:copyright|©)\s*(?:19|20)\d{2}", lower)
        return {"status":"OK","final_url":r.url,"status_code":r.status_code,
                "https":r.url.startswith("https://"),"title":title,"description":desc,
                "word_count":len(text.split()),"internal_link_count":len(set(internal)),
                "has_contact_form":bool(soup.find("form")),
                "has_phone":bool(re.search(r"(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}",text)),
                "has_email":bool(re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}",text,re.I)),
                "has_booking_terms":any(x in lower for x in ["book now","schedule online","appointment","booking"]),
                "copyright_mentions":year_match[-3:],"sample_text":body}
    except Exception as e:
        return {"status":"ERROR","error":str(e)[:500]}


def ollama(prompt):
    base=os.getenv("OLLAMA_URL","http://127.0.0.1:11434").rstrip("/")
    model=os.getenv("OLLAMA_MODEL","qwen3:8b")
    r=requests.post(base+"/api/chat",json={"model":model,"stream":False,"format":"json",
        "options":{"temperature":0.15},"messages":[{"role":"system","content":"Return ONLY valid JSON. Be conservative. Do not invent business facts."},{"role":"user","content":prompt}]},timeout=240)
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"])


def score_business(b):
    prompt=f'''Evaluate this local business as a potential website-development lead. Score 0-100 based on website opportunity, apparent business viability, contactability, and likelihood a website rebuild could plausibly provide value. Do not assume revenue. A bad website is not enough by itself.

BUSINESS
Name: {b.get('name')}
City: {b.get('city')}, {b.get('state')}
Category: {b.get('discovered_category')}
Phone: {b.get('phone')}
Website: {b.get('website')}
Address: {b.get('address')}

WEBSITE SIGNALS
{json.dumps(b.get('website_analysis',{}),ensure_ascii=False)}

Return JSON exactly with keys: score, priority, recommendation, website_problems, sales_angle, risks, confidence. priority must be HIGH, MEDIUM, LOW, or REJECT. recommendation must be CONTACT, REVIEW, or REJECT. Keep each array short.'''
    return ollama(prompt)


def analyze():
    db=load_json(STATE/"businesses.json",{})
    settings=load_json(CONFIG/"settings.json",{})
    website_settings=settings["website"]
    scoring=settings["scoring"]
    review=[]
    for i,(bid,b) in enumerate(db.items(),1):
        print(f"[ANALYZE {i}/{len(db)}] {b['name']}")
        if b.get("website_analysis") is None or "website_analysis" not in b:
            b["website_analysis"]=analyze_site(b.get("website"),website_settings)
        if b.get("website_analysis",{}).get("status") == "ERROR":
            continue
        if "lead_score" not in b:
            try: b["lead_score"]=score_business(b)
            except Exception as e:
                b["score_error"]=str(e)[:500]; continue
        score=float(b.get("lead_score",{}).get("score",0) or 0)
        if score>=scoring.get("min_score_for_review",65) and b.get("lead_score",{}).get("recommendation") != "REJECT":
            review.append(b)
        save_json(STATE/"businesses.json",db)
    review.sort(key=lambda x:float(x.get("lead_score",{}).get("score",0) or 0),reverse=True)
    save_json(OUTPUT/"new_leads.json",review)
    save_json(OUTPUT/"all_analyzed_businesses.json",db)
    print(f"Review queue: {len(review)} leads")


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--discover",action="store_true")
    ap.add_argument("--analyze",action="store_true")
    ap.add_argument("--all",action="store_true")
    args=ap.parse_args()
    if not any(vars(args).values()): ap.error("Use --discover, --analyze, or --all")
    if args.discover or args.all: discover()
    if args.analyze or args.all: analyze()

if __name__=="__main__": main()
