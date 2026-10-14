#!/usr/bin/env python3
"""Free job-hunt agent: public web search + local Ollama + Excel workbook.
 
Supports MULTIPLE resumes: every job is scored against each resume and the
best-matching resume is recorded in the workbook.
 
No paid job APIs. Public search engines and employer pages may rate-limit or block
automated access; this script does not bypass logins, CAPTCHAs, or access controls.
"""
from __future__ import annotations
import html, json, re, time, sys
from pathlib import Path
from urllib.parse import quote_plus, urlparse, parse_qs, unquote
from html.parser import HTMLParser
 
try:
    import requests
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
except ImportError as exc:
    print(f"Missing dependency: {exc.name}. Install with: py -m pip install --user requests openpyxl pypdf python-docx")
    raise SystemExit(1)
 
# --------------------------- CONFIGURATION ---------------------------
BASE_DIR = Path(__file__).resolve().parent
# Optional: semicolon-separated full paths, e.g. r"C:\r\resume_sw.pdf;C:\r\resume_ai.docx"
# Leave blank to auto-find every .pdf/.docx/.txt in this folder (jobhunt files are skipped).
RESUME_PATH = ""
OUTPUT_PATH = BASE_DIR / "jobhunt_results.xlsx"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3:8b"
USE_OLLAMA = True
MAX_SEARCH_QUERIES = 42
MAX_RESULTS_PER_QUERY = 8
MAX_JOBS_TO_SCORE = 160
SEARCH_DELAY_SECONDS = 1.5
PAGE_DELAY_SECONDS = 0.5
MIN_ANNUAL_SALARY = 62400  # $30/hour equivalent
TARGET_ANNUAL_SALARY = 75000
 
LOCATIONS = [
    '"United States" remote',
    'Fort Collins OR Loveland OR Greeley Colorado',
    'Denver OR Boulder Colorado',
    'Cheyenne Wyoming',
]
ROLE_QUERIES = [
    'entry level software engineer', 'junior software engineer', 'associate software engineer',
    'software engineer I', 'entry level backend engineer', 'junior backend developer',
    'entry level Python developer', 'junior Python developer', 'entry level Java developer',
    'junior Java developer', 'entry level full stack developer', 'junior full stack engineer',
    'entry level frontend engineer', 'junior web developer', 'application developer I',
    'software developer I', 'entry level AI engineer', 'junior machine learning engineer',
    'AI software engineer entry level', 'LLM engineer junior', 'automation engineer entry level',
    'QA automation engineer entry level', 'software test engineer I', 'SDET entry level',
    'data engineer I', 'analytics engineer entry level', 'cloud engineer I entry level',
    'DevOps engineer I entry level', 'platform engineer junior', 'systems software engineer entry level',
    'technical solutions engineer software', 'implementation engineer software entry level',
    'security software engineer entry level', 'SOC analyst I', 'security automation engineer junior',
]
 
EXCLUDE_TERMS = [
    'senior software', 'staff engineer', 'principal engineer', 'lead engineer', 'architect',
    'engineering manager', 'director of engineering', 'vice president', 'vp of',
    'help desk', 'service desk', 'desktop support', 'field technician', 'field network',
    'network technician', 'network engineer', 'it support', 'technical support specialist',
    'sales engineer', 'account executive', 'business development', 'commission only',
    'unpaid', 'internship', 'intern ', 'volunteer',
]
GOOD_TERMS = [
    'software engineer', 'software developer', 'application developer', 'backend', 'back-end',
    'frontend', 'front-end', 'full stack', 'full-stack', 'python developer', 'java developer',
    'ai engineer', 'machine learning', 'llm', 'automation engineer', 'qa automation', 'sdet',
    'test engineer', 'data engineer', 'analytics engineer', 'cloud engineer', 'devops',
    'platform engineer', 'security engineer', 'soc analyst', 'systems engineer', 'implementation engineer',
]
 
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
 
 
def clean_text(value: str) -> str:
    value = html.unescape(value or '')
    return re.sub(r'\s+', ' ', value).strip()
 
 
