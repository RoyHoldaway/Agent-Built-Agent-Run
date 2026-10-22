#!/usr/bin/env python3
"""Job-hunt agent: free public job boards + a local Ollama model + an Excel report.

What it does
  1. Reads your resume(s) from the  resumes/  folder (PDF, DOCX or TXT).
  2. Figures out your CURRENT job title (local AI, with a regex fallback) and asks you to confirm it.
  3. Asks for your GOAL job title, experience level, location and minimum salary.
  4. Maps the goal to one or more career "tracks" (software, SOC, NOC, security eng/OpSec, data, ...)
     and, for goals it does not know, asks the local model for related titles.
  5. Pulls postings from Remotive, Greenhouse and Lever (free, no API keys), filters by title,
     level and location, scores each one against your resume(s), and writes jobhunt_results.xlsx.

No paid APIs. It does not log in anywhere or get around CAPTCHAs / access controls.
"""
from __future__ import annotations
import html, json, re, sys, time
from pathlib import Path

try:
    import requests
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
except ImportError as exc:
    print(f"Missing dependency: {exc.name}. Run:  python -m pip install -r requirements.txt")
    raise SystemExit(1)

# ----------------------------- SETTINGS ------------------------------
BASE_DIR = Path(__file__).resolve().parent
RESUME_DIR = BASE_DIR / "resumes"
OUTPUT_PATH = BASE_DIR / "jobhunt_results.xlsx"
PROFILE_PATH = BASE_DIR / "jobhunt_profile.json"   # remembers your answers between runs
OLLAMA_HOST = "http://localhost:11434"
OLLAMA_MODEL = "qwen3:8b"          # change to e.g. "qwen3:4b" if your PC is slow
MAX_JOBS_TO_SCORE = 80             # lower = faster; the AI scoring is the slow part
REQUEST_DELAY = 1.0

# Company boards. boards.greenhouse.io/NAME or jobs.lever.co/NAME. Dead names are skipped with a message.
GREENHOUSE_BOARDS = [
    "gitlab", "datadog", "cloudflare", "twilio", "okta", "databricks", "samsara", "gusto",
    "ibotta", "coinbase", "asana", "dropbox", "reddit", "mongodb", "elastic", "brex", "tailscale",
]
LEVER_BOARDS = ["palantir", "spotify"]

