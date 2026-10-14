#!/usr/bin/env python3
"""
leadGuillotine.py - strict filter / re-scorer for the outreach queue.

What it does
------------
Reads output/outreach_queue.json (READ-ONLY, never modified), re-scores every
record from scratch, throws out the junk, and pushes businesses WITHOUT a real
website to the top.

  * Chains, franchises, national brands, government, banks, schools: REJECTED.
  * Businesses with no website (or only a Facebook/Yelp-style page): top tier.
      - Confirmed via Google Places (by place id) = strongest.
      - Not confirmed = still ranked high but forced to MANUAL_REVIEW.
  * Businesses with a website: only kept if there are 2+ concrete, evidenced
    problem categories. A prior "good" score does not save a site that has
    evidenced problems, and a prior high lead_score does not save a site
    that has no evidence of problems. Both get flagged in the output.
  * Blocked / unreachable sites (403, 409, TLS, timeout) are INCONCLUSIVE,
    never evidence of a bad site.
  * Email must be a real address present in the record AND either carry a
    credible source URL or match the official website's domain. Phone numbers
    and search-suggestion strings are never treated as emails.
  * Nothing is ever sent. No emails. No writes to the source queue.

Optional flags
--------------
  --no-places     skip Google Places lookups (offline mode)
  --check-sites   live-fetch each existing website for objective problems
                  (no viewport tag, plain http, flash/marquee, stale copyright)
  --no-email-search  skip email discovery (on by default; only runs for records that
                  survive filtering and have no verified email yet; results are cached)
  --inspect N     print what was extracted from the first N records and exit
  --cards-only    rebuild lead_guillotine_cards.html from the last run, no lookups
  --open          open the lead deck in your browser when done
  --retry-all     discard CACHED Places/site/email lookups for the selected records
                  and fetch again. Scores are always recomputed either way.
  --limit N       only process the first N unique records
  --min-score N   approval threshold (default 70, env LEAD_MIN_SCORE)
  --input PATH    use a different queue file
"""

import argparse
import html
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
import base64
import webbrowser
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR
sys.path.insert(0, str(ROOT / "local_packages"))

try:
    import requests
except ImportError:
    requests = None

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    for env_file in (ROOT.parent / ".env", ROOT / ".env", ROOT.parent.parent / ".env"):
        if env_file.exists():
            load_dotenv(env_file, override=False)

PLACES_KEY = (
    os.getenv("GOOGLE_PLACES_API_KEY") or os.getenv("GOOGLE_MAPS_API_KEY") or ""
).strip()
REQUEST_TIMEOUT = int(os.getenv("LEAD_HTTP_TIMEOUT", "18"))
PAUSE = float(os.getenv("LEAD_REQUEST_PAUSE", "0.35"))
MIN_SCORE = int(os.getenv("LEAD_MIN_SCORE", "70"))

OUTPUT_DIR = ROOT / "output"
STATE_DIR = ROOT / "lead_guillotine_state"

RESULTS_FILE = ROOT / "lead_guillotine_results.json"
APPROVED_FILE = ROOT / "lead_guillotine_approved.json"
REVIEW_FILE = ROOT / "lead_guillotine_manual_review.json"
REJECTED_FILE = ROOT / "lead_guillotine_rejected.json"
NO_WEBSITE_FILE = ROOT / "lead_guillotine_no_website.json"
CARDS_FILE = ROOT / "lead_guillotine_cards.html"
OLD_STATE_FILE = STATE_DIR / "results.json"          # written by the previous version; read-only now
STATE_FILE = STATE_DIR / "filter_state.json"         # lookup cache for this version

THIS_YEAR = datetime.now().year

# --------------------------------------------------------------------------
# Exclusion data
# --------------------------------------------------------------------------

EXCLUDED_BRANDS = {
    "ace hardware", "napa auto parts", "napa", "home depot", "lowes", "walmart",
    "target", "costco", "best buy", "harbor freight", "tractor supply",
    "autozone", "auto zone", "oreilly auto parts", "advance auto parts",
    "pride roofing", "servpro", "mr rooter", "roto rooter", "remax", "re max",
    "coldwell banker", "keller williams", "century 21", "republic services",
    "waste management", "comcast", "xfinity", "verizon", "at t", "t mobile",
    "mcdonalds", "starbucks", "subway", "dominos", "pizza hut", "papa johns",
    "papa murphys", "taco bell", "wendys", "burger king", "chipotle", "dunkin",
    "chick fil a", "panera", "jiffy lube", "great clips", "supercuts",
    "sport clips", "h r block", "hr block", "jackson hewitt", "planet fitness",
    "anytime fitness", "orangetheory", "7 eleven", "king soopers", "safeway",
    "walgreens", "cvs", "ups store", "fedex", "kohls", "sherwin williams",
    "culligan", "stanley steemer", "chem dry", "terminix", "orkin", "aire serv",
    "one hour heating", "benjamin franklin plumbing", "ben franklin plumbing",
    "midas", "meineke", "valvoline", "firestone", "goodyear", "discount tire",
    "les schwab", "enterprise rent a car", "hertz", "edward jones", "ameriprise",
    "merrill", "wells fargo", "chase", "pnc", "us bank", "keybank", "state farm",
    "allstate", "farmers insurance", "american family insurance", "geico",
    "liberty tax", "massage envy", "european wax center", "the home depot",
    "dollar general", "dollar tree", "family dollar", "o reilly auto parts",
    "snap on", "mac tools", "lennox", "carrier", "trane", "dish network",
    "directv", "cbiz",
}

# Word / phrase patterns that mean "not a small-agency prospect".
EXCLUDED_NAME_PATTERNS = [
    r"\bbank\b", r"\bcredit union\b", r"\bhospital\b", r"\bmedical center\b",
    r"\buniversity\b", r"\bcollege\b", r"\bschool district\b",
    r"\belementary\b", r"\bhigh school\b", r"\bpolice\b", r"\bfire department\b",
    r"\bcity of\b", r"\bcounty of\b", r"\btown of\b", r"\bcounty\b.*\boffice\b",
    r"\bcorporate (headquarters|office)\b", r"\bnational headquarters\b",
    r"\binvestor relations\b", r"\bfranchise\b", r"\bpublic library\b",
    r"\bpost office\b", r"\bdmv\b", r"\bcourthouse\b",
    r"#\s*\d{2,}", r"\bstore\s*#?\s*\d+\b", r"\bunit\s*#?\s*\d{3,}\b",
]

EXCLUDED_TYPES = {
    "bank", "atm", "hospital", "university", "school", "primary_school",
    "secondary_school", "local_government_office", "city_hall", "courthouse",
    "police", "fire_station", "post_office", "library", "embassy",
    "department_store", "supermarket", "shopping_mall", "gas_station",
    "convenience_store", "car_rental", "airport", "train_station",
}

BAD_HOSTS = {
    "facebook.com", "instagram.com", "linkedin.com", "yelp.com", "yellowpages.com",
    "mapquest.com", "bbb.org", "angi.com", "angieslist.com", "thumbtack.com",
    "nextdoor.com", "reddit.com", "youtube.com", "tiktok.com", "pinterest.com",
    "google.com", "goo.gl", "bing.com", "duckduckgo.com", "wikipedia.org",
    "chamberofcommerce.com", "manta.com", "buzzfile.com", "opencorporates.com",
    "linktr.ee", "twitter.com", "x.com", "homeadvisor.com", "houzz.com",
    "tripadvisor.com", "doordash.com", "ubereats.com", "grubhub.com",
    "squareup.com", "square.site", "business.site", "godaddysites.com",
}

SEARCH_HOSTS = {"google.com", "bing.com", "duckduckgo.com", "yahoo.com", "search.brave.com"}

FREE_MAIL = {
    "gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com",
    "aol.com", "live.com", "msn.com", "proton.me", "protonmail.com",
}

EMAIL_RE = re.compile(
    r"(?i)(?<![\w.+-])"
    r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")

BAD_EMAIL_BITS = (
    "example.", "sentry.", "wixpress.", "wordpress.", "cloudflare.", "godaddy.",
    "domainprivacy.", "noreply@", "no-reply@", "donotreply@", "do-not-reply@",
    "@2x", "your@", "yourname@", "name@", "user@domain", "test@test",
)
BAD_EMAIL_SUFFIX = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".css", ".js")

# --------------------------------------------------------------------------
# Problem categories (a "concrete issue" = one distinct category with evidence)
# --------------------------------------------------------------------------

ISSUE_CATEGORIES = {
    "mobile": ("mobile", "responsive", "viewport", "small screen", "phone layout"),
    "outdated": ("outdated", "out-of-date", "out of date", "dated design", "dated layout",
                 "old-fashioned", "looks old", "flash", "marquee", "table layout",
                 "not maintained", "stale", "copyright"),
    "navigation": ("navigation", "menu", "broken link", "dead link", "hard to find",
                   "confusing layout"),
    "contact": ("contact", "no phone", "no email", "phone number", "hard to reach"),
    "cta": ("call to action", "call-to-action", "cta", "no booking", "online booking",
            "quote form", "no form", "appointment"),
    "security": ("https", "ssl", "insecure", "not secure", "certificate"),
    "readability": ("readab", "contrast", "font size", "cluttered", "wall of text"),
    "content": ("no services", "services not", "missing information", "thin content",
                "placeholder", "lorem ipsum", "under construction", "no description",
                "missing essential"),
    "performance": ("slow", "load time", "loading", "heavy images", "unoptimized"),
    "trust": ("unprofessional", "no reviews", "no testimonials", "no portfolio",
              "no photos", "stock photo"),
}

