import argparse

import json

import os

import re

import time

from pathlib import Path

from urllib.parse import urljoin, urlparse



import requests

from bs4 import BeautifulSoup

from dotenv import load_dotenv





# ============================================================

# CONFIGURATION

# ============================================================



ROOT = Path(__file__).resolve().parent

load_dotenv(ROOT / ".env")



OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")



REQUEST_TIMEOUT = 15

SLEEP_SECONDS = 0.4



DATA_DIR = ROOT / "data"

OUTPUT_DIR = ROOT / "output"

STATE_DIR = ROOT / "state"



WEBSITE_FILE = OUTPUT_DIR / "website_analysis.json"

QUALIFIED_FILE = OUTPUT_DIR / "qualified_leads.json"

CONTACTS_FILE = OUTPUT_DIR / "contacts.json"

OUTREACH_FILE = OUTPUT_DIR / "outreach_queue.json"

EXCLUDED_FILE = OUTPUT_DIR / "excluded_businesses.json"



DEFAULT_INPUT = DATA_DIR / "businesses.json"



USER_AGENT = (

          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "

          "AppleWebKit/537.36 (KHTML, like Gecko) "

          "Chrome/131.0.0.0 Safari/537.36"

)





# ============================================================

# NATIONAL CHAIN / CORPORATE EXCLUSIONS

# ============================================================



EXCLUDED_BRANDS = [

          "ubreakifix",

          "best buy",

          "2nd & charles",

          "mcdonald's",

          "mcdonalds",

          "walmart",

          "target",

          "costco",

          "sam's club",

          "sams club",

          "home depot",

          "lowe's",

          "lowes",

          "walgreens",

          "cvs",

          "dollar general",

          "dollar tree",

          "kroger",

          "king soopers",

          "safeway",

          "whole foods",

          "trader joe's",

          "trader joes",

          "aldi",

          "best western",

          "marriott",

          "hilton",

          "holiday inn",

          "motel 6",

          "starbucks",

          "dunkin'",

          "dunkin",

          "burger king",

          "wendy's",

          "wendys",

          "taco bell",

          "chick-fil-a",

          "subway",

          "domino's",

          "dominos",

          "pizza hut",

          "papa john's",

          "papa johns",

          "little caesars",

          "chipotle",

          "panda express",

          "five guys",

          "wingstop",

          "jersey mike's",

          "jersey mikes",

          "jimmy john's",

          "jimmy johns",

          "raising cane's",

          "raising canes",

          "napa auto parts",

          "napa automotive",

          "autozone",

          "o'reilly auto parts",

          "oreilly auto parts",

          "advance auto parts",

          "jiffy lube",

          "discount tire",

          "carmax",

          "carvana",

          "enterprise rent-a-car",

          "enterprise rent a car",

          "hertz",

          "avis",

          "u-haul",

          "uhaul",

          "fedex",

          "the ups store",

          "staples",

          "office depot",

          "petsmart",

          "petco",

          "planet fitness",

          "anytime fitness",

]



CORPORATE_INDICATORS = [

          "national headquarters",

          "corporate headquarters",

          "corporate office",

          "regional headquarters",

]





# ============================================================

# FILE HELPERS

# ============================================================



def ensure_directories():

          OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

          STATE_DIR.mkdir(parents=True, exist_ok=True)





def load_json(path, default=None):

          path = Path(path)



          if not path.exists():

                    return default if default is not None else []



          try:

                    with path.open("r", encoding="utf-8") as file:

                              return json.load(file)

          except (json.JSONDecodeError, OSError) as exc:

                    print(f"[WARN] Could not read {path}: {exc}")

                    return default if default is not None else []





def save_json(path, data):

          path = Path(path)

          path.parent.mkdir(parents=True, exist_ok=True)



          temporary_path = path.with_suffix(path.suffix + ".tmp")



          with temporary_path.open("w", encoding="utf-8") as file:

                    json.dump(data, file, indent=2, ensure_ascii=False)



          temporary_path.replace(path)





# ============================================================

# NORMALIZATION AND DEDUPLICATION

# ============================================================



def normalize_text(value):

          value = str(value or "").lower().strip()

          value = value.replace("&", " and ")

          value = re.sub(r"[^a-z0-9]+", " ", value)

          return re.sub(r"\s+", " ", value).strip()





def normalize_address(value):

          address = normalize_text(value)



          replacements = {

                    " street ": " st ",

                    " avenue ": " ave ",

                    " boulevard ": " blvd ",

                    " road ": " rd ",

                    " drive ": " dr ",

                    " lane ": " ln ",

                    " court ": " ct ",

                    " circle ": " cir ",

                    " highway ": " hwy ",

                    " parkway ": " pkwy ",

                    " place ": " pl ",

                    " suite ": " ste ",

                    " apartment ": " apt ",

          }



          address = f" {address} "



          for old, new in replacements.items():

                    address = address.replace(old, new)



          return re.sub(r"\s+", " ", address).strip()





def normalize_url(url):

          if not url:

                    return ""



          url = str(url).strip()



          if not url:

                    return ""



          if not url.startswith(("http://", "https://")):

                    url = "https://" + url



          try:

                    parsed = urlparse(url)

                    scheme = parsed.scheme.lower() or "https"

                    netloc = parsed.netloc.lower()



                    if netloc.startswith("www."):

                              netloc = netloc[4:]



                    path = parsed.path.rstrip("/")



                    return f"{scheme}://{netloc}{path}".rstrip("/")

          except Exception:

                    return url.lower().rstrip("/")