def normalize_url(url: str) -> str:
    url = html.unescape(url or '').strip()
    if url.startswith('//'):
        url = 'https:' + url
    if 'duckduckgo.com/l/?' in url:
        try:
            qs = parse_qs(urlparse(url).query)
            url = unquote(qs.get('uddg', [url])[0])
        except Exception:
            pass
    return url.split('#')[0]
 
 
class SearchResultParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self.in_result = False
        self.result_depth = 0
        self.in_link = False
        self.in_snippet = False
        self.current = None
 
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get('class', '').split()
        if tag == 'div':
            if not self.in_result and 'result' in cls:
                self.in_result = True
                self.result_depth = 1
                self.current = {'title': '', 'url': '', 'snippet': ''}
            elif self.in_result:
                self.result_depth += 1
        if self.in_result and tag == 'a' and 'result__a' in cls:
            self.in_link = True
            self.current['url'] = normalize_url(a.get('href', ''))
        if self.in_result and tag in ('a', 'div') and 'result__snippet' in cls:
            self.in_snippet = True
 
    def handle_data(self, data):
        if not self.current:
            return
        if self.in_link:
            self.current['title'] += data
        elif self.in_snippet:
            self.current['snippet'] += data
 
    def handle_endtag(self, tag):
        if tag == 'a' and self.in_link:
            self.in_link = False
        if tag == 'a' and self.in_snippet:
            self.in_snippet = False
        if tag == 'div' and self.in_snippet:
            self.in_snippet = False
        if tag == 'div' and self.in_result:
            self.result_depth -= 1
            if self.result_depth <= 0:
                if self.current and self.current.get('url') and self.current.get('title'):
                    self.results.append({k: clean_text(v) for k, v in self.current.items()})
                self.current = None
                self.in_result = False
                self.result_depth = 0
 
 
def search_web(query: str) -> list[dict]:
    url = 'https://html.duckduckgo.com/html/?q=' + quote_plus(query)
    try:
        response = SESSION.get(url, timeout=20)
        if response.status_code != 200:
            print(f'  Search returned HTTP {response.status_code}; skipping this query.')
            return []
        parser = SearchResultParser()
        parser.feed(response.text)
        return parser.results[:MAX_RESULTS_PER_QUERY]
    except requests.RequestException as exc:
        print(f'  Search failed: {exc}')
        return []
 
 
def make_queries() -> list[str]:
    queries = []
    for role in ROLE_QUERIES:
        for location in LOCATIONS:
            queries.append(f'{role} {location} jobs salary')
    return queries[:MAX_SEARCH_QUERIES]
 
 
def extract_salary(text: str) -> tuple[int | None, int | None, str]:
    text = text.replace(',', '')
    patterns = [
        r'\$\s*(\d{2,3}(?:\.\d{1,2})?)\s*k\s*(?:-|to|–|—)\s*\$?\s*(\d{2,3}(?:\.\d{1,2})?)\s*k',
        r'\$\s*(\d{2,3},?\d{3})\s*(?:-|to|–|—)\s*\$?\s*(\d{2,3},?\d{3})',
        r'(?:salary|pay|compensation|range)[^\n]{0,50}\$\s*(\d{2,3}(?:,?\d{3})?)\s*(?:-|to|–|—)\s*\$?\s*(\d{2,3}(?:,?\d{3})?)',
    ]
    for i, pattern in enumerate(patterns):
        match = re.search(pattern, text, re.I)
        if match:
            try:
                a, b = float(match.group(1)), float(match.group(2))
                if i == 0:
                    a, b = a * 1000, b * 1000
                elif a < 1000:
                    a, b = a * 1000, b * 1000
                return int(min(a, b)), int(max(a, b)), f'${int(min(a,b)):,}–${int(max(a,b)):,}'
            except (ValueError, TypeError):
                pass
    hourly = re.search(r'\$\s*(\d{2,3}(?:\.\d{1,2})?)\s*(?:-|to|–|—)\s*\$?\s*(\d{2,3}(?:\.\d{1,2})?)\s*(?:/\s*hour|per hour|hourly|an hour)', text, re.I)
    if hourly:
        a, b = float(hourly.group(1)) * 2080, float(hourly.group(2)) * 2080
        return int(min(a, b)), int(max(a, b)), f'${min(a,b):,.0f}–${max(a,b):,.0f} annualized from hourly'
    single = re.search(r'\$\s*(\d{2,3}(?:,?\d{3})?)\s*(?:/\s*year|per year|annually|annual salary)', text, re.I)
    if single:
        value = int(single.group(1).replace(',', ''))
        if value >= 10000:
            return value, value, f'${value:,}'
    return None, None, 'Not found in accessible listing text'
 
 