# ------------------------------ TRACKS -------------------------------
# match   = words in the user's GOAL title that select this track
# titles  = words a job TITLE must contain to be kept
# remotive = category slugs / search words for the Remotive API
# exclude = title words that are wrong for this track (dropped unless another chosen track wants them)
SUPPORT_NOISE = ["help desk", "service desk", "desktop support", "it support", "technical support"]
TRACKS = {
    "swe": {
        "label": "Software engineering",
        "match": ["software", "swe", "developer", "programmer", "backend", "back-end", "frontend", "front-end",
                  "full stack", "fullstack", "full-stack", "web", "application", "sde", "python", "java"],
        "titles": ["software engineer", "software developer", "application developer", "backend", "back-end",
                   "frontend", "front-end", "full stack", "full-stack", "fullstack", "python developer",
                   "java developer", "web developer", "sde", "programmer", "developer"],
        "remotive": ["category:software-dev"],
        "exclude": SUPPORT_NOISE + ["field technician", "network technician", "sales engineer"],
    },
    "qa": {
        "label": "QA / test automation",
        "match": ["qa", "quality", "test", "sdet"],
        "titles": ["qa", "quality assurance", "sdet", "test engineer", "test automation", "software test"],
        "remotive": ["category:qa"],
        "exclude": SUPPORT_NOISE,
    },
    "data": {
        "label": "Data",
        "match": ["data", "analytics", "bi ", "business intelligence"],
        "titles": ["data engineer", "data analyst", "analytics engineer", "business intelligence", "bi developer",
                   "data scientist"],
        "remotive": ["category:data"],
        "exclude": SUPPORT_NOISE,
    },
    "ml": {
        "label": "AI / machine learning",
        "match": ["machine learning", "ml", "ai", "llm", "deep learning", "computer vision", "nlp"],
        "titles": ["machine learning", "ml engineer", "ai engineer", "llm", "computer vision", "nlp",
                   "data scientist", "applied scientist"],
        "remotive": ["category:software-dev", "search:machine learning"],
        "exclude": SUPPORT_NOISE,
    },
    "devops": {
        "label": "DevOps / cloud / SRE",
        "match": ["devops", "sre", "reliability", "cloud", "platform", "infrastructure", "sysadmin",
                  "systems admin", "system admin", "systems engineer"],
        "titles": ["devops", "site reliability", "sre", "cloud engineer", "platform engineer",
                   "infrastructure engineer", "systems engineer", "build and release", "release engineer",
                   "systems administrator", "system administrator", "sysadmin"],
        "remotive": ["category:devops", "search:devops"],
        "exclude": SUPPORT_NOISE,
    },
    "soc": {
        "label": "Security operations (SOC)",
        "match": ["cyber", "security", "infosec", "soc", "incident", "threat", "blue team"],
        "titles": ["soc analyst", "security operations", "security analyst", "cybersecurity analyst",
                   "cyber security analyst", "information security analyst", "incident response",
                   "threat detection", "detection engineer", "threat analyst", "threat hunter", "mdr",
                   "cyber defense"],
        "remotive": ["search:security analyst", "search:soc"],
        "exclude": ["sales engineer", "account executive"],
    },
    "noc": {
        "label": "Network operations (NOC)",
        "match": ["cyber", "security", "noc", "network", "netops", "telecom"],
        "titles": ["noc", "network operations", "network engineer", "network administrator", "network analyst",
                   "network technician", "network support", "network operations center"],
        "remotive": ["search:noc", "search:network engineer"],
        "exclude": ["sales engineer", "account executive"],
    },
    "secops": {
        "label": "Security engineering / OpSec",
        "match": ["cyber", "security", "infosec", "opsec", "appsec", "pentest", "penetration", "red team",
                  "vulnerability", "hacker", "iam", "grc"],
        "titles": ["security engineer", "opsec", "operations security", "application security", "appsec",
                   "penetration", "pentest", "vulnerability", "security administrator", "cloud security",
                   "iam", "identity and access", "security automation", "devsecops", "red team",
                   "security specialist", "grc", "compliance analyst", "information assurance"],
        "remotive": ["search:security engineer", "search:application security"],
        "exclude": ["sales engineer", "account executive"],
    },
    "it": {
        "label": "IT / support / field tech",
        "match": ["it ", "help desk", "helpdesk", "support", "technician", "field", "desktop", "endpoint"],
        "titles": ["it support", "help desk", "service desk", "desktop support", "technical support",
                   "support engineer", "field technician", "field engineer", "it technician", "it specialist",
                   "endpoint", "systems technician", "it analyst"],
        "remotive": ["search:it support"],
        "exclude": ["sales engineer", "account executive"],
    },
}
ALWAYS_EXCLUDE = ["commission only", "unpaid", "volunteer", "internship", "intern", "account executive",
                  "business development"]

LEVELS = {
    "1": ("entry", "Entry level / new grad"),
    "2": ("mid", "Mid level (2-5 years)"),
    "3": ("senior", "Senior and up"),
}
SENIOR_RE = re.compile(r"\b(senior|sr\.?|staff|principal|lead|manager|director|head of|vp|architect|ii|iii|iv)\b", re.I)
MID_BLOCK_RE = re.compile(r"\b(staff|principal|director|head of|vp|manager|lead|architect)\b", re.I)
JUNIOR_RE = re.compile(r"\b(junior|jr\.?|entry|graduate|new grad|associate|intern|apprentice|trainee)\b", re.I)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
})


# ------------------------------ HELPERS ------------------------------
def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def strip_html(text: str) -> str:
    return clean_text(re.sub(r"<[^>]+>", " ", html.unescape(text or "")))


def term_in(term: str, text: str) -> bool:
    """Whole-word match (allows plural / -ing), so 'iam' != 'william' and 'intern' != 'internal'."""
    return bool(re.search(r"(?<![a-z])" + re.escape(term.strip().lower()) + r"(?:s|ing)?(?![a-z])", text.lower()))


def has_any(terms, text: str) -> bool:
    return any(term_in(t, text) for t in terms)


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        answer = ""
    return answer or default