def get_place_id(business):

          for key in ("place_id", "placeId", "google_place_id", "record_id"):

                    value = business.get(key)



                    if value:

                              return str(value).strip()



          return ""





def business_identity(business):

          """

          Returns:

               - a Google Place ID identity when available

               - a normalized name + full address identity when available



          Using both identities catches duplicates even when the same location

          was imported with different Google Place IDs.

          """

          identities = set()



          place_id = get_place_id(business)



          if place_id:

                    identities.add(("place_id", place_id.lower()))



          name = normalize_text(business.get("name"))

          address = normalize_address(

                    business.get("address")

                    or business.get("formatted_address")

                    or business.get("street_address")

          )



          if name and address:

                    identities.add(("name_address", name, address))



          city = normalize_text(business.get("city"))

          state = normalize_text(business.get("state"))



          # Only use city/state when no street address exists.

          # This is less precise, so it is not used when an address is available.

          if name and not address and city and state:

                    website = normalize_url(business.get("website"))

                    if website:

                              identities.add(("name_city_state_website", name, city, state, website))



          return identities





def deduplicate_businesses(businesses):

          """

          Deduplicates by Place ID first, then normalized business name + address.

          Different branches with different street addresses are preserved.

          """

          unique = []

          seen_identities = set()

          duplicates = 0



          for business in businesses:

                    identities = business_identity(business)



                    if identities and identities.intersection(seen_identities):

                              duplicates += 1

                              continue



                    unique.append(business)

                    seen_identities.update(identities)



          if duplicates:

                    print(f"[DEDUP] Removed {duplicates} duplicate business locations.")



          return unique





def is_excluded_business(business):

          name = normalize_text(business.get("name"))

          description = normalize_text(

                    business.get("description")

                    or business.get("business_description")

          )



          for brand in EXCLUDED_BRANDS:

                    normalized_brand = normalize_text(brand)



                    if name == normalized_brand or name.startswith(normalized_brand + " "):

                              return f"National chain: {brand}"



          for indicator in CORPORATE_INDICATORS:

                    if indicator in name or indicator in description:

                              return f"Corporate location: {indicator}"



          return None





def filter_excluded_businesses(businesses):

          accepted = []

          excluded = []

          seen_excluded = set()



          for business in businesses:

                    reason = is_excluded_business(business)



                    if not reason:

                              accepted.append(business)

                              continue



                    identities = business_identity(business)

                    key = tuple(sorted(map(str, identities))) if identities else (

                              normalize_text(business.get("name")),

                              normalize_address(business.get("address")),

                    )



                    if key in seen_excluded:

                              continue



                    seen_excluded.add(key)



                    excluded.append({

                              "name": business.get("name", ""),

                              "address": business.get("address", ""),

                              "place_id": get_place_id(business),

                              "reason": reason,

                    })



          return accepted, excluded





def load_businesses(path):

          raw = load_json(path, default=[])



          if isinstance(raw, list):

                    businesses = raw



          elif isinstance(raw, dict):

                    if isinstance(raw.get("businesses"), list):

                              businesses = raw["businesses"]



                    elif isinstance(raw.get("leads"), list):

                              businesses = raw["leads"]



                    elif isinstance(raw.get("results"), list):

                              businesses = raw["results"]



                    else:

                              # Input may be keyed by Google Place ID.

                              businesses = []



                              for key, value in raw.items():

                                        if not isinstance(value, dict):

                                                  continue



                                        business = dict(value)

                                        business.setdefault("place_id", key)

                                        business.setdefault("record_id", key)

                                        businesses.append(business)



          else:

                    businesses = []



          businesses = deduplicate_businesses(businesses)

          businesses, excluded = filter_excluded_businesses(businesses)



          # Preserve prior exclusions and add any new ones.

          old_exclusions = load_json(EXCLUDED_FILE, default=[])

          combined = old_exclusions if isinstance(old_exclusions, list) else []



          exclusion_seen = {

                    (

                              normalize_text(item.get("name")),

                              normalize_address(item.get("address")),

                              normalize_text(item.get("reason")),

                    )

                    for item in combined

                    if isinstance(item, dict)

          }



          for item in excluded:

                    key = (

                              normalize_text(item.get("name")),

                              normalize_address(item.get("address")),

                              normalize_text(item.get("reason")),

                    )



                    if key not in exclusion_seen:

                              combined.append(item)

                              exclusion_seen.add(key)



          save_json(EXCLUDED_FILE, combined)



          print(f"[LOAD] Eligible businesses: {len(businesses)}")

          print(f"[LOAD] Excluded national chains/corporate locations: {len(combined)}")



          return businesses





def deduplicate_existing_analyses(analyses):

          unique = []

          seen_identities = set()

          seen_record_ids = set()

          duplicates = 0



          for item in analyses:

                    if not isinstance(item, dict):

                              continue



                    business = item.get("business", {})

                    if not isinstance(business, dict):

                              business = {}



                    reason = is_excluded_business(business)



                    if reason:

                              duplicates += 1

                              continue



                    record_id = str(item.get("record_id") or get_place_id(business) or "").strip()



                    identities = business_identity(business)



                    if record_id and record_id in seen_record_ids:

                              duplicates += 1

                              continue



                    if identities and identities.intersection(seen_identities):

                              duplicates += 1

                              continue



                    unique.append(item)



                    if record_id:

                              seen_record_ids.add(record_id)



                    seen_identities.update(identities)



          if duplicates:

                    print(f"[DEDUP] Removed {duplicates} duplicate/excluded saved analyses.")



          return unique