def flatten_jsonld(obj):
    found = []
    if isinstance(obj, dict):
        typ = obj.get('@type', [])
        if isinstance(typ, str):
            typ = [typ]
        if 'JobPosting' in typ:
            found.append(obj)
        graph = obj.get('@graph')
        if isinstance(graph, list):
            for item in graph:
                found.extend(flatten_jsonld(item))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(flatten_jsonld(item))
    return found
 
 
class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_script = False
        self.script_type = ''
        self.script_buffer = ''
        self.scripts = []
        self.text_parts = []
        self.in_style = False
 
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'script':
            self.in_script = True
            self.script_type = a.get('type', '')
            self.script_buffer = ''
        if tag in ('style', 'noscript', 'svg'):
            self.in_style = True
 
    def handle_data(self, data):
        if self.in_script:
            self.script_buffer += data
        elif not self.in_style:
            self.text_parts.append(data)
 
    def handle_endtag(self, tag):
        if tag == 'script' and self.in_script:
            if 'ld+json' in self.script_type.lower():
                self.scripts.append(self.script_buffer)
            self.in_script = False
            self.script_buffer = ''
        if tag in ('style', 'noscript', 'svg'):
            self.in_style = False
 
 
def fetch_job_page(url: str) -> dict:
    data = {'description': '', 'company': '', 'location': '', 'date_posted': '', 'salary_text': '', 'structured': False}
    try:
        response = SESSION.get(url, timeout=18, allow_redirects=True)
        if response.status_code != 200 or 'text/html' not in response.headers.get('content-type', ''):
            return data
        parser = PageParser()
        parser.feed(response.text[:2_500_000])
        text = clean_text(' '.join(parser.text_parts))[:12000]
        data['description'] = text
        for raw in parser.scripts:
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            for job in flatten_jsonld(parsed):
                data['structured'] = True
                data['description'] = clean_text(str(job.get('description', '')) or text)[:12000]
                data['company'] = clean_text(str((job.get('hiringOrganization') or {}).get('name', '')))
                loc = job.get('jobLocation', [])
                if isinstance(loc, dict):
                    loc = [loc]
                places = []
                for item in loc if isinstance(loc, list) else []:
                    address = item.get('address', {}) if isinstance(item, dict) else {}
                    if isinstance(address, dict):
                        parts = [address.get(k, '') for k in ('addressLocality', 'addressRegion', 'addressCountry')]
                        places.append(', '.join(str(p) for p in parts if p))
                data['location'] = '; '.join(x for x in places if x)
                data['date_posted'] = str(job.get('datePosted', ''))
                salary = job.get('baseSalary')
                if isinstance(salary, dict):
                    val = salary.get('value', {})
                    if isinstance(val, dict):
                        mn, mx = val.get('minValue'), val.get('maxValue')
                        unit = val.get('unitText', '')
                        if mn or mx:
                            data['salary_text'] = f'{mn or mx} - {mx or mn} {unit}'
                break
    except requests.RequestException:
        pass
    return data
 
 
# ----------------------------- RESUMES -------------------------------
def read_resume_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == '.txt':
        return path.read_text(encoding='utf-8', errors='ignore')
    if suffix == '.pdf':
        from pypdf import PdfReader
        return '\n'.join(page.extract_text() or '' for page in PdfReader(str(path)).pages)
    if suffix == '.docx':
        from docx import Document
        return '\n'.join(p.text for p in Document(str(path)).paragraphs)
    return ''
 
 