POSITIVE_HINTS = ("well-designed", "well designed", "modern", "clean", "excellent",
                  "strong", "good", "great", "professional-looking", "mobile-friendly",
                  "responsive design", "no issues", "no major", "no significant")
NEGATORS = ("not ", "no ", "lack", "poor", "missing", "without", "isn't", "isnt",
            "doesn't", "doesnt", "fail", "weak", "bad", "outdated", "broken", "limited")

ISSUE_KEY_WORDS = ("issue", "problem", "weakness", "deficien", "flaw", "pain", "complaint",
                   "shortcoming", "gap", "negative")
QUALITY_KEYS = ("website_quality_score", "website_score", "site_score", "site_quality_score",
                "quality_score", "design_score", "website_quality")
WEBSITE_KEYS = ("website", "website_url", "websiteuri", "official_website", "site_url",
                "business_website")

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def log(message=""):
    print(message, flush=True)


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path, data):
    """Atomic, validated write. Never leaves a half-written file behind."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    json.loads(payload)  # validate before touching disk
    temp = path.with_suffix(path.suffix + ".tmp")
    with open(temp, "w", encoding="utf-8") as f:
        f.write(payload)
    temp.replace(path)


def norm(value):
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def key_name(value):
    return re.sub(r"[^a-z0-9]", "", norm(value).lower())


def name_words(value):
    text = norm(value).lower().replace("\u2019", "'").replace("'", "")
    text = text.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def to_num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip().rstrip("%"))
    except ValueError:
        return None


def first_value(obj, names):
    if not isinstance(obj, dict):
        return ""
    lower = {str(k).lower(): v for k, v in obj.items()}
    for name in names:
        value = lower.get(name.lower())
        if value is not None and value != "" and isinstance(value, (str, int, float)):
            return str(value).strip()
    return ""


def walk(obj, depth=0, max_depth=4):
    """Yield (key, value) for every dict entry up to max_depth."""
    if depth > max_depth:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k), v
            if isinstance(v, (dict, list)):
                yield from walk(v, depth + 1, max_depth)
    elif isinstance(obj, list):
        for v in obj[:50]:
            if isinstance(v, (dict, list)):
                yield from walk(v, depth + 1, max_depth)


def flatten_strings(value, limit=40):
    out = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(flatten_strings(v, limit))
    elif isinstance(value, list):
        for v in value:
            out.extend(flatten_strings(v, limit))
    return [norm(s) for s in out if norm(s)][:limit]


# --------------------------------------------------------------------------
# URL helpers
# --------------------------------------------------------------------------


def normalize_url(url):
    url = html.unescape(norm(url))
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    try:
        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            return ""
        if not parsed.hostname or "." not in parsed.hostname:
            return ""
        if parsed.username or parsed.password:
            return ""
        return parsed._replace(fragment="", scheme=parsed.scheme.lower()).geturl().rstrip("/")
    except ValueError:
        return ""


def host_of(url):
    try:
        host = (urlparse(url).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host
    except ValueError:
        return ""


def host_matches(host, bad_set):
    return any(host == bad or host.endswith("." + bad) for bad in bad_set)


def is_directory_url(url):
    return host_matches(host_of(url), BAD_HOSTS)


def is_search_url(url):
    parsed = urlparse(url)
    host = host_of(url)
    return host_matches(host, SEARCH_HOSTS) and (
        "/search" in parsed.path or "q=" in parsed.query or parsed.path in ("", "/")
    )


# --------------------------------------------------------------------------
# Reading the queue
# --------------------------------------------------------------------------


def nested_candidates(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("businesses", "candidates", "records", "results", "leads",
                    "outreach_queue", "queue", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        return [data]
    return []


def find_input_file():
    for path in (OUTPUT_DIR / "outreach_queue.json", ROOT / "outreach_queue.json",
                 ROOT / "qualified_leads.json", ROOT / "data" / "businesses.json",
                 ROOT / "businesses.json"):
        if path.exists():
            return path
    matches = [p for p in ROOT.glob("**/outreach_queue.json")
               if not any(x in str(p) for x in ("lead_guillotine_state", "node_modules", ".venv"))]
    if matches:
        return max(matches, key=lambda p: p.stat().st_mtime)
    raise FileNotFoundError("Could not find outreach_queue.json in the project directory.")


def collect_issue_strings(item):
    """Problem statements recorded by the previous agent (never strengths)."""
    found = []
    for key, value in walk(item):
        k = key.lower()
        if "strength" in k or "positive" in k:
            continue
        if any(w in k for w in ISSUE_KEY_WORDS):
            found.extend(flatten_strings(value))
    return list(dict.fromkeys(found))


def collect_prior_quality(item):
    for key, value in walk(item):
        if key.lower() in QUALITY_KEYS:
            num = to_num(value)
            if num is not None:
                return num * 10 if num <= 10 else num
    return None


def collect_website(combined, item):
    site = first_value(combined, WEBSITE_KEYS)
    if site:
        return site
    for key, value in walk(item):
        if key.lower() in WEBSITE_KEYS and isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def credible_source(source):
    """A source is credible if it is a real page URL and not a search results page."""
    url = normalize_url(source)
    if not url:
        return False
    return not is_search_url(url)


def collect_email_entries(combined, item):
    """Return [{'email','source'}]. Plain strings get an empty source."""
    default_source = first_value(combined, ["email_source", "email_source_url", "emails_source"])
    entries = []

    def add(value, source=""):
        if isinstance(value, str):
            for match in EMAIL_RE.findall(value):
                entries.append({"email": match, "source": source or default_source})
        elif isinstance(value, dict):
            raw = first_value(value, ["email", "address", "value"])
            src = first_value(value, ["source", "source_url", "url", "found_on", "page"])
            if raw:
                add(raw, src or default_source)
        elif isinstance(value, list):
            for v in value:
                add(v, source)

    for name in ("emails", "public_emails", "verified_public_emails", "email_candidates"):
        add(combined.get(name))
    for name in ("public_email", "business_email", "email", "contact_email"):
        add(combined.get(name))
    return entries


def collect_phones(combined):
    phones = []
    raw = combined.get("phones")
    if isinstance(raw, list):
        phones.extend(norm(p) for p in raw if isinstance(p, (str, int)) and norm(p))
    single = first_value(combined, ["phone", "phone_number", "telephone",
                                    "nationalPhoneNumber", "national_phone_number"])
    if single:
        phones.append(single)
    good = []
    for p in phones:
        digits = re.sub(r"\D", "", p)
        if len(digits) >= 10 and PHONE_RE.search(p):
            good.append(p)
    return list(dict.fromkeys(good))


PLACE_ID_RE = re.compile(r"^(?:places/)?(ChIJ[\w-]{10,})$")


def find_place_id(combined, item):
    """Find a real Google place id anywhere in the record (ChIJ... format)."""
    for name in ("place_id", "placeId", "google_place_id", "id", "record_id"):
        value = first_value(combined, [name])
        m = PLACE_ID_RE.match(value)
        if m:
            return m.group(1)
    for _key, value in walk(item):
        if isinstance(value, str):
            m = PLACE_ID_RE.match(value.strip())
            if m:
                return m.group(1)
    return ""


def business_fields(item):
    if not isinstance(item, dict):
        item = {"business_name": str(item)}
    nested = item.get("business")
    if not isinstance(nested, dict):
        nested = item.get("Business")
    if not isinstance(nested, dict):
        nested = {}
    combined = {**nested, **item}

    name = first_value(combined, ["business_name", "name", "company", "company_name",
                                  "title", "displayName", "business"])
    if not name and isinstance(nested.get("displayName"), dict):
        name = norm(nested["displayName"].get("text"))

    google_place_id = find_place_id(combined, item)
    legacy_id = (first_value(combined, ["place_id", "placeId", "google_place_id"])
                 or first_value(combined, ["record_id", "id"]))

    types = combined.get("types") or combined.get("categories") or []
    if isinstance(types, str):
        types = [types]
    types = [norm(t).lower() for t in types if isinstance(t, str)]
    primary = first_value(combined, ["primary_type", "primaryType", "category", "type"]).lower()
    if primary:
        types.append(primary)

    reviews = to_num(first_value(combined, [
        "user_ratings_total", "userRatingCount", "review_count", "reviews_count",
        "total_reviews", "reviews"]))
    rating = to_num(first_value(combined, ["rating", "google_rating"]))

    prior_score = to_num(first_value(item, ["lead_score", "score", "priority_score"]))
    if prior_score is not None and prior_score <= 10:
        prior_score *= 10

    return {
        "record_id": first_value(item, ["record_id", "id"]),
        "business_name": norm(name),
        "city": norm(first_value(combined, ["city", "town", "locality"])),
        "state": norm(first_value(combined, ["state", "region"])) or "Colorado",
        "address": norm(first_value(combined, ["address", "formatted_address",
                                               "full_address", "street_address"])),
        "website": collect_website(combined, item),
        "phones": collect_phones(combined),
        "email_entries": collect_email_entries(combined, item),
        "place_id": legacy_id,
        "google_place_id": google_place_id,
        "types": list(dict.fromkeys(types)),
        "review_count": reviews,
        "rating": rating,
        "business_status": first_value(combined, ["business_status", "businessStatus"]).upper(),
        "prior_status": first_value(item, ["status"]),
        "prior_lead_score": prior_score,
        "prior_site_quality": collect_prior_quality(item),
        "prior_issue_strings": collect_issue_strings(item),
        "source_record": item,
    }


def identity_key(record):
    place_id = norm(record.get("place_id"))
    if place_id:
        return "place:" + place_id
    return ("biz:" + key_name(record.get("business_name")) + "|"
            + (key_name(record.get("address")) or key_name(record.get("city"))))


# --------------------------------------------------------------------------
# Exclusion logic
# --------------------------------------------------------------------------


def excluded_reason(record, name_counts):
    name = record["business_name"]
    if not name:
        return "Business name is missing."
    words = name_words(name)
    for brand in EXCLUDED_BRANDS:
        b = name_words(brand)
        if not b:
            continue
        if words == b or words.startswith(b + " ") or (len(b) >= 8 and b in words):
            return f"Chain / national brand / corporate: {brand}."
    lowered = norm(name).lower()
    for pattern in EXCLUDED_NAME_PATTERNS:
        if re.search(pattern, lowered):
            return "Name matches a poor-fit pattern (government, bank, school, franchise unit)."
    bad_types = EXCLUDED_TYPES.intersection(record["types"])
    if bad_types:
        return "Poor-fit business type: " + ", ".join(sorted(bad_types)) + "."
    if record["business_status"] in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
        return "Business is marked closed: " + record["business_status"] + "."
    if name_counts.get(key_name(name), 0) >= 3:
        return "Same business name appears 3+ times in the queue (likely a chain or franchise)."
    reviews = record["review_count"]
    if reviews is not None and reviews >= 1500:
        return f"{int(reviews)} Google reviews: too large for a small-agency project."
    return ""


# --------------------------------------------------------------------------
# Google Places (optional) - only used to CONFIRM missing / social-only websites
# --------------------------------------------------------------------------


def places_lookup(place_id):
    """Return {'ok': bool, 'website': str, 'business_status': str, 'error': str}."""
    if requests is None:
        return {"ok": False, "error": "requests not installed"}
    if not PLACES_KEY:
        return {"ok": False, "error": "no Google Places key"}
    try:
        response = requests.get(
            "https://places.googleapis.com/v1/places/" + place_id,
            headers={
                "X-Goog-Api-Key": PLACES_KEY,
                "X-Goog-FieldMask": "id,displayName,formattedAddress,websiteUri,"
                                    "nationalPhoneNumber,businessStatus",
            },
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            return {"ok": False, "error": f"Places HTTP {response.status_code}"}
        data = response.json()
        return {
            "ok": True,
            "website": normalize_url(data.get("websiteUri", "")),
            "business_status": data.get("businessStatus", ""),
            "phone": data.get("nationalPhoneNumber", ""),
            "checked": datetime.now().isoformat(timespec="seconds"),
        }
    except (requests.RequestException, ValueError) as exc:
        return {"ok": False, "error": type(exc).__name__}


# --------------------------------------------------------------------------
# Optional live site probe (objective facts only)
# --------------------------------------------------------------------------


class PageProbe(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.in_title = False
        self.skip = 0
        self.text = []
        self.viewport = False
        self.tags = Counter()
        self.has_tel = False
        self.has_mailto = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = {k.lower(): (v or "") for k, v in attrs}
        self.tags[tag] += 1
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        elif tag == "title":
            self.in_title = True
        elif tag == "meta" and a.get("name", "").lower() == "viewport":
            self.viewport = True
        elif tag == "a":
            href = a.get("href", "").lower()
            self.has_tel = self.has_tel or href.startswith("tel:")
            self.has_mailto = self.has_mailto or href.startswith("mailto:")
        elif tag == "embed" and "flash" in a.get("type", "").lower():
            self.tags["flash"] += 1

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style", "noscript") and self.skip:
            self.skip -= 1
        elif tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self.text.append(data)


def probe_site(url):
    """Return {'reachable': bool, 'conclusive': bool, 'note': str, 'issues': [...]}"""
    if requests is None:
        return {"reachable": False, "conclusive": False, "note": "requests not installed", "issues": []}
    attempts = [url]
    if url.startswith("https://"):
        attempts.append("http://" + url[len("https://"):])
    note = ""
    for candidate in attempts:
        try:
            response = requests.get(candidate, timeout=REQUEST_TIMEOUT, allow_redirects=True,
                                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                             "AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
                                             "Accept": "text/html,*/*;q=0.8"})
        except requests.exceptions.SSLError:
            note = "TLS failure (inconclusive)"
            continue
        except requests.exceptions.Timeout:
            note = "timeout (inconclusive)"
            continue
        except requests.RequestException as exc:
            note = type(exc).__name__ + " (inconclusive)"
            continue
        if response.status_code >= 400:
            note = f"HTTP {response.status_code} (inconclusive: blocked or bot-protected)"
            continue
        ctype = response.headers.get("Content-Type", "").lower()
        if "html" not in ctype and "<html" not in response.text[:1500].lower():
            note = "non-HTML response (inconclusive)"
            continue
        return analyze_html(response.text[:1500000], response.url, candidate != url)
    return {"reachable": False, "conclusive": False, "note": note or "unreachable (inconclusive)",
            "issues": []}


def analyze_html(text, final_url, downgraded):
    probe = PageProbe()
    try:
        probe.feed(text)
    except Exception:
        return {"reachable": True, "conclusive": False, "note": "HTML parse error", "issues": []}
    visible = norm(" ".join(probe.text))
    title = norm(probe.title)
    issues = []

    def add(cat, evidence):
        issues.append({"category": cat, "evidence": evidence, "source": "live_check"})

    if urlparse(final_url).scheme == "http" or downgraded:
        add("security", "Site only loads over plain HTTP (no working HTTPS).")
    if not probe.viewport:
        add("mobile", "No viewport meta tag; page is not set up for mobile screens.")
    flash_like = probe.tags["marquee"] + probe.tags["blink"] + probe.tags["frameset"] + probe.tags["flash"]
    if flash_like or probe.tags["font"] >= 5:
        add("outdated", "Uses obsolete markup (marquee/frameset/flash/font tags).")
    if probe.tags["table"] >= 8 and probe.tags["div"] < 5:
        add("outdated", "Layout built from nested tables instead of modern CSS.")
    years = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-\u2013]\s*)?((?:19|20)\d{2})",
                                        visible, re.I)]
    if years and max(years) <= THIS_YEAR - 4:
        add("outdated", f"Footer copyright year is {max(years)}, suggesting the site is not maintained.")
    if len(visible) >= 400 and not (probe.has_tel or probe.has_mailto or probe.tags["form"]
                                    or PHONE_RE.search(visible) or EMAIL_RE.search(visible)):
        add("contact", "No phone number, email, or contact form found on the homepage.")
    return {"reachable": True, "conclusive": True, "note": "fetched OK", "issues": issues,
            "final_url": final_url, "title": title[:200], "text_chars": len(visible)}


# --------------------------------------------------------------------------
# Issue handling
# --------------------------------------------------------------------------


def categorize_prior_issues(strings):
    """Map free-text issues from the previous agent to distinct categories."""
    out = {}
    for s in strings:
        low = s.lower()
        if len(low) < 8:
            continue
        positive = any(h in low for h in POSITIVE_HINTS)
        negated = any(n in low for n in NEGATORS)
        if positive and not negated:
            continue
        for cat, words in ISSUE_CATEGORIES.items():
            if cat not in out and any(w in low for w in words):
                out[cat] = {"category": cat, "evidence": s[:240], "source": "prior_agent"}
                break
    return list(out.values())


def merge_issues(*lists):
    merged = {}
    for lst in lists:
        for item in lst:
            merged.setdefault(item["category"], item)
    return list(merged.values())


# --------------------------------------------------------------------------
# Email logic
# --------------------------------------------------------------------------


def clean_email(email):
    email = html.unescape(norm(email)).strip(".,;:()[]{}<>\"' ").lower()
    if not EMAIL_RE.fullmatch(email):
        return ""
    if any(b in email for b in BAD_EMAIL_BITS) or email.endswith(BAD_EMAIL_SUFFIX):
        return ""
    return email


def verified_emails(entries, website):
    """Emails that are real, present in the record, and credibly grounded."""
    site_host = host_of(website) if website else ""
    good, unverified = [], []
    seen = set()
    for entry in entries:
        email = clean_email(entry.get("email", ""))
        if not email or email in seen:
            continue
        seen.add(email)
        domain = email.rsplit("@", 1)[-1]
        source = entry.get("source", "")
        if credible_source(source):
            good.append({"email": email, "basis": "credible_source", "source": normalize_url(source)})
        elif site_host and not is_directory_url("https://" + site_host) and (
                domain == site_host or domain.endswith("." + site_host)):
            good.append({"email": email, "basis": "matches_official_website_domain", "source": website})
        else:
            unverified.append(email)
    return good, unverified


# --------------------------------------------------------------------------
# Email discovery (only addresses actually printed on a credible page)
# --------------------------------------------------------------------------

EMAIL_SKIP_DOMAINS = (
    "booksy.com", "vagaro.com", "squarespace.com", "wix.com", "wixpress.com",
    "weebly.com", "godaddy.com", "mindbodyonline.com", "styleseat.com",
    "schedulicity.com", "sentry.io", "yelp.com", "facebook.com", "google.com",
    "gstatic.com", "w3.org", "schema.org", "jquery.com", "bootstrapcdn.com",
    "wordpress.com", "wordpress.org", "shopify.com", "mailchimp.com",
    "constantcontact.com", "godaddysites.com", "secureserver.net",
)
NAME_STOPWORDS = {"the", "and", "llc", "inc", "corp", "company", "co", "services",
                  "service", "shop", "salon", "spa", "studio", "of", "for"}
COMMON_TLDS = {"com", "net", "org", "biz", "info", "us", "co", "io", "pro", "me",
               "email", "company", "services", "tech", "shop", "online", "site"}
OBF_AT = re.compile(r"\s*[\[\(\{]\s*at\s*[\]\)\}]\s*", re.I)
OBF_DOT = re.compile(r"\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*", re.I)
SEARCH_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def decode_cfemail(hexstr):
    """Decode Cloudflare's data-cfemail obfuscation."""
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))
    except (ValueError, IndexError):
        return ""