# ------------------------------ OLLAMA -------------------------------
class Ollama:
    def __init__(self):
        self.enabled = False
        self.failures = 0

    def check(self) -> bool:
        try:
            tags = SESSION.get(f"{OLLAMA_HOST}/api/tags", timeout=5).json()
            names = [m.get("name", "") for m in tags.get("models", [])]
        except Exception:
            print("  Ollama is not running (or not installed). Continuing with the basic keyword scorer.")
            print("  See README.md, step 1, then re-run for AI-quality scoring.")
            return False
        if not any(n == OLLAMA_MODEL or n.startswith(OLLAMA_MODEL + ":") for n in names):
            print(f"  Ollama is running but model '{OLLAMA_MODEL}' is not downloaded. Run:  ollama pull {OLLAMA_MODEL}")
            return False
        self.enabled = True
        return True

    def json(self, prompt: str, timeout: int = 180) -> dict:
        """Ask the local model for a JSON object. Returns {} on any failure."""
        if not self.enabled:
            return {}
        try:
            r = SESSION.post(f"{OLLAMA_HOST}/api/generate", json={
                "model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "format": "json",
                "think": False, "options": {"temperature": 0.1}}, timeout=timeout)
            r.raise_for_status()
            data = json.loads(r.json().get("response", ""))
            self.failures = 0
            return data if isinstance(data, dict) else {}
        except Exception:
            self.failures += 1
            if self.failures >= 3:
                print("  Ollama failed 3 times in a row; switching to the keyword scorer for the rest of this run.")
                self.enabled = False
            return {}


AI = Ollama()


# ----------------------------- RESUMES -------------------------------
def read_resume_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".pdf":
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    if suffix == ".docx":
        from docx import Document
        return "\n".join(p.text for p in Document(str(path)).paragraphs)
    return ""


def load_resumes() -> list[tuple[str, str]]:
    RESUME_DIR.mkdir(exist_ok=True)
    paths = sorted(p for ext in ("*.pdf", "*.docx", "*.txt") for p in RESUME_DIR.glob(ext) if not p.name.startswith("~$"))
    resumes = []
    for path in paths:
        try:
            text = read_resume_file(path)
        except Exception as exc:
            print(f"  Could not read {path.name}: {exc}")
            continue
        if text.strip():
            resumes.append((text[:16000], str(path)))
            print(f"  Loaded resume: {path.name}")
        else:
            print(f"  No extractable text in {path.name} (scanned image PDF?). Export a text PDF or use .docx.")
    if not resumes:
        entered = ask("No resumes found in the 'resumes' folder. Full path to a resume file (Enter to skip)").strip('"')
        if entered:
            try:
                p = Path(entered).expanduser()
                text = read_resume_file(p)
                if text.strip():
                    resumes.append((text[:16000], str(p)))
            except Exception as exc:
                print(f"  Could not read that file: {exc}")
    if not resumes:
        print("  Continuing without a resume - scores will be based on titles only.")
        resumes.append(("", "(no resume)"))
    return resumes


TITLE_WORDS = r"(engineer|developer|analyst|technician|administrator|specialist|manager|architect|consultant|scientist|programmer|intern|coordinator|associate|lead|director|representative|tech)"


def guess_current_title(resume_text: str) -> str:
    """Local AI first; regex fallback. The user confirms either way."""
    head = resume_text[:5000]
    data = AI.json(
        "From this resume, return ONLY JSON like {\"current_title\": \"...\"} with the candidate's MOST RECENT or "
        "CURRENT job title (not a degree, not a skill, not a company). Use an empty string if unsure.\n\nRESUME:\n" + head,
        timeout=90)
    title = clean_text(str(data.get("current_title", "")))
    if title and len(title) < 80:
        return title
    # Fallback (no AI): find the first capitalized "<Words> Engineer/Analyst/Technician..." after the Experience header.
    flat = re.sub(r"\s+", " ", resume_text)
    kw = r"(?:Engineer|Developer|Analyst|Technician|Administrator|Specialist|Manager|Architect|Consultant|Scientist|Programmer|Coordinator|Representative|Associate|Tech)"
    pat = re.compile(r"((?:[A-Z][A-Za-z&/.+-]*\s){0,4}" + kw + r"(?:\s(?:I{1,3}|IV))?)\b")
    for hdr in re.finditer(r"\b(?:Work |Professional |Relevant )?(?:Experience|EXPERIENCE|Employment)\b", flat):
        m = pat.search(flat[hdr.end():hdr.end() + 200])
        if m:
            return m.group(1).strip()
    m = pat.search(flat)
    if m:
        return m.group(1).strip()
    return ""