def load_resumes() -> list[tuple[str, str]]:
    """Return a list of (resume_text, resume_path). Reads every resume found."""
    candidates: list[Path] = []
    if RESUME_PATH:
        for part in RESUME_PATH.split(';'):
            if part.strip():
                candidates.append(Path(part.strip().strip('"')).expanduser())
    else:
        for pattern in ('*.pdf', '*.docx', '*.txt'):
            candidates.extend(
                p for p in BASE_DIR.glob(pattern)
                if 'jobhunt' not in p.name.lower() and not p.name.startswith('~$')
            )
    candidates = list(dict.fromkeys(candidates))
 
    resumes = []
    for path in candidates:
        if not path.is_file():
            print(f'Resume not found: {path}')
            continue
        try:
            content = read_resume_file(path)
            if content.strip():
                resumes.append((content[:16000], str(path)))
                print(f'Loaded resume: {path.name}')
            else:
                print(f'Resume has no extractable text: {path.name}')
        except ImportError as exc:
            print(f'Skipping {path.name}: missing {exc.name}. Install: py -m pip install --user pypdf python-docx')
        except Exception as exc:
            print(f'Could not read {path.name}: {exc}')
 
    if not resumes:
        print(f'\nNo readable resumes found in {BASE_DIR}.')
        entered = input('Resume path (or Enter to continue with none): ').strip().strip('"')
        if entered:
            try:
                p = Path(entered).expanduser()
                content = read_resume_file(p)
                if content.strip():
                    resumes.append((content[:16000], str(p)))
            except Exception as exc:
                print(f'Could not read that file: {exc}')
        if not resumes:
            resumes.append(('', '(not provided)'))
    print(f'Using {len(resumes)} resume(s).')
    return resumes
 
 
# ----------------------------- SCORING -------------------------------
def ollama_score(resume: str, job: dict) -> dict:
    prompt = f'''Evaluate this job for a candidate seeking entry-level software/engineering work.
Candidate resume:
{resume[:9000] or '(Resume not supplied)'}
 
Job:
Title: {job.get('title','')}
Company: {job.get('company','')}
Location: {job.get('location','')}
Salary: {job.get('salary_text','')}
Snippet and accessible description: {(job.get('snippet','')+' '+job.get('description',''))[:6500]}
 
Return ONLY JSON with keys: fit_score (0-100 integer), recommendation ("Apply", "Maybe", or "Skip"), role_type, seniority, why_apply (one concise sentence), matching_skills (short string), skill_gaps (short string), remote_status ("Remote", "Hybrid", "On-site", or "Unclear").
Rules: Favor genuine entry-level software development, AI/software, QA automation/SDET, cloud/DevOps/platform, data engineering, and software security roles. The candidate has a CS bachelor's and IT infrastructure experience plus Python/Java/JavaScript, Linux, Git, APIs, networking, and automation. Do not overvalue network/help-desk support. Flag seniority or unclear salary; do not invent requirements. If clearly unrelated, recommend Skip.'''
    try:
        response = SESSION.post(OLLAMA_URL, json={'model': OLLAMA_MODEL, 'prompt': prompt, 'stream': False, 'format': 'json', 'options': {'temperature': 0.1}}, timeout=180)
        response.raise_for_status()
        raw = response.json().get('response', '')
        result = json.loads(raw)
        return result if isinstance(result, dict) else {}
    except Exception as exc:
        return {'fit_score': 0, 'recommendation': 'Maybe', 'role_type': 'Needs manual review', 'seniority': 'Unclear', 'why_apply': f'Local AI scoring unavailable ({type(exc).__name__}); review manually.', 'matching_skills': '', 'skill_gaps': '', 'remote_status': 'Unclear'}
 
 
def heuristic_score(job: dict) -> dict:
    title = job.get('title', '').lower()
    combined = (title + ' ' + job.get('snippet', '') + ' ' + job.get('description', '')).lower()
    bad = [term for term in EXCLUDE_TERMS if term in title]
    good = [term for term in GOOD_TERMS if term in title]
    score = min(95, 45 + 7 * len(good)) if good else 20
    if any(x in title for x in ('senior', 'staff', 'principal', 'lead', 'manager', 'director')):
        score -= 50
    if bad:
        score -= 35
    remote = 'Remote' if 'remote' in combined else ('Hybrid' if 'hybrid' in combined else ('On-site' if 'on-site' in combined or 'onsite' in combined else 'Unclear'))
    return {'fit_score': max(0, min(100, score)), 'recommendation': 'Apply' if score >= 65 else ('Maybe' if score >= 40 else 'Skip'), 'role_type': ', '.join(good[:3]) or 'Needs manual review', 'seniority': 'Senior/lead' if any(x in title for x in ('senior', 'staff', 'principal', 'lead', 'manager')) else 'Unclear', 'why_apply': 'Title appears related to target engineering roles; verify requirements and compensation manually.', 'matching_skills': 'Python, Java, JavaScript, Linux, Git, APIs, automation (verify against listing)', 'skill_gaps': 'Not assessed by local model', 'remote_status': remote}
 
 