class EmailHarvester(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.strong = set()      # mailto links, Cloudflare-encoded, JSON-LD
        self.links = []          # (href, label)
        self.text = []
        self.ld = []
        self._skip = 0
        self._ld_block = False
        self._href = None
        self._label = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("script", "style", "noscript"):
            self._skip += 1
            self._ld_block = tag == "script" and "ld+json" in a.get("type", "").lower()
        if a.get("data-cfemail"):
            decoded = decode_cfemail(a["data-cfemail"])
            if decoded:
                self.strong.add(decoded)
        if tag == "a":
            href = a.get("href", "").strip()
            if href.lower().startswith("mailto:"):
                self.strong.add(unquote(href[7:].split("?")[0]))
            self._href = href
            self._label = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style", "noscript"):
            if self._skip:
                self._skip -= 1
            self._ld_block = False
        if tag == "a" and self._href is not None:
            self.links.append((self._href, norm(" ".join(self._label))))
            self._href = None

    def handle_data(self, data):
        if self._skip:
            if self._ld_block:
                self.ld.append(data)
            return
        self.text.append(data)
        if self._href is not None:
            self._label.append(data)


def harvest(html_text):
    """Return (emails, visible_text, links). Text-derived emails are held to a stricter TLD rule."""
    h = EmailHarvester()
    try:
        h.feed(html_text)
    except Exception:
        pass
    text = norm(" ".join(h.text))
    strong = set(h.strong)
    for blob in h.ld:
        strong.update(EMAIL_RE.findall(blob))
    deob = OBF_DOT.sub(".", OBF_AT.sub("@", text))
    weak = {e for e in EMAIL_RE.findall(deob)
            if e.rsplit(".", 1)[-1].lower() in COMMON_TLDS}
    out = set()
    for e in strong | weak:
        e = clean_email(e)
        if not e:
            continue
        if any(e.endswith("@" + d) or e.endswith("." + d) for d in EMAIL_SKIP_DOMAINS):
            continue
        out.add(e)
    return out, text, h.links


def fetch_html(url):
    """Return (html, final_url, error). Errors are inconclusive, never proof of anything."""
    if requests is None:
        return "", url, "requests unavailable"
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True,
                         headers={"User-Agent": SEARCH_UA, "Accept": "text/html,*/*;q=0.8",
                                  "Accept-Language": "en-US,en;q=0.9"})
    except requests.exceptions.SSLError:
        return "", url, "TLS failure"
    except requests.RequestException as exc:
        return "", url, type(exc).__name__
    if r.status_code >= 400:
        return "", url, f"HTTP {r.status_code}"
    return r.text[:1500000], r.url, ""