def record_id_for(business, index=0):

          return (

                    get_place_id(business)

                    or business.get("id")

                    or business.get("business_id")

                    or f"{normalize_text(business.get('name'))}|"

                             f"{normalize_address(business.get('address'))}"

                    or f"record-{index}"

          )





# ============================================================

# WEBSITE FETCHING AND EXTRACTION

# ============================================================



def fetch(url):

          headers = {"User-Agent": USER_AGENT}



          try:

                    response = requests.get(

                              url,

                              headers=headers,

                              timeout=REQUEST_TIMEOUT,

                              allow_redirects=True,

                    )



                    return {

                              "ok": response.ok,

                              "status_code": response.status_code,

                              "url": response.url,

                              "html": response.text if response.ok else "",

                              "error": "" if response.ok else f"HTTP {response.status_code}",

                    }



          except requests.RequestException as exc:

                    return {

                              "ok": False,

                              "status_code": None,

                              "url": url,

                              "html": "",

                              "error": str(exc),

                    }





def extract_page(url, html):

          soup = BeautifulSoup(html or "", "html.parser")



          for element in soup([

                    "script",

                    "style",

                    "noscript",

                    "svg",

                    "template",

                    "iframe",

          ]):

                    element.decompose()



          title = soup.title.get_text(" ", strip=True) if soup.title else ""



          text = soup.get_text(" ", strip=True)

          text = re.sub(r"\s+", " ", text)



          links = []



          for anchor in soup.find_all("a", href=True):

                    href = urljoin(url, anchor.get("href", "").strip())

                    label = anchor.get_text(" ", strip=True)



                    if href.startswith(("http://", "https://")):

                              links.append({

                                        "url": href,

                                        "text": label,

                              })



          emails = set(

                    re.findall(

                              r"[\w.+-]+@[\w-]+\.[\w.-]+",

                              html or "",

                              flags=re.IGNORECASE,

                    )

          )



          phones = set(

                    re.findall(

                              r"(?:\\+?1[\s.-]?)?(?:\\(?\d{3}\\)?[\s.-])\d{3}[\s.-]\d{4}",

                              text,

                    )

          )



          lower_html = (html or "").lower()

          lower_text = text.lower()



          h1_count = len(soup.find_all("h1"))

          h2_count = len(soup.find_all("h2"))

          image_count = len(soup.find_all("img"))



          viewport = bool(

                    soup.find(

                              "meta",

                              attrs={"name": re.compile("viewport", re.I)},

                    )

          )



          cms_signals = [

                    signal for signal in (

                              "wordpress",

                              "wix",

                              "squarespace",

                              "webflow",

                              "shopify",

                              "godaddy",

                              "weebly",

                    )

                    if signal in lower_html

          ]



          social_links = [

                    link["url"]

                    for link in links

                    if any(

                              domain in link["url"].lower()

                              for domain in (

                                        "facebook.com",

                                        "instagram.com",

                                        "linkedin.com",

                                        "youtube.com",

                                        "tiktok.com",

                                        "x.com",

                              )

                    )

          ]



          broken_markers = [

                    marker for marker in (

                              "404 not found",

                              "page not found",

                              "coming soon",

                              "under construction",

                              "index of /",

                              "website expired",

                              "domain for sale",

                    )

                    if marker in lower_text

          ]



          old_years = [

                    year for year in re.findall(r"\b(?:19|20)\d{2}\b", lower_text)

                    if int(year) <= 2020

          ]



          return {

                    "url": url,

                    "final_url": url,

                    "title": title,

                    "text": text[:18000],

                    "links": links[:250],

                    "emails": sorted(emails),

                    "phones": sorted(phones),

                    "html_signals": {

                              "has_viewport": viewport,

                              "h1_count": h1_count,

                              "h2_count": h2_count,

                              "image_count": image_count,

                              "has_contact_link": any(

                                        word in link["url"].lower() or word in link["text"].lower()

                                        for link in links

                                        for word in ("contact", "get in touch")

                              ),

                              "has_about_link": any(

                                        word in link["url"].lower() or word in link["text"].lower()

                                        for link in links

                                        for word in ("about", "our story")

                              ),

                              "has_services_link": any(

                                        word in link["url"].lower() or word in link["text"].lower()

                                        for link in links

                                        for word in ("service", "what-we-do")

                              ),

                              "has_testimonials": any(

                                        word in lower_text

                                        for word in ("testimonial", "reviews", "what our customers say")

                              ),

                              "has_social_links": bool(social_links),

                              "social_links": social_links[:20],

                              "cms_signals": cms_signals,

                              "broken_markers": broken_markers,

                              "old_years": sorted(set(old_years)),

                    },

          }