# ------------------------------ PROFILE ------------------------------
def pick_tracks(goal: str) -> list[str]:
    g = " " + goal.lower() + " "
    hits = []
    for key, t in TRACKS.items():
        # Short words (qa, ml, ai, soc, it) must be whole words; longer ones match as prefixes (cyber -> cybersecurity).
        if any(re.search(r"(?<![a-z])" + re.escape(m.strip()) + (r"(?![a-z])" if len(m.strip()) <= 4 else ""), g) for m in t["match"]):
            hits.append(key)
    # A generic word like "analyst"/"network" should not flood the list when something more specific matched.
    return hits


def ai_related_titles(goal: str, level: str) -> list[str]:
    data = AI.json(
        f"A job seeker wants to become: \"{goal}\" ({level} level). Return ONLY JSON like "
        "{\"related_titles\": [\"...\"]} with 10 to 14 real job titles on job boards that are closely related "
        "(synonyms and neighboring roles), lowercase, no seniority words, no company names.", timeout=90)
    out = data.get("related_titles", [])
    return [clean_text(str(x)).lower() for x in out if isinstance(x, str) and 3 < len(x) < 60]


def build_profile(resumes: list[tuple[str, str]]) -> dict:
    saved = {}
    if PROFILE_PATH.exists():
        try:
            saved = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        except Exception:
            saved = {}
        if saved and ask("Reuse your answers from last time? (y/n)", "y").lower().startswith("y"):
            print(f"  Using saved profile: {saved.get('current_title') or '?'} -> {saved.get('goal_title')}")
            return saved

    print("\nStep 1 - your current job title")
    first_text = next((t for t, _ in resumes if t.strip()), "")
    guess = guess_current_title(first_text) if first_text else ""
    current = ask("I read your resume. Is this your current title? Press Enter to accept or type the right one", guess)

    print("\nStep 2 - what you want next")
    goal = ask("Goal job title (e.g. 'software engineer', 'SOC analyst', 'cybersecurity', 'data analyst')")
    while not goal:
        goal = ask("Please type a goal job title")
    print("  Experience level:  1) Entry level / new grad   2) Mid (2-5 yrs)   3) Senior+")
    level_key = ask("Pick 1, 2 or 3", "1")
    level, level_label = LEVELS.get(level_key, LEVELS["1"])

    print("\nStep 3 - where")
    print("  Examples: 'Austin, TX'   'Denver, CO; Boulder, CO'   leave blank for remote only")
    locations = [x.strip() for x in ask("Cities/states you'd work in (separate with ;)", "").split(";") if x.strip()]
    remote_ok = ask("Include remote jobs? (y/n)", "y").lower().startswith("y")

    sal = re.sub(r"[^\d]", "", ask("Minimum yearly salary in USD, blank for none", ""))
    min_salary = int(sal) if sal else 0

    tracks = pick_tracks(goal)
    extra_titles: list[str] = []
    if not tracks or AI.enabled:
        extra_titles = ai_related_titles(goal, level_label)
    if not tracks and not extra_titles:
        extra_titles = [goal.lower()]
    print("\nSearching these job families:")
    for k in tracks:
        print(f"  - {TRACKS[k]['label']}")
    if extra_titles:
        print(f"  - Related titles: {', '.join(extra_titles[:8])}{'...' if len(extra_titles) > 8 else ''}")

    profile = {
        "current_title": current, "goal_title": goal, "level": level, "level_label": level_label,
        "locations": locations, "remote_ok": remote_ok, "min_salary": min_salary,
        "tracks": tracks, "extra_titles": extra_titles,
    }
    PROFILE_PATH.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    return profile


def title_terms(profile: dict) -> tuple[list[str], list[str]]:
    good, bad = [], list(ALWAYS_EXCLUDE)
    for k in profile["tracks"]:
        good += TRACKS[k]["titles"]
        bad += TRACKS[k]["exclude"]
    good += profile["extra_titles"] + [profile["goal_title"].lower()]
    good = list(dict.fromkeys(g for g in good if g))
    # A term wanted by any chosen track/title is never excluded.
    bad = [b for b in dict.fromkeys(bad) if not any(b in g or g in b for g in good)]
    return good, bad