def unwrap_result_url(href):
    href = html.unescape(href or "").strip()
    if href.startswith("//"):
        href = "https:" + href
    if not href.lower().startswith("http"):
        return ""
    parsed = urlparse(href)
    host = (parsed.hostname or "").lower()
    qs = parse_qs(parsed.query)
    if host.endswith("duckduckgo.com"):
        target = qs.get("uddg", [""])[0]
        return normalize_url(unquote(target)) if target else ""
    if host.endswith("bing.com"):
        if parsed.path.startswith("/ck/"):
            u = qs.get("u", [""])[0]
            if u.startswith("a1"):
                try:
                    pad = "=" * (-len(u[2:]) % 4)
                    return normalize_url(base64.urlsafe_b64decode(u[2:] + pad).decode("utf-8", "ignore"))
                except (ValueError, UnicodeDecodeError):
                    return ""
        return ""
    return normalize_url(href)


def web_search_urls(query):
    """Result URLs from DuckDuckGo's HTML page, falling back to Bing. May return []."""
    for base in ("https://html.duckduckgo.com/html/?q=", "https://www.bing.com/search?q="):
        page, _final, err = fetch_html(base + quote(query))
        if err or not page:
            continue
        _e, _t, links = harvest(page)
        urls = []
        for href, _label in links:
            u = unwrap_result_url(href)
            if u and not is_directory_url(u) and not is_search_url(u):
                urls.append(u)
        urls = list(dict.fromkeys(urls))
        if urls:
            return urls
    return []


def name_token_ratio(name, text_lower):
    tokens = [t for t in name_words(name).split() if len(t) >= 3 and t not in NAME_STOPWORDS]
    if not tokens:
        return 0.0
    return sum(1 for t in tokens if t in text_lower) / len(tokens)


def discover_emails(record, website, social_site=""):
    """Look for public emails. Returns entries each carrying the page URL they were printed on."""
    entries, notes = [], []
    pages = 0
    searched = False
    seen = set()

    def take(emails, url, how):
        for e in sorted(emails):
            if e not in seen:
                seen.add(e)
                entries.append({"email": e, "source": url, "found_by": how})

    # A. Official website: homepage + contact/about pages
    official_host = ""
    if website:
        page, final, err = fetch_html(website)
        if err:
            notes.append(f"official site not readable: {err} (inconclusive)")
        else:
            pages += 1
            official_host = host_of(final)
            emails, _text, links = harvest(page)
            take(emails, final, "official_website")
            targets = []
            for href, label in links:
                url = normalize_url(urljoin(final, href)) if href else ""
                if url and host_of(url) == official_host and any(
                        w in (label + " " + url).lower()
                        for w in ("contact", "about", "team", "staff", "reach", "connect")):
                    targets.append(url)
            for path in ("/contact", "/contact-us", "/about", "/about-us"):
                targets.append(normalize_url(urljoin(final.rstrip("/") + "/", path.lstrip("/"))))
            visited, count = {normalize_url(final)}, 0
            for url in targets:
                if not url or url in visited or count >= 5:
                    continue
                visited.add(url)
                count += 1
                sub, sfinal, serr = fetch_html(url)
                time.sleep(PAUSE)
                if serr:
                    continue
                pages += 1
                em, _t, _l = harvest(sub)
                take(em, sfinal, "official_website")

    # B. Web search anchored on the business phone number + name
    if not entries:
        phone = record["phones"][0] if record["phones"] else ""
        phone_digits = re.sub(r"\D", "", phone)[-10:]
        queries = []
        if phone:
            queries.append(f'"{phone}" {record["business_name"]}')
        queries.append(f'"{record["business_name"]}" {record["city"]} email')
        fetched_hosts = {official_host} if official_host else set()
        for q in queries[:2]:
            urls = web_search_urls(q)
            time.sleep(PAUSE)
            if urls:
                searched = True
            else:
                notes.append("search returned no usable results (blocked or rate-limited?)")
            for url in urls[:6]:
                host = host_of(url)
                if not host or host in fetched_hosts:
                    continue
                fetched_hosts.add(host)
                page, final, err = fetch_html(url)
                time.sleep(PAUSE)
                if err:
                    continue
                pages += 1
                emails, text, _l = harvest(page)
                if not emails:
                    continue
                low = text.lower()
                digits = re.sub(r"\D", "", text)
                anchored = (bool(phone_digits) and phone_digits in digits
                            and name_token_ratio(record["business_name"], low) >= 0.5)
                if not phone_digits:
                    anchored = (name_token_ratio(record["business_name"], low) >= 0.75
                                and bool(record["city"]) and record["city"].lower() in low)
                if not anchored:
                    notes.append(f"{host}: has emails but page not tied to this business")
                    continue
                if len(emails) > 3:
                    notes.append(f"{host}: {len(emails)} emails on page (looks like a directory); skipped")
                    continue
                take(emails, final, "web_search_page_matching_phone_and_name")
            if entries:
                break
    if social_site and not entries:
        notes.append("social page listed as website was not scraped (blocks bots; check by hand)")

    return {"entries": entries, "pages_checked": pages, "notes": notes,
            "checked": datetime.now().isoformat(timespec="seconds"),
            "cacheable": pages > 0 or searched}


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def base_result(record):
    out = {k: v for k, v in record.items()
           if k not in ("email_entries", "prior_issue_strings", "phones")}
    out["phones"] = record["phones"]
    return out