def crawl_contact_pages(base_url, initial_page):

          parsed_base = urlparse(base_url)

          base_domain = parsed_base.netloc.lower().removeprefix("www.")



          pages = [initial_page]

          visited = {normalize_url(base_url)}



          candidates = []



          for link in initial_page.get("links", []):

                    link_url = link.get("url", "")

                    link_text = link.get("text", "").lower()

                    parsed_link = urlparse(link_url)

                    link_domain = parsed_link.netloc.lower().removeprefix("www.")



                    if link_domain != base_domain:

                              continue



                    combined = f"{link_url.lower()} {link_text}"



                    if any(

                              word in combined

                              for word in (

                                        "contact",

                                        "about",

                                        "team",

                                        "staff",

                                        "our-story",

                                        "get-in-touch",

                              )

                    ):

                              candidates.append(link_url)



          for path in (

                    "/contact",

                    "/contact-us",

                    "/about",

                    "/about-us",

                    "/team",

          ):

                    candidates.append(urljoin(base_url.rstrip("/") + "/", path.lstrip("/")))



          for candidate in candidates:

                    normalized = normalize_url(candidate)



                    if normalized in visited:

                              continue



                    visited.add(normalized)



                    if len(pages) >= 5:

                              break



                    result = fetch(candidate)



                    if result["ok"] and result["html"]:

                              page = extract_page(result["url"], result["html"])

                              pages.append(page)



                    time.sleep(SLEEP_SECONDS)



          return pages





def website_signals(business, page, pages):

          all_text = " ".join(

                    item.get("text", "")

                    for item in pages

                    if isinstance(item, dict)

          ).lower()



          all_emails = set()

          all_phones = set()

          all_links = []



          for item in pages:

                    all_emails.update(item.get("emails", []))

                    all_phones.update(item.get("phones", []))

                    all_links.extend(item.get("links", []))



          website = normalize_url(

                    business.get("website")

                    or page.get("url")

          )



          parsed_website = urlparse(website)

          domain = parsed_website.netloc.lower().removeprefix("www.")



          business_name = business.get("name", "")



          service_terms = [

                    "plumbing",

                    "heating",

                    "cooling",

                    "hvac",

                    "roofing",

                    "contractor",

                    "electrical",

                    "landscaping",

                    "dental",

                    "dentist",

                    "lawyer",

                    "attorney",

                    "auto repair",

                    "repair",

                    "cleaning",

                    "pest control",

                    "remodeling",

                    "construction",

          ]



          service_mentions = [

                    term for term in service_terms

                    if term in all_text or term in normalize_text(business_name)

          ]



          contact_links = [

                    link for link in all_links

                    if any(

                              word in (

                                        link.get("url", "").lower()

                                        + " "

                                        + link.get("text", "").lower()

                              )

                              for word in ("contact", "about", "team", "staff")

                    )

          ]



          return {

                    "business_name": business_name,

                    "address": business.get("address", ""),

                    "city": business.get("city", ""),

                    "state": business.get("state", ""),

                    "primary_type": business.get("primary_type", ""),

                    "types": business.get("types", []),

                    "website": website,

                    "domain": domain,

                    "page_title": page.get("title", ""),

                    "page_count": len(pages),

                    "text_excerpt": all_text[:12000],

                    "emails": sorted(all_emails),

                    "phones": sorted(all_phones),

                    "contact_links": contact_links[:30],

                    "service_mentions": service_mentions,

                    "html_signals": page.get("html_signals", {}),

                    "business_provided_phone": business.get("phone", ""),

                    "business_provided_website": business.get("website", ""),

          }





# ============================================================

# OLLAMA WEBSITE GRADING

# ============================================================



def strict_llm_grade(business, signals):

          prompt = f"""

You are a conservative evaluator of small-business websites and potential

website redesign leads.



Evaluate only the supplied evidence. Do not invent facts. A business with

no website, an unreachable website, or a clearly broken website can be a

strong redesign opportunity. A polished and current website should score

lower as an opportunity.



Score each field from 0 to 100:

- website_quality: 0 means unusable or absent; 100 means excellent.

- opportunity_score: 0 means no apparent redesign opportunity; 100 means

     very strong opportunity.

- confidence: how confident you are based on the supplied evidence.



Consider mobile readiness, clarity, navigation, contact details, service

information, trust signals, content quality, and obvious technical problems.

Do not automatically assume that a dated design means the owner wants a

redesign.



Business:

{json.dumps(business, ensure_ascii=False)}



Website evidence:

{json.dumps(signals, ensure_ascii=False)}



Return only a JSON object with:

{{

     "website_quality": 0,

     "opportunity_score": 0,

     "confidence": 0,

     "recommended": true,

     "reasoning": "Short evidence-based explanation",

     "issues": ["specific issue"],

     "potential_improvements": ["specific improvement"]

}}

"""



          try:

                    response = requests.post(

                              f"{OLLAMA_URL.rstrip('/')}/api/chat",

                              json={

                                        "model": OLLAMA_MODEL,

                                        "messages": [

                                                  {

                                                            "role": "system",

                                                            "content": (

                                                                      "Return valid JSON only. Be conservative, "

                                                                      "evidence-based, and concise."

                                                            ),

                                                  },

                                                  {"role": "user", "content": prompt},

                                        ],

                                        "format": "json",

                                        "stream": False,

                                        "options": {"temperature": 0.1},

                              },

                              timeout=180,

                    )



                    response.raise_for_status()

                    payload = response.json()

                    content = payload.get("message", {}).get("content", "{}")

                    grade = json.loads(content)



                    for field in (

                              "website_quality",

                              "opportunity_score",

                              "confidence",

                    ):

                              try:

                                        grade[field] = max(0, min(100, int(grade.get(field, 0))))

                              except (TypeError, ValueError):

                                        grade[field] = 0



                    grade["recommended"] = bool(grade.get("recommended", False))

                    grade["reasoning"] = str(grade.get("reasoning", ""))

                    grade["issues"] = grade.get("issues", [])

                    grade["potential_improvements"] = grade.get(

                              "potential_improvements", []

                    )



                    if not isinstance(grade["issues"], list):

                              grade["issues"] = []



                    if not isinstance(grade["potential_improvements"], list):

                              grade["potential_improvements"] = []



                    return grade



          except Exception as exc:

                    print(f"[WARN] Ollama grading failed: {exc}")



                    return {

                              "website_quality": 0,

                              "opportunity_score": 50,

                              "confidence": 0,

                              "recommended": False,

                              "reasoning": f"LLM grading failed: {exc}",

                              "issues": [],

                              "potential_improvements": [],

                    }