# -------------------------- JOB COLLECTION ---------------------------
def location_regex(profile: dict):
    parts = []
    for loc in profile["locations"]:
        bits = [b.strip() for b in loc.split(",") if b.strip()]
        if bits:
            parts.append(re.escape(bits[0].lower()))
        if len(bits) > 1 and len(bits[1]) == 2:
            parts.append(r",\s*" + re.escape(bits[1].lower()) + r"\b")
    return re.compile("|".join(parts), re.I) if parts else None


REMOTE_RE = re.compile(r"remote|anywhere|worldwide|united states|\busa?\b|\bu\.s\.", re.I)
US_RE = re.compile(r"worldwide|anywhere|united states|north america|americas|\busa?\b|\bu\.s\.", re.I)
# "Remote - Germany" style listings are usually not open to US applicants. This tool is US-focused.
FOREIGN_RE = re.compile(r"europe|emea|apac|\buk\b|united kingdom|germany|france|india|canada|brazil|latam|latin america|"
                        r"australia|ireland|poland|spain|netherlands|mexico|asia|africa|israel|singapore|japan|"
                        r"portugal|romania|ukraine|argentina|colombia|philippines|pakistan|nigeria", re.I)


def get_json(url: str, label: str):
    try:
        r = SESSION.get(url, timeout=25)
        if r.status_code != 200:
            print(f"  Skipped {label} (HTTP {r.status_code})")
            return None
        return r.json()
    except Exception as exc:
        print(f"  Skipped {label} ({type(exc).__name__})")
        return None


def jobs_remotive(queries: list[str]) -> list[dict]:
    out = []
    for q in queries:
        kind, _, value = q.partition(":")
        param = "category" if kind == "category" else "search"
        data = get_json(f"https://remotive.com/api/remote-jobs?{param}={requests.utils.quote(value)}&limit=300", f"Remotive {q}")
        for j in (data or {}).get("jobs", []):
            out.append({
                "title": j.get("title", ""), "url": j.get("url", ""), "company": j.get("company_name", ""),
                "location": "Remote - " + (j.get("candidate_required_location") or ""),
                "date_posted": j.get("publication_date", ""), "salary_text": j.get("salary") or "",
                "description": strip_html(j.get("description", ""))[:12000], "snippet": "",
                "source_domain": "remotive.com", "search_query": f"Remotive: {q}",
            })
        time.sleep(REQUEST_DELAY)
    return out


def jobs_greenhouse(token: str) -> list[dict]:
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true", f"Greenhouse {token}")
    return [{
        "title": j.get("title", ""), "url": j.get("absolute_url", ""), "company": token,
        "location": (j.get("location") or {}).get("name", ""), "date_posted": j.get("updated_at", ""),
        "salary_text": "", "description": strip_html(j.get("content", ""))[:12000], "snippet": "",
        "source_domain": "greenhouse.io", "search_query": f"Greenhouse: {token}",
    } for j in (data or {}).get("jobs", [])]


def jobs_lever(slug: str) -> list[dict]:
    data = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json", f"Lever {slug}")
    out = []
    for j in data if isinstance(data, list) else []:
        loc = (j.get("categories") or {}).get("location", "") or ""
        if j.get("workplaceType") == "remote":
            loc = ("Remote - " + loc).strip(" -")
        out.append({
            "title": j.get("text", ""), "url": j.get("hostedUrl", ""), "company": slug, "location": loc,
            "date_posted": "", "salary_text": "", "description": clean_text(j.get("descriptionPlain", ""))[:12000],
            "snippet": "", "source_domain": "lever.co", "search_query": f"Lever: {slug}",
        })
    return out