def score_against_all_resumes(resumes: list[tuple[str, str]], job: dict) -> dict:
    """Score a job against each resume and keep the strongest match."""
    title = job.get('title', '').lower()
    # Obvious non-targets: skip the slow AI calls, score once with the heuristic.
    skip_ai = any(term in title for term in EXCLUDE_TERMS)
    evaluations = []
    for resume_text, resume_path in resumes:
        if USE_OLLAMA and not skip_ai:
            result = ollama_score(resume_text, job)
            if not result or 'fit_score' not in result:
                result = heuristic_score(job)
        else:
            result = heuristic_score(job)
        try:
            result['fit_score'] = max(0, min(100, int(float(result.get('fit_score', 0)))))
        except (TypeError, ValueError):
            result['fit_score'] = 0
        evaluations.append({**result, 'resume_used': Path(resume_path).name, 'resume_path': resume_path})
        if skip_ai:
            break  # identical result for every resume; no point repeating
 
    best = max(evaluations, key=lambda item: item['fit_score'])
    best['resume_match_details'] = ' | '.join(
        f'{e["resume_used"]}: {e["fit_score"]}/100 ({e.get("recommendation", "Unknown")})'
        for e in evaluations
    )
    return best
 
 
def salary_filter(job: dict) -> str:
    low, high = job.get('salary_min'), job.get('salary_max')
    if low is not None and high is not None and high < MIN_ANNUAL_SALARY:
        return f'Reject: salary max below ${MIN_ANNUAL_SALARY:,}'
    title = job.get('title', '').lower()
    if any(term in title for term in EXCLUDE_TERMS):
        return 'Reject: excluded role/title'
    if not any(term in title for term in GOOD_TERMS):
        return 'Review: title not clearly in target engineering categories'
    if low is None:
        return 'Review: salary not verified; check employer listing'
    if low < MIN_ANNUAL_SALARY <= (high or low):
        return 'Review: salary range overlaps minimum'
    return 'Pass: salary floor appears met'
 
 
# ------------------------------ EXCEL --------------------------------
def style_sheet(ws):
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = ws.dimensions
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='243B53')
        cell.alignment = Alignment(wrap_text=True, vertical='top')
    for column in ws.columns:
        values = [len(str(c.value or '')) for c in list(column)[:100]]
        width = min(max(max(values, default=10) + 2, 12), 48)
        ws.column_dimensions[get_column_letter(column[0].column)].width = width
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical='top', wrap_text=True)
 
 
def write_workbook(jobs: list[dict], resume_paths: str, search_count: int):
    wb = Workbook()
    summary = wb.active
    summary.title = 'Read Me'
    rows = [
        ['Free Job Hunt Agent Results'],
        ['Resumes used', resume_paths],
        ['Search queries attempted', search_count],
        ['Unique job pages found', len(jobs)],
        ['Minimum annual salary target', MIN_ANNUAL_SALARY],
        ['Preferred salary target', TARGET_ANNUAL_SALARY],
        ['Local model', OLLAMA_MODEL if USE_OLLAMA else 'Heuristic fallback only'],
        ['Important limitation', 'Public search is best-effort; results may be blocked, duplicated, stale, or incomplete. Verify salary, eligibility, and requirements on the employer site.'],
        ['Apply Shortlist', 'Roles scored Apply/Maybe with no explicit exclusion. Verify the salary and job details before applying.'],
        ['Resume columns', 'resume_used = best-matching resume for that job; resume_match_details = score from every resume.'],
    ]
    for row in rows:
        summary.append(row)
    summary.column_dimensions['A'].width = 32
    summary.column_dimensions['B'].width = 110
    summary['A1'].font = Font(bold=True, size=16, color='FFFFFF')
    summary['A1'].fill = PatternFill('solid', fgColor='243B53')
    summary['B1'].fill = PatternFill('solid', fgColor='243B53')
    for row in summary.iter_rows():
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical='top')
 
    fields = [
        'fit_score', 'recommendation',
        'resume_used', 'resume_path', 'resume_match_details',
        'title', 'company', 'location', 'salary_text', 'salary_filter', 'remote_status',
        'why_apply', 'matching_skills', 'skill_gaps', 'seniority', 'role_type',
        'date_posted', 'url', 'search_query', 'source_domain', 'structured_job_data',
        'snippet', 'description',
    ]
    for sheet_name, subset in [
        ('Apply Shortlist', [j for j in jobs if j.get('recommendation') in ('Apply', 'Maybe') and not j.get('salary_filter', '').startswith('Reject')]),
        ('All Scored Jobs', jobs),
        ('Filtered Out', [j for j in jobs if j.get('recommendation') == 'Skip' or j.get('salary_filter', '').startswith('Reject')]),
    ]:
        ws = wb.create_sheet(sheet_name)
        ws.append(fields)
        for job in sorted(subset, key=lambda x: (x.get('fit_score', 0), x.get('salary_min') or 0), reverse=True):
            ws.append([job.get(k, '') for k in fields])
        style_sheet(ws)
    wb.save(OUTPUT_PATH)
 
 # --------------------------- JOB SOURCES -----------------------------