def deterministic_lead_score(grade, business, signals):

          opportunity = int(grade.get("opportunity_score", 0))

          business_name = normalize_text(business.get("name"))

          category = normalize_text(

                    business.get("primary_type")

                    or business.get("category")

                    or ""

          )



          types = business.get("types", [])

          if isinstance(types, list):

                    category += " " + " ".join(

                              normalize_text(item) for item in types

                    )



          high_value_terms = [

                    "plumber",

                    "plumbing",

                    "hvac",

                    "heating",

                    "cooling",

                    "roof",

                    "contractor",

                    "construction",

                    "electrician",

                    "electrical",

                    "dentist",

                    "dental",

                    "lawyer",

                    "attorney",

                    "landscap",

                    "remodel",

                    "pest control",

                    "auto repair",

                    "garage door",

                    "flooring",

                    "concrete",

                    "restoration",

          ]



          medium_value_terms = [

                    "cleaning",

                    "salon",

                    "barber",

                    "photography",

                    "fitness",

                    "pet groom",

                    "restaurant",

                    "catering",

                    "moving",

                    "painting",

          ]



          if any(term in category or term in business_name for term in high_value_terms):

                    business_value = 95

          elif any(

                    term in category or term in business_name

                    for term in medium_value_terms

          ):

                    business_value = 75

          else:

                    business_value = 55



          emails = signals.get("emails", [])

          phones = signals.get("phones", []) or [

                    business.get("phone", "")

          ]



          contactability = 30



          if business.get("phone") or any(phones):

                    contactability += 30



          if emails:

                    contactability += 40



          contactability = min(100, contactability)



          score = round(

                    opportunity * 0.65

                    + business_value * 0.20

                    + contactability * 0.15

          )



          return {

                    "lead_score": score,

                    "opportunity_score": opportunity,

                    "business_value_score": business_value,

                    "contactability_score": contactability,

          }





# ============================================================

# WEBSITE ANALYSIS PIPELINE

# ============================================================



def run_analyze(input_path):

          ensure_directories()



          businesses = load_businesses(input_path)



          existing = load_json(WEBSITE_FILE, default=[])

          if not isinstance(existing, list):

                    existing = []



          existing = deduplicate_existing_analyses(existing)

          save_json(WEBSITE_FILE, existing)



          completed_ids = {

                    str(item.get("record_id"))

                    for item in existing

                    if item.get("record_id")

          }



          completed_identities = set()



          for item in existing:

                    business = item.get("business", {})

                    if isinstance(business, dict):

                              completed_identities.update(business_identity(business))



          print(f"[ANALYZE] Businesses queued: {len(businesses)}")

          print(f"[ANALYZE] Previously completed: {len(completed_ids)}")



          for index, business in enumerate(businesses, start=1):

                    if not isinstance(business, dict):

                              continue



                    record_id = str(record_id_for(business, index))

                    identities = business_identity(business)



                    if record_id in completed_ids:

                              continue



                    if identities and identities.intersection(completed_identities):

                              print(f"[SKIP DUPLICATE] {business.get('name', 'Unknown')}")

                              continue



                    exclusion_reason = is_excluded_business(business)



                    if exclusion_reason:

                              print(

                                        f"[SKIP EXCLUDED] {business.get('name', 'Unknown')}: "

                                        f"{exclusion_reason}"

                              )

                              continue



                    name = business.get("name", "Unknown business")

                    website = business.get("website", "")



                    print(f"\n[{index}/{len(businesses)}] {name}")



                    result = {

                              "record_id": record_id,

                              "business": business,

                              "analyzed_at": time.strftime("%Y-%m-%d %H:%M:%S"),

                              "website": website,

                    }



                    try:

                              if not website:

                                        signals = {

                                                  "business_name": name,

                                                  "website": "",

                                                  "emails": [],

                                                  "phones": [business.get("phone", "")]

                                                  if business.get("phone")

                                                  else [],

                                                  "text_excerpt": "",

                                                  "page_count": 0,

                                                  "html_signals": {},

                                        }



                                        grade = {

                                                  "website_quality": 0,

                                                  "opportunity_score": 100,

                                                  "confidence": 90,

                                                  "recommended": True,

                                                  "reasoning": (

                                                            "No website was supplied in the business record. "

                                                            "Verify manually before contacting."

                                                  ),

                                                  "issues": ["No website listed"],

                                                  "potential_improvements": [

                                                            "Build a professional mobile-friendly website",

                                                            "Add service descriptions and contact information",

                                                  ],

                                        }



                              else:

                                        normalized_website = normalize_url(website)

                                        fetched = fetch(normalized_website)



                                        if not fetched["ok"]:

                                                  signals = {

                                                            "business_name": name,

                                                            "website": normalized_website,

                                                            "domain": urlparse(normalized_website).netloc,

                                                            "emails": [],

                                                            "phones": [business.get("phone", "")]

                                                            if business.get("phone")

                                                            else [],

                                                            "text_excerpt": "",

                                                            "page_count": 0,

                                                            "fetch_error": fetched["error"],

                                                            "html_signals": {},

                                                  }



                                                  grade = {

                                                            "website_quality": 0,

                                                            "opportunity_score": 90,

                                                            "confidence": 35,

                                                            "recommended": True,

                                                            "reasoning": (

                                                                      "The website could not be reached during this "

                                                                      "check. This may be temporary and needs manual "

                                                                      "verification."

                                                            ),

                                                            "issues": [

                                                                      "Website fetch failed",

                                                                      fetched["error"],

                                                            ],

                                                            "potential_improvements": [

                                                                      "Verify domain and website availability",

                                                            ],

                                                  }



                                        else:

                                                  page = extract_page(fetched["url"], fetched["html"])

                                                  page["final_url"] = fetched["url"]



                                                  pages = crawl_contact_pages(fetched["url"], page)

                                                  signals = website_signals(business, page, pages)

                                                  grade = strict_llm_grade(business, signals)



                              score = deterministic_lead_score(grade, business, signals)



                              result.update({

                                        "signals": signals,

                                        "grade": grade,

                                        **score,

                              })



                    except Exception as exc:

                              print(f"[ERROR] {name}: {exc}")



                              result.update({

                                        "error": str(exc),

                                        "signals": {

                                                  "business_name": name,

                                                  "website": website,

                                                  "emails": [],

                                                  "phones": [business.get("phone", "")]

                                                  if business.get("phone")

                                                  else [],

                                        },

                                        "grade": {

                                                  "website_quality": 0,

                                                  "opportunity_score": 0,

                                                  "confidence": 0,

                                                  "recommended": False,

                                                  "reasoning": f"Analysis failed: {exc}",

                                                  "issues": [str(exc)],

                                                  "potential_improvements": [],

                                        },

                                        "lead_score": 0,

                                        "opportunity_score": 0,

                                        "business_value_score": 0,

                                        "contactability_score": 0,

                              })



                    existing.append(result)

                    save_json(WEBSITE_FILE, existing)



                    completed_ids.add(record_id)

                    completed_identities.update(identities)



                    print(

                              f"     Score: {result.get('lead_score', 0)} | "

                              f"Opportunity: {result.get('opportunity_score', 0)} | "

                              f"Quality: {result.get('grade', {}).get('website_quality', 0)}"

                    )



                    time.sleep(SLEEP_SECONDS)



          # Clean up duplicates that might have existed in prior runs.

          existing = deduplicate_existing_analyses(existing)

          save_json(WEBSITE_FILE, existing)



          print(f"\n[COMPLETE] Saved {len(existing)} website analyses.")

          print(f"[OUTPUT] {WEBSITE_FILE}")