def make_filters(profile: dict):
    good, bad = title_terms(profile)
    level = profile["level"]
    loc_re = location_regex(profile)

    def title_ok(title: str) -> bool:
        t = title.lower()
        if has_any(bad, t):
            return False
        if level == "entry" and SENIOR_RE.search(t):
            return False
        if level == "mid" and MID_BLOCK_RE.search(t):
            return False
        if level == "senior" and JUNIOR_RE.search(t):
            return False
        return has_any(good, t)

    def location_ok(loc: str) -> bool:
        if loc_re and loc_re.search(loc):
            return True
        if not (profile["remote_ok"] and REMOTE_RE.search(loc)):
            return False
        return bool(US_RE.search(loc)) or not FOREIGN_RE.search(loc)

    return title_ok, location_ok, loc_re


def collect_jobs(profile: dict) -> tuple[list[dict], int]:
    title_ok, location_ok, loc_re = make_filters(profile)
    remotive_q = []
    for k in profile["tracks"]:
        remotive_q += TRACKS[k]["remotive"]
    if profile["extra_titles"]:
        remotive_q += [f"search:{t}" for t in profile["extra_titles"][:3]]
    remotive_q = list(dict.fromkeys(remotive_q)) or ["category:software-dev"]
    if not profile["remote_ok"]:
        remotive_q = []

    sources = []
    if remotive_q:
        sources.append(("Remotive", lambda: jobs_remotive(remotive_q)))
    sources += [(f"Greenhouse {t}", lambda t=t: jobs_greenhouse(t)) for t in GREENHOUSE_BOARDS]
    sources += [(f"Lever {s}", lambda s=s: jobs_lever(s)) for s in LEVER_BOARDS]

    found: dict[str, dict] = {}
    for i, (name, fetch) in enumerate(sources, 1):
        print(f"[{i}/{len(sources)}] Fetching {name}")
        raw = fetch()
        kept = 0
        for job in raw:
            url = job.get("url", "")
            if not url.startswith(("http://", "https://")):
                continue
            if not title_ok(job["title"]) or not location_ok(job["location"]):
                continue
            key = url.split("?")[0].rstrip("/")
            if key not in found:
                found[key] = job
                kept += 1
        print(f"  {len(raw)} postings, {kept} match your filters")
        time.sleep(REQUEST_DELAY)
    # Local jobs first so they survive the MAX_JOBS_TO_SCORE cutoff.
    ordered = sorted(found.values(), key=lambda j: 0 if (loc_re and loc_re.search(j["location"])) else 1)
    return ordered, len(sources)


# ------------------------------ SALARY -------------------------------
def extract_salary(text: str) -> tuple[int | None, int | None, str]:
    text = text.replace(",", "")
    m = re.search(r"\$\s*(\d{2,3}(?:\.\d{1,2})?)\s*k\s*(?:-|to|–|—)\s*\$?\s*(\d{2,3}(?:\.\d{1,2})?)\s*k", text, re.I)
    if m:
        a, b = float(m.group(1)) * 1000, float(m.group(2)) * 1000
        return int(min(a, b)), int(max(a, b)), f"${int(min(a, b)):,}–${int(max(a, b)):,}"
    m = re.search(r"\$\s*(\d{2,3}\d{3})\s*(?:-|to|–|—)\s*\$?\s*(\d{2,3}\d{3})", text, re.I)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        return int(min(a, b)), int(max(a, b)), f"${int(min(a, b)):,}–${int(max(a, b)):,}"
    m = re.search(r"\$\s*(\d{2,3}(?:\.\d{1,2})?)\s*(?:-|to|–|—)\s*\$?\s*(\d{2,3}(?:\.\d{1,2})?)\s*(?:/\s*hour|per hour|hourly|an hour|/hr)", text, re.I)
    if m:
        a, b = float(m.group(1)) * 2080, float(m.group(2)) * 2080
        return int(min(a, b)), int(max(a, b)), f"${min(a, b):,.0f}–${max(a, b):,.0f} (annualized from hourly)"
    m = re.search(r"\$\s*(\d{2,3}\d{3})\s*(?:/\s*year|per year|annually|annual salary)", text, re.I)
    if m:
        v = int(m.group(1))
        return v, v, f"${v:,}"
    return None, None, "Not found in listing text"


def salary_filter(job: dict, profile: dict, bad: list[str]) -> str:
    floor = profile["min_salary"]
    low, high = job.get("salary_min"), job.get("salary_max")
    if floor and high is not None and high < floor:
        return f"Reject: salary max below ${floor:,}"
    if has_any(bad, job.get("title", "")):
        return "Reject: excluded role/title"
    if low is None:
        return "Review: salary not verified; check employer listing"
    if floor and low < floor <= (high or low):
        return "Review: salary range overlaps your minimum"
    return "Pass: salary floor appears met" if floor else "Pass"