def assess_prior_score(record, new_score, not_assessed=False):
    prior = record["prior_lead_score"]
    if not_assessed:
        return {"prior_lead_score": prior,
                "prior_score_verdict": "not comparable: site was never assessed"}
    if prior is None:
        return {"prior_lead_score": None, "prior_score_verdict": "no prior score"}
    delta = round(new_score - prior, 1)
    if abs(delta) <= 15:
        verdict = "prior score roughly holds up"
    elif delta < 0:
        verdict = "prior score was overstated"
    else:
        verdict = "prior score was understated"
    return {"prior_lead_score": prior, "score_delta": delta, "prior_score_verdict": verdict}


def evaluate(record, name_counts, cache, args):
    key = identity_key(record)
    result = base_result(record)
    result["identity_key"] = key
    result["evaluated_at"] = datetime.now().isoformat(timespec="seconds")

    def finish(status, reason, score, **extra):
        result.update({"status": status, "reason": reason, "new_score": int(max(0, min(100, score)))})
        result.update(extra)
        result.update(assess_prior_score(record, result["new_score"], extra.get("not_assessed", False)))
        return result

    # 1. Hard exclusions -------------------------------------------------
    why = excluded_reason(record, name_counts)
    if why:
        return finish("REJECTED", why, 0, tier="excluded", website_state="n/a",
                      website="", public_email="")

    # 2. Website state ---------------------------------------------------
    website = normalize_url(record["website"])
    cached = cache.get(key, {})
    places = cached.get("places")
    site_state = None
    method = "queue_record"
    social_site = ""

    if website and is_directory_url(website):
        social_site = website
        website = ""

    needs_places = (not website) and record["google_place_id"] and not args.no_places
    if needs_places and not (places and places.get("ok")):
        places = places_lookup(record["google_place_id"])
        time.sleep(PAUSE)
        cached["places"] = places
        cache[key] = cached

    result["places_error"] = ""
    if not record["google_place_id"]:
        result["places_error"] = "no Google place id (ChIJ...) found in record"
    elif places and not places.get("ok"):
        result["places_error"] = places.get("error", "lookup failed")
    if places and places.get("ok"):
        if places.get("business_status") in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
            return finish("REJECTED", "Google Places marks this business as closed.", 0,
                          tier="excluded", website_state="n/a", website="", public_email="")
        pw = places.get("website", "")
        if pw and not is_directory_url(pw):
            website, method = pw, "google_places"
        elif pw:
            social_site, website, method = pw, "", "google_places"
        if not website:
            site_state = "social_only_confirmed" if social_site else "no_website_confirmed"
    if not website and site_state is None:
        site_state = "social_only_unconfirmed" if social_site else "no_website_unconfirmed"
    if website:
        site_state = "has_website"

    # 3. Contact info ----------------------------------------------------
    emails, unverified, email = [], [], ""
    result["email_search"] = {"ran": False}

    def refresh_emails():
        nonlocal emails, unverified, email
        found_entries = (cached.get("emails") or {}).get("entries", [])
        emails, unverified = verified_emails(list(record["email_entries"]) + list(found_entries), website)
        email = emails[0]["email"] if emails else ""
        result.update({
            "website": website,
            "social_page_only": social_site,
            "website_state": site_state,
            "discovery_method": method,
            "public_email": email,
            "verified_public_emails": emails,
            "unverified_emails_ignored": unverified,
            "email_source": emails[0]["source"] if emails else "",
            "phones": record["phones"],
        })

    def ensure_emails():
        """Look for a public email once (cached). Only runs when none is verified yet."""
        if email or not getattr(args, "find_emails", False):
            return
        found = cached.get("emails")
        if not found:
            found = discover_emails(record, website, social_site)
            if found.get("cacheable"):
                cached["emails"] = found
                cache[key] = cached
        result["email_search"] = {"ran": True, "pages_checked": found.get("pages_checked", 0),
                                  "notes": found.get("notes", [])[:8]}
        refresh_emails()

    refresh_emails()
    has_phone = bool(record["phones"])

    if not has_phone and not email:
        return finish("REJECTED", "No phone number and no verified email: no way to contact them.", 0,
                      tier="no_contact_route")

    reviews = record["review_count"]
    activity_bonus = 3 if (reviews is not None and reviews >= 10) else 0
    activity_penalty = 10 if (reviews is not None and reviews < 3 and not record["rating"]) else 0

    # 4A. No real website: the top tier ----------------------------------
    if site_state != "has_website":
        ensure_emails()
        confirmed = site_state.endswith("_confirmed")
        base = 90 if site_state == "no_website_confirmed" else (86 if confirmed else 70)
        score = base + (4 if email else 0) + (3 if has_phone else 0) + activity_bonus - activity_penalty
        score = min(score, 100)
        channel = "email" if email else "phone"
        extra = {"tier": "NO_WEBSITE", "outreach_channel": channel,
                 "serious_issues": [], "evidence": [
                     {"category": "no_website",
                      "evidence": ("Google Places lists no website for this business." if site_state == "no_website_confirmed"
                                   else "Only a social/directory page is listed as the website: " + social_site
                                   if social_site else "No website recorded in queue; not yet confirmed with Google Places."),
                      "source": "google_places" if confirmed else "queue_record"}]}
        if not confirmed:
            if args.no_places:
                pe = "Places lookup was turned off"
            else:
                pe = result.get("places_error") or "no Places result"
            return finish("MANUAL_REVIEW",
                          f"No website on record, but not confirmed ({pe}). "
                          "Verify by hand before contacting.", score, **extra)
        if not email:
            return finish("MANUAL_REVIEW",
                          "Confirmed no real website, strong prospect, but no verified public email. "
                          "Phone/in-person outreach only; not approved for email.", score, **extra)
        if score >= MIN_SCORE:
            return finish("APPROVED", "Confirmed no real website and a verified public email.", score, **extra)
        return finish("MANUAL_REVIEW", "No website but score below threshold.", score, **extra)

    # 4B. Has a website: need evidenced problems -------------------------
    live = cached.get("site_check")
    if args.check_sites and not (live and live.get("conclusive")):
        live = probe_site(website)
        time.sleep(PAUSE)
        cached["site_check"] = live
        cache[key] = cached
    prior_issues = categorize_prior_issues(record["prior_issue_strings"])
    live_issues = live.get("issues", []) if live else []
    issues = merge_issues(prior_issues, live_issues)
    if len(issues) >= 2:
        ensure_emails()
    n = len(issues)
    quality = record["prior_site_quality"]
    result.update({
        "tier": "HAS_WEBSITE",
        "serious_issues": [i["evidence"] for i in issues],
        "evidence": issues,
        "issue_categories": [i["category"] for i in issues],
        "site_check": ({"note": live.get("note"), "conclusive": live.get("conclusive")} if live else None),
        "prior_site_quality": quality,
        "outreach_channel": "email" if email else "phone",
    })
    inconclusive = bool(live) and not live.get("conclusive")

    score = 20 + 15 * min(n, 4) + (10 if quality is not None and quality <= 40 else 0) \
        + (5 if email else 0) + activity_bonus - activity_penalty
    if quality is not None and quality >= 70:
        score -= 25
    if n < 2:
        score = min(score, 45)

    if n == 0 and quality is not None and quality >= 60:
        return finish("REJECTED", "Website looks fine (prior quality "
                      f"{int(quality)}) and no concrete problems are evidenced.", score)
    if n == 0:
        conclusive_clean = bool(live) and live.get("conclusive")
        if conclusive_clean:
            return finish("REJECTED", "Live check found no objective website problems. "
                          "Not redesigning a site just because it could be modernized.", score)
        why = ("Live check was blocked/inconclusive (not evidence of a bad site)." if inconclusive
               else "Website was never assessed (no prior issues or score in the queue); "
                    "run with --check-sites.")
        return finish("MANUAL_REVIEW", why, score, not_assessed=True)
    if n == 1:
        return finish("MANUAL_REVIEW", "Only one evidenced problem category; not enough for a redesign pitch.", score)
    if quality is not None and quality >= 70:
        return finish("MANUAL_REVIEW", "Problems found, but prior agent rated the site 70+; conflicting evidence.", score)
    if not email:
        unv = " An unverified email was ignored." if unverified else ""
        return finish("MANUAL_REVIEW", "Evidenced problems, but no verified public email." + unv, score)
    if score >= MIN_SCORE:
        return finish("APPROVED", f"{n} evidenced problem categories ({', '.join(result['issue_categories'])}) "
                                  "and a verified public email.", score)
    return finish("MANUAL_REVIEW", "Problems evidenced but below the approval score.", score)


# --------------------------------------------------------------------------
# Lead deck (HTML)
# --------------------------------------------------------------------------