# Company board names: boards.greenhouse.io/NAME or jobs.lever.co/NAME.
# Edit freely. Wrong or dead names are just skipped with a message.
GREENHOUSE_BOARDS = [
    'gitlab', 'datadog', 'cloudflare', 'twilio', 'okta', 'databricks', 'samsara',
    'gusto', 'ibotta', 'coinbase', 'asana', 'dropbox', 'reddit', 'mongodb', 'elastic', 'brex',
]
LEVER_BOARDS = ['palantir', 'spotify']
REMOTIVE_CATEGORIES = ['software-dev', 'data']  # keep this short; Remotive asks for light usage

LOCAL_RE = re.compile(r'colorado|,\s*co\b|denver|boulder|fort collins|loveland|greeley|cheyenne|wyoming|,\s*wy\b', re.I)
REMOTE_RE = re.compile(r'remote|anywhere|united states|\busa?\b|\bu\.s\.', re.I)


def strip_html(text: str) -> str:
    text = html.unescape(text or '')
    text = re.sub(r'<[^>]+>', ' ', text)
    return clean_text(html.unescape(text))


def get_json(url: str, label: str):
    try:
        r = SESSION.get(url, timeout=25)
        if r.status_code != 200:
            print(f'  Skipped {label} (HTTP {r.status_code})')
            return None
        return r.json()
    except Exception as exc:
        print(f'  Skipped {label} ({type(exc).__name__})')
        return None


def title_ok(title: str) -> bool:
    t = title.lower()
    if any(x in t for x in EXCLUDE_TERMS):
        return False
    if re.search(r'\b(senior|sr\.?|staff|principal|lead|manager|director|head of|vp)\b', t):
        return False
    return any(g in t for g in GOOD_TERMS)


def location_ok(loc: str) -> bool:
    return bool(LOCAL_RE.search(loc) or REMOTE_RE.search(loc))


def jobs_remotive() -> list[dict]:
    out = []
    for cat in REMOTIVE_CATEGORIES:
        data = get_json(f'https://remotive.com/api/remote-jobs?category={cat}&limit=300', f'Remotive {cat}')
        for j in (data or {}).get('jobs', []):
            out.append({
                'title': j.get('title', ''), 'url': j.get('url', ''), 'company': j.get('company_name', ''),
                'location': 'Remote - ' + (j.get('candidate_required_location') or ''),
                'date_posted': j.get('publication_date', ''), 'salary_text': j.get('salary') or '',
                'description': strip_html(j.get('description', ''))[:12000], 'snippet': '',
                'source_domain': 'remotive.com', 'search_query': f'Remotive: {cat}',
            })
    return out