# ----------------------------- SCORING -------------------------------
def ollama_score(resume: str, job: dict, profile: dict) -> dict:
    prompt = f"""Evaluate this job for a candidate.
Candidate's current title: {profile['current_title'] or 'unknown'}
Candidate's GOAL title: {profile['goal_title']}
Target level: {profile['level_label']}
Candidate resume:
{resume[:8000] or '(Resume not supplied)'}

Job:
Title: {job.get('title','')}
Company: {job.get('company','')}
Location: {job.get('location','')}
Salary: {job.get('salary_text','')}
Description: {(job.get('snippet','')+' '+job.get('description',''))[:6000]}

Return ONLY JSON with keys: fit_score (0-100 integer), recommendation ("Apply", "Maybe", or "Skip"), role_type, seniority, why_apply (one concise sentence), matching_skills (short string), skill_gaps (short string), remote_status ("Remote", "Hybrid", "On-site", or "Unclear").
Rules: Score how well the job matches the GOAL title and target level, and how well the resume supports it. Reward skills the resume really shows. Penalize jobs clearly above the target level or unrelated to the goal. Do not invent requirements. If clearly unrelated, recommend Skip."""
    return AI.json(prompt, timeout=180)


def heuristic_score(job: dict, profile: dict, good: list[str], bad: list[str]) -> dict:
    title = job.get("title", "").lower()
    combined = (title + " " + job.get("snippet", "") + " " + job.get("description", "")).lower()
    hits = [g for g in good if term_in(g, title)]
    goal_words = [w for w in re.findall(r"[a-z]+", profile["goal_title"].lower()) if len(w) > 2]
    score = min(90, 45 + 8 * len(hits)) if hits else 20
    score += 5 * sum(1 for w in goal_words if w in title)
    if profile["level"] == "entry" and SENIOR_RE.search(title):
        score -= 50
    if has_any(bad, title):
        score -= 35
    if JUNIOR_RE.search(title) and profile["level"] == "entry":
        score += 8
    score = max(0, min(100, score))
    remote = "Remote" if "remote" in combined else ("Hybrid" if "hybrid" in combined else ("On-site" if re.search(r"on-?site", combined) else "Unclear"))
    return {
        "fit_score": score, "recommendation": "Apply" if score >= 65 else ("Maybe" if score >= 40 else "Skip"),
        "role_type": ", ".join(hits[:3]) or "Needs manual review",
        "seniority": "Senior/lead" if SENIOR_RE.search(title) else "Unclear",
        "why_apply": "Title matches your goal (keyword scoring - AI scoring was off); verify requirements and pay.",
        "matching_skills": "", "skill_gaps": "Not assessed (keyword scorer)", "remote_status": remote,
    }


def score_against_all_resumes(resumes, job, profile, good, bad) -> dict:
    title = job.get("title", "").lower()
    skip_ai = has_any(bad, title)
    evals = []
    for text, path in resumes:
        result = {} if (skip_ai or not AI.enabled) else ollama_score(text, job, profile)
        if "fit_score" not in result:
            result = heuristic_score(job, profile, good, bad)
        try:
            result["fit_score"] = max(0, min(100, int(float(result.get("fit_score", 0)))))
        except (TypeError, ValueError):
            result["fit_score"] = 0
        evals.append({**result, "resume_used": Path(path).name, "resume_path": path})
        if skip_ai or not AI.enabled:
            break
    best = max(evals, key=lambda e: e["fit_score"])
    best["resume_match_details"] = " | ".join(f'{e["resume_used"]}: {e["fit_score"]}/100 ({e.get("recommendation", "?")})' for e in evals)
    return best