# ============================================================

# QUALIFICATION

# ============================================================



def run_qualify(minimum_score=65):

          ensure_directories()



          analyses = load_json(WEBSITE_FILE, default=[])



          if not isinstance(analyses, list):

                    analyses = []



          analyses = deduplicate_existing_analyses(analyses)

          save_json(WEBSITE_FILE, analyses)



          qualified = []



          for item in analyses:

                    business = item.get("business", {})



                    if not isinstance(business, dict):

                              continue



                    if is_excluded_business(business):

                              continue



                    grade = item.get("grade", {})

                    score = int(item.get("lead_score", 0) or 0)



                    if score < minimum_score:

                              continue



                    if not grade.get("recommended", False):

                              continue



                    qualified.append(item)



          qualified.sort(

                    key=lambda item: int(item.get("lead_score", 0) or 0),

                    reverse=True,

          )



          qualified = deduplicate_existing_analyses(qualified)

          save_json(QUALIFIED_FILE, qualified)



          print(

                    f"[QUALIFY] {len(qualified)} leads scored at least "

                    f"{minimum_score} and were recommended."

          )

          print(f"[OUTPUT] {QUALIFIED_FILE}")





# ============================================================

# CONTACT FINDING

# ============================================================



def find_contacts_for_lead(item):

          business = item.get("business", {})

          signals = item.get("signals", {})



          emails = signals.get("emails", [])

          phones = signals.get("phones", [])



          if not isinstance(emails, list):

                    emails = []



          if not isinstance(phones, list):

                    phones = []



          business_phone = business.get("phone")



          if business_phone:

                    phones.insert(0, business_phone)



          website = normalize_url(

                    business.get("website")

                    or signals.get("website")

          )



          domain = urlparse(website).netloc.lower().removeprefix("www.")



          unique_emails = []

          seen_emails = set()



          for email in emails:

                    email = str(email).strip().lower()



                    if not email or email in seen_emails:

                              continue



                    if any(

                              token in email

                              for token in (

                                        "noreply",

                                        "no-reply",

                                        "donotreply",

                                        "example.com",

                                        "sentry.io",

                                        "wixpress.com",

                              )

                    ):

                              continue



                    seen_emails.add(email)

                    unique_emails.append(email)



          # Prefer addresses matching the business's website domain.

          unique_emails.sort(

                    key=lambda email: (

                              0 if domain and email.split("@")[-1] == domain else 1,

                              email,

                    )

          )



          unique_phones = []

          seen_phones = set()



          for phone in phones:

                    phone = re.sub(r"\s+", " ", str(phone or "")).strip()



                    if not phone:

                              continue



                    normalized = re.sub(r"\D", "", phone)



                    if len(normalized) < 7 or normalized in seen_phones:

                              continue



                    seen_phones.add(normalized)

                    unique_phones.append(phone)



          return {

                    "record_id": item.get("record_id", ""),

                    "business": business,

                    "lead_score": item.get("lead_score", 0),

                    "website": website,

                    "domain": domain,

                    "emails": unique_emails,

                    "phones": unique_phones,

                    "contact_status": (

                              "contact_found"

                              if unique_emails or unique_phones

                              else "manual_research_needed"

                    ),

          }