def jobs_greenhouse(token: str) -> list[dict]:
    data = get_json(f'https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true', f'Greenhouse {token}')
    out = []
    for j in (data or {}).get('jobs', []):
        out.append({
            'title': j.get('title', ''), 'url': j.get('absolute_url', ''), 'company': token,
            'location': (j.get('location') or {}).get('name', ''),
            'date_posted': j.get('updated_at', ''), 'salary_text': '',
            'description': strip_html(j.get('content', ''))[:12000], 'snippet': '',
            'source_domain': 'greenhouse.io', 'search_query': f'Greenhouse: {token}',
        })
    return out


def jobs_lever(slug: str) -> list[dict]:
    data = get_json(f'https://api.lever.co/v0/postings/{slug}?mode=json', f'Lever {slug}')
    out = []
    for j in data if isinstance(data, list) else []:
        cats = j.get('categories') or {}
        loc = cats.get('location', '') or ''
        if j.get('workplaceType') == 'remote':
            loc = ('Remote - ' + loc).strip(' -')
        out.append({
            'title': j.get('text', ''), 'url': j.get('hostedUrl', ''), 'company': slug,
            'location': loc, 'date_posted': '', 'salary_text': '',
            'description': clean_text(j.get('descriptionPlain', ''))[:12000], 'snippet': '',
            'source_domain': 'lever.co', 'search_query': f'Lever: {slug}',
        })
    return out


def collect_jobs() -> tuple[list[dict], list[str]]:
    sources = [('Remotive', jobs_remotive)]
    sources += [(f'Greenhouse {t}', lambda t=t: jobs_greenhouse(t)) for t in GREENHOUSE_BOARDS]
    sources += [(f'Lever {s}', lambda s=s: jobs_lever(s)) for s in LEVER_BOARDS]
    found = {}
    for index, (name, fetch) in enumerate(sources, 1):
        print(f'[{index}/{len(sources)}] Fetching {name}')
        raw = fetch()
        kept = 0
        for job in raw:
            url = job.get('url', '')
            if not url.startswith(('http://', 'https://')):
                continue
            if not title_ok(job['title']) or not location_ok(job['location']):
                continue
            key = url.split('?')[0].rstrip('/')
            if key not in found:
                found[key] = job
                kept += 1
        print(f'  {len(raw)} postings, {kept} match your title/location filters')
        time.sleep(1)
    # Local (Colorado/Wyoming) jobs first, so they survive the MAX_JOBS_TO_SCORE cutoff.
    ordered = sorted(found.values(), key=lambda j: 0 if LOCAL_RE.search(j['location']) else 1)
    return ordered, [name for name, _ in sources]


# ------------------------------- MAIN --------------------------------
def main():
    print('FREE JOB HUNT AGENT — job board APIs + local Ollama')
    print(f'Local model: {OLLAMA_MODEL if USE_OLLAMA else "off"}')
    resumes = load_resumes()
    resume_paths = '; '.join(path for _, path in resumes)
    results, sources = collect_jobs()
    results = results[:MAX_JOBS_TO_SCORE]
    print(f'Found {len(results)} matching jobs. Scoring...')
    jobs = []
    for index, item in enumerate(results, 1):
        print(f'[{index}/{len(results)}] {item.get("title", "(no title)")}')
        job = dict(item)
        job.setdefault('company', '')
        job.setdefault('location', '')
        salary_source = job.get('salary_text', '') + ' ' + job.get('snippet', '') + ' ' + job.get('description', '')
        lo, hi, salary_label = extract_salary(salary_source)
        if lo is not None:
            job['salary_min'], job['salary_max'], job['salary_text'] = lo, hi, salary_label
        else:
            job['salary_min'], job['salary_max'] = None, None
            if not job.get('salary_text'):
                job['salary_text'] = salary_label
        title = job.get('title', '')
        job.update(score_against_all_resumes(resumes, job))
        job['salary_filter'] = salary_filter(job)
        if any(term in title.lower() for term in EXCLUDE_TERMS):
            job['recommendation'] = 'Skip'
        jobs.append(job)
    write_workbook(jobs, resume_paths, len(sources))
    print(f'\nFinished. Workbook saved to: {OUTPUT_PATH}')
    print('Open the "Apply Shortlist" sheet first. Confirm each role and salary on the employer website.')
    if not jobs:
        print('No matching jobs found. Check the "Skipped" messages above or edit the board lists.')
 
if __name__ == '__main__':
    main()