# ------------------------------- EXCEL -------------------------------
def style_sheet(ws):
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="243B53")
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for column in ws.columns:
        values = [len(str(c.value or "")) for c in list(column)[:100]]
        ws.column_dimensions[get_column_letter(column[0].column)].width = min(max(max(values, default=10) + 2, 12), 48)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def write_workbook(jobs: list[dict], profile: dict, resume_paths: str, source_count: int):
    wb = Workbook()
    ws0 = wb.active
    ws0.title = "Read Me"
    rows = [
        ["Job Hunt Agent Results"],
        ["Current title", profile["current_title"]],
        ["Goal title", profile["goal_title"]],
        ["Level", profile["level_label"]],
        ["Locations", "; ".join(profile["locations"]) + ("  + remote" if profile["remote_ok"] else "")],
        ["Job families searched", ", ".join(TRACKS[k]["label"] for k in profile["tracks"]) + (" | related: " + ", ".join(profile["extra_titles"][:8]) if profile["extra_titles"] else "")],
        ["Resumes used", resume_paths],
        ["Job sources queried", source_count],
        ["Unique postings scored", len(jobs)],
        ["Minimum salary", profile["min_salary"] or "none"],
        ["Scorer", f"Local AI ({OLLAMA_MODEL})" if AI.enabled else "Keyword scorer (Ollama off)"],
        ["Limitation", "Public job boards only; results can be stale or incomplete. Verify pay, eligibility and requirements on the employer site."],
        ["Apply Shortlist", "Roles scored Apply/Maybe with no explicit exclusion."],
    ]
    for r in rows:
        ws0.append(r)
    ws0.column_dimensions["A"].width = 26
    ws0.column_dimensions["B"].width = 110
    ws0["A1"].font = Font(bold=True, size=16, color="FFFFFF")
    for c in ("A1", "B1"):
        ws0[c].fill = PatternFill("solid", fgColor="243B53")
    for row in ws0.iter_rows():
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    fields = ["fit_score", "recommendation", "resume_used", "resume_match_details", "title", "company", "location",
              "salary_text", "salary_filter", "remote_status", "why_apply", "matching_skills", "skill_gaps",
              "seniority", "role_type", "date_posted", "url", "search_query", "source_domain", "snippet", "description"]
    for name, subset in [
        ("Apply Shortlist", [j for j in jobs if j.get("recommendation") in ("Apply", "Maybe") and not j.get("salary_filter", "").startswith("Reject")]),
        ("All Scored Jobs", jobs),
        ("Filtered Out", [j for j in jobs if j.get("recommendation") == "Skip" or j.get("salary_filter", "").startswith("Reject")]),
    ]:
        ws = wb.create_sheet(name)
        ws.append(fields)
        for job in sorted(subset, key=lambda x: (x.get("fit_score", 0), x.get("salary_min") or 0), reverse=True):
            ws.append([job.get(k, "") for k in fields])
        style_sheet(ws)
    wb.save(OUTPUT_PATH)


# ------------------------------- MAIN --------------------------------
def main():
    print("JOB HUNT AGENT - free job boards + local Ollama\n")
    print(f"Checking Ollama / model {OLLAMA_MODEL} ...")
    AI.check()
    print("\nReading resumes ...")
    resumes = load_resumes()
    profile = build_profile(resumes)
    good, bad = title_terms(profile)

    print("\nCollecting jobs ...")
    results, source_count = collect_jobs(profile)
    results = results[:MAX_JOBS_TO_SCORE]
    print(f"\nFound {len(results)} matching jobs. Scoring ({'local AI' if AI.enabled else 'keyword scorer'}) ...")

    jobs = []
    for i, item in enumerate(results, 1):
        print(f"[{i}/{len(results)}] {item.get('title', '(no title)')}")
        job = dict(item)
        lo, hi, label = extract_salary(" ".join([job.get("salary_text", ""), job.get("snippet", ""), job.get("description", "")]))
        job["salary_min"], job["salary_max"] = lo, hi
        if lo is not None or not job.get("salary_text"):
            job["salary_text"] = label
        job.update(score_against_all_resumes(resumes, job, profile, good, bad))
        job["salary_filter"] = salary_filter(job, profile, bad)
        if has_any(bad, job["title"]):
            job["recommendation"] = "Skip"
        jobs.append(job)

    write_workbook(jobs, profile, "; ".join(p for _, p in resumes), source_count)
    print(f"\nDone. Results saved to: {OUTPUT_PATH}")
    print('Open the "Apply Shortlist" sheet first and confirm each role on the employer site.')
    if not jobs:
        print("No matching jobs. Try a broader goal title, add more locations, or allow remote.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(1)