CARDS_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Lead Deck</title>
<style>
:root{--bg:#f3f4f7;--card:#fff;--ink:#14161a;--mute:#6a7280;--line:#e3e6ec;--green:#0f7a58;--greenbg:#dff5ea;--amber:#8f5500;--amberbg:#ffefcc;--blue:#2457f5;--bluebg:#e6edff;--red:#c0392b;--shadow:0 12px 32px rgba(20,22,26,.14)}
@media(prefers-color-scheme:dark){:root{--bg:#0e1013;--card:#191c22;--ink:#f1f3f6;--mute:#98a1b0;--line:#292e38;--green:#47d19f;--greenbg:#12372b;--amber:#f2b84b;--amberbg:#3a2c10;--blue:#7da0ff;--bluebg:#1a2547;--red:#ff7b6b;--shadow:0 12px 32px rgba(0,0,0,.55)}}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--bg);padding:12px 16px 8px;border-bottom:1px solid var(--line)}
.top{display:flex;align-items:center;justify-content:space-between;gap:12px;max-width:560px;margin:0 auto}
h1{font-size:18px;margin:0;letter-spacing:-.01em}
.count{color:var(--mute);font-size:13px}
.tabs{display:flex;gap:6px;max-width:560px;margin:10px auto 0}
.tab{flex:1;padding:8px 6px;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:10px;font:inherit;font-size:14px;cursor:pointer}
.tab.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.chips{display:flex;gap:6px;overflow-x:auto;max-width:560px;margin:10px auto 0;padding-bottom:4px;scrollbar-width:none}
.chips::-webkit-scrollbar{display:none}
.chip{flex:none;padding:5px 11px;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;font:inherit;font-size:13px;cursor:pointer}
.chip.on{background:var(--blue);border-color:var(--blue);color:#fff}
main{padding:16px;max-width:560px;margin:0 auto}
.stack{position:relative;height:calc(100dvh - 300px);min-height:430px;max-height:700px;max-width:440px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:22px;box-shadow:var(--shadow);padding:18px;overflow-y:auto;position:relative}
.stack .card{position:absolute;inset:0;touch-action:pan-y;user-select:none;-webkit-user-select:none;will-change:transform}
.stack .card.under{transform:scale(.95) translateY(14px);opacity:.7;pointer-events:none}
.feed .card{margin-bottom:16px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.between{justify-content:space-between;align-items:flex-start}
.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600}
.pill.nosite{background:var(--greenbg);color:var(--green)}
.pill.issues{background:var(--bluebg);color:var(--blue)}
.pill.ready{background:var(--greenbg);color:var(--green)}
.pill.verify{background:var(--amberbg);color:var(--amber)}
.score{width:46px;height:46px;border-radius:50%;display:grid;place-items:center;font-weight:700;font-size:15px;border:3px solid var(--green);color:var(--green);flex:none}
.score.mid{border-color:var(--amber);color:var(--amber)}
h2{margin:12px 0 2px;font-size:24px;line-height:1.15;letter-spacing:-.02em}
.sub{color:var(--mute);font-size:14px}
.why{margin:14px 0 6px;padding:11px 12px;border-radius:12px;background:var(--bg);font-size:14px}
.why b{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin-bottom:3px}
ul.ev{margin:6px 0 0;padding-left:18px;font-size:14px}
.contact{display:flex;flex-direction:column;gap:8px;margin:14px 0 8px}
.btn{display:block;text-align:center;text-decoration:none;padding:12px 14px;border-radius:12px;font-weight:600;font-size:15px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer;font-family:inherit}
.btn.call{background:var(--green);border-color:var(--green);color:#fff}
.btn.mail{background:var(--blue);border-color:var(--blue);color:#fff}
.btn.small{padding:8px 10px;font-size:13px;font-weight:500}
.note{font-size:13px;color:var(--mute)}
.note a{color:var(--blue)}
.meta{margin-top:10px;font-size:12px;color:var(--mute);line-height:1.5}
.meta a{color:var(--blue)}
.stamp{position:absolute;top:22px;padding:4px 12px;border:3px solid;border-radius:8px;font-weight:800;font-size:22px;letter-spacing:.06em;opacity:0;pointer-events:none;transform:rotate(-12deg)}
.stamp.yes{left:18px;color:var(--green);border-color:var(--green)}
.stamp.no{right:18px;color:var(--red);border-color:var(--red);transform:rotate(12deg)}
.actions{display:flex;justify-content:center;gap:18px;margin-top:18px}
.round{width:62px;height:62px;border-radius:50%;border:1px solid var(--line);background:var(--card);box-shadow:var(--shadow);font-size:26px;cursor:pointer;color:var(--ink)}
.round.no{color:var(--red)}.round.yes{color:var(--green)}
.round.undo{width:46px;height:46px;font-size:18px;align-self:center}
.hint{text-align:center;color:var(--mute);font-size:12px;margin-top:10px}
.empty{text-align:center;color:var(--mute);padding:60px 20px}
.feedactions{display:flex;gap:8px;margin-top:6px}
.feedactions .btn{flex:1}
.tag{font-size:12px;font-weight:700;color:var(--green)}
.bar{display:flex;gap:8px;margin-bottom:12px}
</style>
</head>
<body>
<header>
  <div class="top"><h1>Lead Deck</h1><div class="count" id="count"></div></div>
  <div class="tabs" id="tabs"></div>
  <div class="chips" id="chips"></div>
</header>
<main id="main"></main>
<script>
(function(){
var LEADS = __DATA__;
var KEY = "lead_guillotine_decisions_v1";
var decisions = {};
try { decisions = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (e) { decisions = {}; }
function persist(){ try { localStorage.setItem(KEY, JSON.stringify(decisions)); } catch (e) {} }
var history = [], view = "swipe", filter = "all", busy = false;

function h(tag, cls, text){
  var e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}
function safeUrl(u){ return /^https?:\/\//i.test(u || "") ? u : ""; }
function digits(p){ return String(p || "").replace(/\D/g, ""); }
function link(href, label, cls){
  var a = h("a", cls, label); a.href = href;
  if (/^https?:/i.test(href)) { a.target = "_blank"; a.rel = "noopener noreferrer"; }
  return a;
}

var FILTERS = [
  ["all", "All", function(){ return true; }],
  ["email", "Email ready", function(l){ return !!l.email; }],
  ["phone", "Phone only", function(l){ return !l.email; }],
  ["nosite", "No website", function(l){ return l.tier === "NO_WEBSITE"; }],
  ["issues", "Site problems", function(l){ return l.tier === "HAS_WEBSITE"; }],
  ["verify", "Verify first", function(l){ return l.status !== "APPROVED"; }]
];
function inFilter(l){
  for (var i = 0; i < FILTERS.length; i++) if (FILTERS[i][0] === filter) return FILTERS[i][2](l);
  return true;
}
function pool(){ return LEADS.filter(inFilter); }
function pending(){ return pool().filter(function(l){ return !decisions[l.id]; }); }
function pursued(){ return LEADS.filter(function(l){ return decisions[l.id] === "pursue" || decisions[l.id] === "contacted"; }); }

function buildCard(l, mode){
  var card = h("div", "card");
  var top = h("div", "row between");
  var chips = h("div", "row");
  chips.appendChild(h("span", "pill " + (l.tier === "NO_WEBSITE" ? "nosite" : "issues"),
    l.tier === "NO_WEBSITE" ? "NO WEBSITE" : "SITE PROBLEMS"));
  chips.appendChild(h("span", "pill " + (l.status === "APPROVED" ? "ready" : "verify"),
    l.status === "APPROVED" ? "READY" : "VERIFY FIRST"));
  top.appendChild(chips);
  top.appendChild(h("div", "score" + (l.score >= 80 ? "" : " mid"), String(l.score)));
  card.appendChild(top);

  card.appendChild(h("h2", "", l.name));
  var bits = [];
  if (l.category) bits.push(l.category);
  if (l.city) bits.push(l.city);
  var sub = bits.join(" · ");
  if (l.rating) sub += (sub ? " · " : "") + "★ " + l.rating + (l.reviews ? " (" + l.reviews + ")" : "");
  card.appendChild(h("div", "sub", sub));

  var why = h("div", "why");
  why.appendChild(h("b", "", "Why this lead"));
  why.appendChild(h("span", "", l.reason));
  if (l.evidence && l.evidence.length) {
    var ul = h("ul", "ev");
    l.evidence.slice(0, 5).forEach(function(ev){ ul.appendChild(h("li", "", ev)); });
    why.appendChild(ul);
  }
  card.appendChild(why);

  var contact = h("div", "contact");
  l.phones.forEach(function(p){ contact.appendChild(link("tel:" + digits(p), "📞  Call " + p, "btn call")); });
  if (l.email) {
    contact.appendChild(link("mailto:" + l.email, "✉️  Email " + l.email, "btn mail"));
    var src = safeUrl(l.email_source);
    if (src) {
      var n = h("div", "note", "Found on: ");
      n.appendChild(link(src, src.replace(/^https?:\/\/(www\.)?/i, "").slice(0, 60)));
      contact.appendChild(n);
    }
  } else {
    contact.appendChild(h("div", "note", "No verified public email — phone outreach."));
  }
  card.appendChild(contact);

  var meta = h("div", "meta");
  if (l.address) meta.appendChild(h("div", "", l.address));
  var site = safeUrl(l.website), soc = safeUrl(l.social);
  var line = h("div", "");
  if (site) { line.appendChild(document.createTextNode("Website: ")); line.appendChild(link(site, site.replace(/^https?:\/\/(www\.)?/i, ""))); }
  else if (soc) { line.appendChild(document.createTextNode("Only a social page: ")); line.appendChild(link(soc, soc.replace(/^https?:\/\/(www\.)?/i, ""))); }
  else line.appendChild(document.createTextNode(l.site_state === "no_website_confirmed" ? "Google lists no website (confirmed)" : "No website on record (not confirmed)"));
  meta.appendChild(line);
  var maps = safeUrl(l.maps);
  if (maps) { var m = h("div", ""); m.appendChild(link(maps, "Open in Google Maps")); meta.appendChild(m); }
  if (l.prior !== null && l.prior !== undefined) meta.appendChild(h("div", "", "Old score " + l.prior + " → new score " + l.score));
  card.appendChild(meta);

  if (mode === "swipe") {
    var y = h("div", "stamp yes", "PURSUE"), nn = h("div", "stamp no", "SKIP");
    card.appendChild(y); card.appendChild(nn);
    card._yes = y; card._no = nn;
  }
  return card;
}

function decide(id, val, card){
  if (busy) return;
  busy = true;
  var dir = val === "pursue" ? 1 : -1;
  if (card) {
    card.style.transition = "transform .25s ease, opacity .25s ease";
    card.style.transform = "translateX(" + (dir * (window.innerWidth + 200)) + "px) rotate(" + (dir * 24) + "deg)";
    card.style.opacity = "0";
  }
  setTimeout(function(){
    decisions[id] = val; persist(); history.push(id); busy = false; render();
  }, card ? 230 : 0);
}
function undo(){
  var id = history.pop();
  if (!id) return;
  delete decisions[id]; persist(); render();
}

function attachDrag(card, id){
  var sx = 0, dx = 0, drag = false;
  card.addEventListener("pointerdown", function(e){
    if (busy || e.target.closest("a,button")) return;
    drag = true; sx = e.clientX; dx = 0;
    try { card.setPointerCapture(e.pointerId); } catch (x) {}
    card.style.transition = "none";
  });
  card.addEventListener("pointermove", function(e){
    if (!drag) return;
    dx = e.clientX - sx;
    card.style.transform = "translateX(" + dx + "px) rotate(" + (dx / 18) + "deg)";
    var p = Math.min(Math.abs(dx) / 110, 1);
    card._yes.style.opacity = dx > 0 ? p : 0;
    card._no.style.opacity = dx < 0 ? p : 0;
  });
  function end(){
    if (!drag) return;
    drag = false;
    if (Math.abs(dx) > 110) { decide(id, dx > 0 ? "pursue" : "skip", card); }
    else {
      card.style.transition = "transform .2s ease";
      card.style.transform = ""; card._yes.style.opacity = 0; card._no.style.opacity = 0;
    }
  }
  card.addEventListener("pointerup", end);
  card.addEventListener("pointercancel", end);
}

function renderSwipe(main){
  var list = pending();
  if (!list.length) {
    main.appendChild(h("div", "empty", pool().length ? "You're through this stack. Check the Pursued tab, or change the filter." : "No leads match this filter."));
    return;
  }
  var stack = h("div", "stack");
  if (list[1]) { var under = buildCard(list[1], "under"); under.className += " under"; stack.appendChild(under); }
  var top = buildCard(list[0], "swipe");
  attachDrag(top, list[0].id);
  stack.appendChild(top);
  main.appendChild(stack);
  var act = h("div", "actions");
  var no = h("button", "round no", "✕"); no.title = "Skip (left arrow)";
  var un = h("button", "round undo", "↶"); un.title = "Undo";
  var yes = h("button", "round yes", "♥"); yes.title = "Pursue (right arrow)";
  no.onclick = function(){ decide(list[0].id, "skip", top); };
  yes.onclick = function(){ decide(list[0].id, "pursue", top); };
  un.onclick = undo;
  act.appendChild(no); act.appendChild(un); act.appendChild(yes);
  main.appendChild(act);
  main.appendChild(h("div", "hint", "Swipe right to pursue · left to skip · arrow keys work too"));
}

function renderFeed(main){
  var list = pool().filter(function(l){ return decisions[l.id] !== "skip"; });
  if (!list.length) { main.appendChild(h("div", "empty", "Nothing here.")); return; }
  var feed = h("div", "feed");
  list.forEach(function(l){
    var card = buildCard(l, "feed");
    var bar = h("div", "feedactions");
    var state = decisions[l.id];
    if (state) {
      card.appendChild(h("div", "tag", state === "contacted" ? "✓ Contacted" : "♥ Pursuing"));
    }
    var skip = h("button", "btn small", "Skip");
    skip.onclick = function(){ decisions[l.id] = "skip"; persist(); render(); };
    var pur = h("button", "btn small", state ? "Un-pursue" : "♥ Pursue");
    pur.onclick = function(){ if (state) delete decisions[l.id]; else decisions[l.id] = "pursue"; persist(); render(); };
    bar.appendChild(skip); bar.appendChild(pur);
    card.appendChild(bar);
    feed.appendChild(card);
  });
  main.appendChild(feed);
}

function csvCell(v){
  v = String(v == null ? "" : v);
  return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
}
function exportCsv(list){
  var rows = [["Business", "City", "Phone", "Email", "Website", "Status", "Score", "Contacted"]];
  list.forEach(function(l){
    rows.push([l.name, l.city, l.phones.join(" / "), l.email, l.website || l.social, l.status, l.score,
      decisions[l.id] === "contacted" ? "yes" : "no"]);
  });
  var text = rows.map(function(r){ return r.map(csvCell).join(","); }).join("\r\n");
  var blob = new Blob([text], { type: "text/csv" });
  var a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "pursued_leads.csv";
  document.body.appendChild(a); a.click(); a.remove();
}

function renderPursued(main){
  var list = pursued();
  if (!list.length) { main.appendChild(h("div", "empty", "Nothing pursued yet. Swipe right on a lead you want to go after.")); return; }
  var bar = h("div", "bar");
  var ex = h("button", "btn small", "Export CSV"); ex.onclick = function(){ exportCsv(list); };
  var rs = h("button", "btn small", "Reset all decisions");
  rs.onclick = function(){ if (confirm("Clear every pursue/skip decision?")) { decisions = {}; history = []; persist(); render(); } };
  bar.appendChild(ex); bar.appendChild(rs);
  main.appendChild(bar);
  var feed = h("div", "feed");
  list.forEach(function(l){
    var card = buildCard(l, "feed");
    var done = decisions[l.id] === "contacted";
    var row = h("div", "feedactions");
    var c = h("button", "btn small", done ? "✓ Contacted (undo)" : "Mark contacted");
    c.onclick = function(){ decisions[l.id] = done ? "pursue" : "contacted"; persist(); render(); };
    var back = h("button", "btn small", "Remove");
    back.onclick = function(){ delete decisions[l.id]; persist(); render(); };
    row.appendChild(c); row.appendChild(back);
    card.appendChild(row);
    feed.appendChild(card);
  });
  main.appendChild(feed);
}

function render(){
  var tabs = $("#tabs"); tabs.textContent = "";
  [["swipe", "Swipe"], ["feed", "Scroll"], ["pursued", "Pursued (" + pursued().length + ")"]].forEach(function(t){
    var b = h("button", "tab" + (view === t[0] ? " on" : ""), t[1]);
    b.onclick = function(){ view = t[0]; render(); };
    tabs.appendChild(b);
  });
  var chips = $("#chips"); chips.textContent = "";
  FILTERS.forEach(function(f){
    var n = LEADS.filter(f[2]).length;
    var b = h("button", "chip" + (filter === f[0] ? " on" : ""), f[1] + " " + n);
    b.onclick = function(){ filter = f[0]; render(); };
    chips.appendChild(b);
  });
  $("#chips").style.display = view === "pursued" ? "none" : "flex";
  var main = $("#main"); main.textContent = "";
  $("#count").textContent = pending().length + " left · " + pursued().length + " pursued";
  if (view === "swipe") renderSwipe(main);
  else if (view === "feed") renderFeed(main);
  else renderPursued(main);
}
function $(s){ return document.querySelector(s); }

document.addEventListener("keydown", function(e){
  if (view !== "swipe" || busy) return;
  var list = pending(); if (!list.length) return;
  var top = document.querySelector(".stack .card:not(.under)");
  if (e.key === "ArrowRight") decide(list[0].id, "pursue", top);
  else if (e.key === "ArrowLeft") decide(list[0].id, "skip", top);
  else if (e.key === "z" || e.key === "Z") undo();
});
render();
})();
</script>
</body>
</html>
"""


def card_data(r):
    """Slim, display-ready view of one result for the lead deck."""
    src = r.get("source_record") if isinstance(r.get("source_record"), dict) else {}
    biz = src.get("business") if isinstance(src.get("business"), dict) else {}
    both = {**biz, **src}
    category = first_value(both, ["category", "discovered_category", "primary_type"]).replace("_", " ").strip()
    evidence = [norm(e.get("evidence", "")) for e in (r.get("evidence") or [])
                if isinstance(e, dict) and e.get("evidence")]
    rating = to_num(r.get("rating"))
    reviews = to_num(r.get("review_count"))
    prior = to_num(r.get("prior_lead_score"))
    return {
        "id": r.get("identity_key") or identity_key(r),
        "name": r.get("business_name", ""),
        "city": r.get("city", ""),
        "address": r.get("address", ""),
        "category": category,
        "tier": r.get("tier", ""),
        "status": r.get("status", ""),
        "score": int(r.get("new_score", 0) or 0),
        "prior": int(prior) if prior is not None else None,
        "reason": r.get("reason", ""),
        "evidence": evidence,
        "phones": r.get("phones", []) or [],
        "email": r.get("public_email", ""),
        "email_source": r.get("email_source", ""),
        "website": r.get("website", ""),
        "social": r.get("social_page_only", ""),
        "site_state": r.get("website_state", ""),
        "maps": first_value(both, ["maps_url", "googleMapsUri", "google_maps_url"]),
        "rating": round(rating, 1) if rating else None,
        "reviews": int(reviews) if reviews else None,
    }


def write_cards(results):
    """Write the self-contained swipe/scroll lead deck. Returns number of leads shown."""
    keep = [r for r in results
            if r.get("status") in ("APPROVED", "MANUAL_REVIEW")
            and (r.get("tier") == "NO_WEBSITE"
                 or (r.get("tier") == "HAS_WEBSITE" and len(r.get("issue_categories") or []) >= 2))
            and (r.get("public_email") or r.get("phones"))]
    keep.sort(key=sort_key)
    payload = json.dumps([card_data(r) for r in keep], ensure_ascii=False)
    payload = payload.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    page = CARDS_TEMPLATE.replace("__DATA__", payload, 1)
    temp = CARDS_FILE.with_suffix(".html.tmp")
    with open(temp, "w", encoding="utf-8") as f:
        f.write(page)
    temp.replace(CARDS_FILE)
    return len(keep)


# --------------------------------------------------------------------------
# State / main
# --------------------------------------------------------------------------


def load_cache():
    data = read_json(STATE_FILE)
    if isinstance(data, dict) and isinstance(data.get("lookups"), dict):
        return data["lookups"]
    return {}


def save_cache(cache):
    write_json(STATE_FILE, {"version": 1, "lookups": cache})


def old_state_website_hints():
    """Websites the previous version verified via Google Places. Read-only."""
    hints = {}
    data = read_json(OLD_STATE_FILE)
    if isinstance(data, list):
        for row in data:
            if (isinstance(row, dict) and row.get("website")
                    and str(row.get("discovery_method", "")).startswith("google_places")):
                hints[identity_key(row)] = row["website"]
    return hints


def sort_key(r):
    return (0 if r.get("tier") == "NO_WEBSITE" else 1, -r.get("new_score", 0),
            r.get("business_name", "").lower())


def main():
    ap = argparse.ArgumentParser(description="Strict filter / re-scorer for the outreach queue.")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--retry-all", action="store_true",
                    help="discard cached Places/site lookups for selected records and re-fetch")
    ap.add_argument("--input", type=str, default="")
    ap.add_argument("--no-places", action="store_true")
    ap.add_argument("--check-sites", action="store_true")
    ap.add_argument("--min-score", type=int, default=0)
    ap.add_argument("--no-email-search", action="store_true",
                    help="do not look for public emails on websites / via web search")
    ap.add_argument("--cards-only", action="store_true",
                    help="rebuild the lead deck (HTML) from the last results file, no lookups")
    ap.add_argument("--open", action="store_true", help="open the lead deck in your browser when done")
    ap.add_argument("--inspect", type=int, default=0,
                    help="print what the script extracted from the first N records, then exit")
    args = ap.parse_args()

    if args.cards_only:
        data = read_json(RESULTS_FILE)
        if not isinstance(data, list):
            raise SystemExit(f"No results yet at {RESULTS_FILE}. Run the script once first.")
        n = write_cards(data)
        log(f"[GUILLOTINE] Lead deck rebuilt with {n} leads: {CARDS_FILE}")
        if args.open:
            webbrowser.open(CARDS_FILE.as_uri())
        return
    args.find_emails = not args.no_email_search
    global MIN_SCORE
    if args.min_score > 0:
        MIN_SCORE = args.min_score

    if (not args.no_places or args.check_sites or args.find_emails) and requests is None:
        raise SystemExit("requests is unavailable. Use --no-places --no-email-search, "
                         "or check local_packages.")

    input_path = Path(args.input).resolve() if args.input else find_input_file()
    raw = read_json(input_path)
    if raw is None:
        raise SystemExit(f"Could not read JSON input: {input_path}")

    all_records = []
    for item in nested_candidates(raw):
        rec = business_fields(item)
        if rec["business_name"]:
            all_records.append(rec)

    name_counts = Counter(key_name(r["business_name"]) for r in all_records)

    hints = old_state_website_hints()
    candidates, seen = [], set()
    for rec in all_records:
        k = identity_key(rec)
        if k in seen:
            continue
        seen.add(k)
        if not rec["website"] and k in hints:
            rec["website"] = hints[k]
        candidates.append(rec)
    if args.limit > 0:
        candidates = candidates[:args.limit]

    if args.inspect > 0:
        for rec in candidates[:args.inspect]:
            src = rec["source_record"]
            log("=" * 70)
            log(rec["business_name"])
            log("  top-level keys : " + ", ".join(src.keys()) if isinstance(src, dict) else "")
            nb = src.get("business") if isinstance(src, dict) else None
            if isinstance(nb, dict):
                log("  business keys  : " + ", ".join(nb.keys()))
            log(f"  place id found : {rec['google_place_id'] or 'NONE'}")
            log(f"  record_id      : {rec['record_id']}")
            log(f"  website        : {rec['website'] or 'NONE'}")
            log(f"  phones         : {rec['phones']}")
            log(f"  emails         : {rec['email_entries']}")
            log(f"  prior score    : {rec['prior_lead_score']} | site quality: {rec['prior_site_quality']}")
            log(f"  prior issues   : {rec['prior_issue_strings'][:5]}")
        return

    cache = load_cache()
    if args.retry_all:
        for rec in candidates:
            cache.pop(identity_key(rec), None)

    log(f"[GUILLOTINE] Source (read-only): {input_path}")
    log(f"[GUILLOTINE] Records in file: {len(all_records)} | unique: {len(candidates)}")
    log(f"[GUILLOTINE] Places confirmation: {'off' if args.no_places else ('on' if PLACES_KEY else 'no key')}"
        f" | live site check: {'on' if args.check_sites else 'off'}"
        f" | email search: {'on' if args.find_emails else 'off'} | approve at >= {MIN_SCORE}")
    log("[GUILLOTINE] Email sending: DISABLED.\n")

    results = []
    for i, rec in enumerate(candidates, 1):
        try:
            res = evaluate(rec, name_counts, cache, args)
        except KeyboardInterrupt:
            log("\nInterrupted. Saving what is done.")
            break
        except Exception as exc:  # never lose the run over one bad record
            res = {**base_result(rec), "identity_key": identity_key(rec), "status": "MANUAL_REVIEW",
                   "reason": f"Unexpected error: {type(exc).__name__}: {exc}", "new_score": 0,
                   "tier": "error", "website": rec.get("website", ""), "public_email": ""}
        results.append(res)
        log(f"{i}/{len(candidates)} {rec['business_name'][:46]:46} {res['status']:13} "
            f"{res.get('new_score', 0):3} {res.get('website_state', '')}")
        log(f"      {res['reason']}")
        if i % 5 == 0:
            save_cache(cache)
    save_cache(cache)

    results.sort(key=sort_key)
    approved = [r for r in results if r["status"] == "APPROVED" and r.get("public_email")]
    review = [r for r in results if r["status"] == "MANUAL_REVIEW"]
    rejected = [r for r in results if r["status"] == "REJECTED"]
    no_site = [r for r in results if r.get("tier") == "NO_WEBSITE" and r["status"] != "REJECTED"]

    write_json(RESULTS_FILE, results)
    write_json(APPROVED_FILE, approved)
    write_json(REVIEW_FILE, review)
    write_json(REJECTED_FILE, rejected)
    write_json(NO_WEBSITE_FILE, no_site)

    overstated = [r for r in results if r.get("prior_score_verdict") == "prior score was overstated"]
    log("\n[GUILLOTINE] Finished.")
    log(f"  Approved: {len(approved)}  Manual review: {len(review)}  Rejected: {len(rejected)}")
    log(f"  No-website leads (priority list): {len(no_site)}")
    log(f"  Prior scores that were overstated: {len(overstated)}")
    log("  Top of the list:")
    for r in results[:10]:
        if r["status"] != "REJECTED":
            log(f"    {r['new_score']:3} {r['status']:13} {r['business_name'][:40]:40} {r.get('website_state', '')}")
    shown = write_cards(results)
    log(f"  Lead deck: {CARDS_FILE} ({shown} leads to swipe through)")
    if args.open:
        webbrowser.open(CARDS_FILE.as_uri())
    log(f"  Files: {RESULTS_FILE.name}, {APPROVED_FILE.name}, {REVIEW_FILE.name}, "
        f"{REJECTED_FILE.name}, {NO_WEBSITE_FILE.name}")


if __name__ == "__main__":
    main()