def run_contacts():

          ensure_directories()



          qualified = load_json(QUALIFIED_FILE, default=[])



          if not isinstance(qualified, list):

                    qualified = []



          qualified = deduplicate_existing_analyses(qualified)



          contacts = []

          seen_ids = set()

          seen_identities = set()



          for item in qualified:

                    contact = find_contacts_for_lead(item)

                    record_id = str(contact.get("record_id", ""))

                    identities = business_identity(contact.get("business", {}))



                    if record_id and record_id in seen_ids:

                              continue



                    if identities and identities.intersection(seen_identities):

                              continue



                    contacts.append(contact)



                    if record_id:

                              seen_ids.add(record_id)



                    seen_identities.update(identities)



          save_json(CONTACTS_FILE, contacts)



          print(f"[CONTACTS] Processed {len(contacts)} leads.")

          print(f"[OUTPUT] {CONTACTS_FILE}")





# ============================================================

# OUTREACH DRAFTING

# ============================================================





def outreach_llm(contact, analysis):

          business = contact.get("business", {})

          grade = analysis.get("grade", {})

          signals = analysis.get("signals", {})



          prompt = f"""

Write a short cold email from a 25-year-old software developer

in Northern Colorado who builds websites for local businesses.



VOICE:

- Casual, direct, friendly, and confident.

- Sound like a real person, not an agency or AI.

- Use normal everyday English and contractions.

- No forced slang, fake enthusiasm, or corporate language.

- Keep it around 50-85 words. Shorter is fine.

- Write like you're emailing another person, not pitching a company.



GREETING RULES:

- Start every email with either "Howdy," or "Hello Business Name,"

- Use the actual business name from the supplied business data when available.

- If the business name is missing or awkward to use, write "Howdy,"

- Never use placeholders such as [Name], {{name}}, <business>, or "Hi Name".

- Never leave an incomplete greeting.

- Never invent a business name.



AVOID:

- "I hope this email finds you well"

- "I wanted to reach out"

- "I came across your business"

- "Digital presence", "elevate your brand", "drive growth"

- Generic compliments, long introductions, feature lists,

     exaggerated claims, and pushy sales language.



DO:

- Explain naturally why you're emailing.

- Mention one specific detail only when supported by evidence.

- Briefly mention that you build websites.

- For businesses with no listed website, say you couldn't find one,

     not that they definitely don't have one.

- For unreachable websites, don't assume the site is broken.

- End with a simple, low-pressure question.

- Vary the wording and opening between businesses.

- Never invent experience, clients, results, or personal connections.

- Don't use placeholders or pretend you've already spoken.

- Draft only. Do not send.





BUSINESS:

{json.dumps(business, ensure_ascii=False)}



WEBSITE EVIDENCE:

{json.dumps(signals, ensure_ascii=False)}



ANALYSIS:

{json.dumps(grade, ensure_ascii=False)}



Return JSON only:

{{

     "subject": "Natural, short subject",

     "body": "Email body",

     "personalization_basis": "Evidence used"

}}

"""



          try:

                    response = requests.post(

                              f"{OLLAMA_URL.rstrip('/')}/api/chat",

                              json={

                                        "model": OLLAMA_MODEL,

                                        "messages": [

                                                  {

                                                            "role": "system",

                                                            "content": (

                                                                      "Write like a real, relaxed, technically minded "

                                                                      "young independent developer. Be concise, natural, "

                                                                      "and specific. Never sound corporate or templated. "

                                                                      "Return valid JSON only."

                                                            ),

                                                  },

                                                  {"role": "user", "content": prompt},

                                        ],

                                        "format": "json",

                                        "stream": False,

                                        "options": {

                                                  "temperature": 0.65,

                                                  "top_p": 0.9,

                                        },

                              },

                              timeout=180,

                    )



                    response.raise_for_status()

                    content = response.json().get("message", {}).get("content", "{}")

                    draft = json.loads(content)



                    business_name = str(business.get("name") or "").strip()

                    body = str(draft.get("body", "")).strip()



                    # Remove common placeholders anywhere in the email.

                    placeholders = [

                              "[name]", "{name}", "<name>",

                              "[business]", "{business}", "<business>",

                              "[business name]", "{business_name}",

                              "your name here", "insert name",

                    ]



                    for placeholder in placeholders:

                              body = re.sub(

                                        re.escape(placeholder),

                                        "",

                                        body,

                                        flags=re.IGNORECASE,

                              )



                    # Enforce the required greeting.

                    lines = body.splitlines()

                    if lines and lines[0].strip().lower() in [

                              "howdy,", "howdy", "hello,", "hello",

                              "hi,", "hi", "hey,", "hey",

                              "hi there,", "hello there,",

                    ]:

                              lines = lines[1:]

                    elif lines and re.match(

                              r"^(hi|hey|hello)\s+.+[,!]?$",

                              lines[0].strip(),

                              re.IGNORECASE,

                    ):

                              lines = lines[1:]



                    greeting = (

                              f"Hello {business_name},"

                              if business_name

                              else "Howdy,"

                    )

                    body = greeting + "\n\n" + "\n".join(lines).strip()

                    draft["body"] = body



                    return {

                              "subject": str(draft.get("subject", "")).strip(),

                              "body": str(draft.get("body", "")).strip(),

                              "personalization_basis": str(

                                        draft.get("personalization_basis", "")

                              ).strip(),

                              "error": "",

                    }



          except Exception as exc:

                    return {

                              "subject": "",

                              "body": "",

                              "personalization_basis": "",

                              "error": str(exc),

                    }





def run_outreach():
    ensure_directories()

    contacts = load_json(CONTACTS_FILE, default=[])
    analyses = load_json(WEBSITE_FILE, default=[])
    existing_queue = load_json(OUTREACH_FILE, default=[])

    if not isinstance(contacts, list):
        contacts = []
    if not isinstance(analyses, list):
        analyses = []
    if not isinstance(existing_queue, list):
        existing_queue = []

    analyses = deduplicate_existing_analyses(analyses)
    analysis_by_id = {
        str(item.get("record_id")): item
        for item in analyses
        if item.get("record_id")
    }

    # Resume from the saved queue instead of starting over and overwriting it.
    queue = existing_queue
    seen_ids = {
        str(item.get("record_id"))
        for item in queue
        if isinstance(item, dict) and item.get("record_id")
    }
    seen_identities = set()
    for item in queue:
        if not isinstance(item, dict):
            continue
        business = item.get("business", {})
        if isinstance(business, dict):
            seen_identities.update(business_identity(business))

    skipped_existing = 0
    skipped_duplicate = 0
    total = len(contacts)

    for index, contact in enumerate(contacts, start=1):
        if not isinstance(contact, dict):
            continue

        business = contact.get("business", {})
        if not isinstance(business, dict):
            business = {}

        if is_excluded_business(business):
            continue

        record_id = str(contact.get("record_id", ""))
        identities = business_identity(business)

        if record_id and record_id in seen_ids:
            skipped_existing += 1
            continue

        if identities and identities.intersection(seen_identities):
            skipped_duplicate += 1
            continue

        analysis = analysis_by_id.get(record_id, {})

        if contact.get("emails"):
            recommended_method = "email"
        elif contact.get("phones"):
            recommended_method = "phone"
        else:
            recommended_method = "manual_research"

        print(
            f"[OUTREACH {index}/{total}] "
            f"{business.get('name', 'Unknown business')} "
            f"(saved drafts: {len(queue)})"
        )

        draft = outreach_llm(contact, analysis)

        queue.append({
            "record_id": record_id,
            "business": business,
            "lead_score": contact.get("lead_score", 0),
            "recommended_method": recommended_method,
            "emails": contact.get("emails", []),
            "phones": contact.get("phones", []),
            "subject": draft.get("subject", ""),
            "body": draft.get("body", ""),
            "personalization_basis": draft.get("personalization_basis", ""),
            "draft_error": draft.get("error", ""),
            "status": "needs_human_review",
            "auto_send": False,
        })

        if record_id:
            seen_ids.add(record_id)
        seen_identities.update(identities)

        # Persist the accumulated queue after each new draft.
        save_json(OUTREACH_FILE, queue)
        time.sleep(SLEEP_SECONDS)

    save_json(OUTREACH_FILE, queue)

    print(f"[OUTREACH] Queue now contains {len(queue)} drafts.")
    print(f"[OUTREACH] Skipped {skipped_existing} contacts already drafted.")
    print(f"[OUTREACH] Skipped {skipped_duplicate} duplicate businesses.")
    print("[OUTREACH] Nothing was sent automatically.")
    print(f"[OUTPUT] {OUTREACH_FILE}")


# FULL PIPELINE

# ============================================================



def run_all(input_path, minimum_score):

          run_analyze(input_path)

          run_qualify(minimum_score)

          run_contacts()

          run_outreach()





# ============================================================

# CLI

# ============================================================



def main():

          parser = argparse.ArgumentParser(

                    description="Local business website lead-generation pipeline."

          )



          parser.add_argument(

                    "--input",

                    default=str(DEFAULT_INPUT),

                    help="Input business JSON file.",

          )



          parser.add_argument(

                    "--min-score",

                    type=int,

                    default=65,

                    help="Minimum lead score for qualification.",

          )



          parser.add_argument(

                    "--analyze-websites",

                    action="store_true",

                    help="Analyze websites and score leads.",

          )



          parser.add_argument(

                    "--qualify",

                    action="store_true",

                    help="Qualify analyzed leads.",

          )



          parser.add_argument(

                    "--find-contacts",

                    action="store_true",

                    help="Find contact details for qualified leads.",

          )



          parser.add_argument(

                    "--draft-outreach",

                    action="store_true",

                    help="Draft outreach messages for human review.",

          )



          parser.add_argument(

                    "--run",

                    action="store_true",

                    help="Run the complete pipeline.",

          )



          args = parser.parse_args()



          ensure_directories()



          if args.run:

                    run_all(args.input, args.min_score)

                    return



          selected = any((

                    args.analyze_websites,

                    args.qualify,

                    args.find_contacts,

                    args.draft_outreach,

          ))



          if not selected:

                    parser.print_help()

                    return



          if args.analyze_websites:

                    run_analyze(args.input)



          if args.qualify:

                    run_qualify(args.min_score)



          if args.find_contacts:

                    run_contacts()



          if args.draft_outreach:

                    run_outreach()





if __name__ == "__main__":

          main()