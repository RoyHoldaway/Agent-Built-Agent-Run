# Job Hunt Agent

A free job-search helper that runs on your own computer.

1. It reads your resume and figures out your **current job title**.
2. It asks for your **goal job title** (and level, location, minimum pay).
3. It searches for that job family plus related ones (e.g. goal "cybersecurity" -> SOC, NOC and security engineering / OpSec roles; goal "software engineer" -> SWE, backend, full stack, etc.), filtered to the level you pick (entry level / new grad by default).
4. A **local AI model (via Ollama)** reads each posting against your resume and scores it.
5. You get an Excel file, `jobhunt_results.xlsx`, with a shortlist sorted best-first.

Everything is free. Your resume is only read by the AI running on your own machine; the only internet traffic is fetching public job postings.

---

## Setup (one time, ~15 minutes, mostly downloading)

### Step 1 - Install Ollama (runs the AI locally)

Download it from **https://ollama.com/download** and install it:

- **Windows:** run `OllamaSetup.exe`.
- **Mac:** open the download and drag Ollama into Applications, then launch it once.
- **Linux:** `curl -fsSL https://ollama.com/install.sh | sh`

Check it installed - open a terminal (Windows: PowerShell) and run:

```
ollama --version
```

On Windows/Mac, Ollama runs in the background automatically (look for the llama icon in the tray / menu bar). On Linux, if it isn't running, start it with `ollama serve` in a separate terminal.

### Step 2 - Download the AI model (this is the "agent")

The agent is the model **`qwen3:8b`** (about 5 GB download). In the terminal:

```
ollama pull qwen3:8b
```

Quick test (type `/bye` to leave):

```
ollama run qwen3:8b
```

**Slow or low-memory computer?** The 8B model wants roughly 8 GB of free RAM (or a GPU with 6+ GB). If it crawls or crashes, use the smaller one instead:

```
ollama pull qwen3:4b
```

then open `jobhunt_agent.py` in any text editor and change this line near the top:

```python
OLLAMA_MODEL = "qwen3:8b"     ->     OLLAMA_MODEL = "qwen3:4b"
```

If you skip Ollama entirely, the program still works, but it falls back to a simple keyword scorer (less accurate, and it can't read your resume for the current title).

### Step 3 - Install Python

You need **Python 3.9 or newer** from https://www.python.org/downloads/. On Windows, tick **"Add python.exe to PATH"** in the installer.

### Step 4 - Add your resume

Drop your resume (PDF, DOCX or TXT) into the **`resumes`** folder. It must be a *text* PDF - if you can select/copy text in it, you're fine. A scanned image won't work. You can put in several resumes (say, one tailored per career path); every job gets scored against each one and the best match is recorded.

### Step 5 - Run it

- **Windows:** double-click **`run.bat`**
- **Mac/Linux:** run `./run.sh`

The first run creates a private Python environment and installs the few libraries (`requests`, `openpyxl`, `pypdf`, `python-docx`). Later runs start right away.

Prefer doing it by hand?

```
python -m venv .venv
.venv\Scripts\activate          (Mac/Linux: source .venv/bin/activate)
python -m pip install -r requirements.txt
python jobhunt_agent.py
```

---

## What it asks you

| Prompt | What to type |
|---|---|
| Current title | It guesses from your resume. Press Enter to accept or type the right one. |
| Goal job title | e.g. `software engineer`, `SOC analyst`, `cybersecurity`, `data analyst`, `devops`, `QA automation` |
| Level | `1` entry level / new grad (default), `2` mid, `3` senior+ |
| Locations | e.g. `Austin, TX` or `Denver, CO; Boulder, CO`. Leave blank for remote only. Using the city name works best. |
| Remote? | `y` to include US-remote jobs |
| Minimum salary | e.g. `65000`, or blank for none |

Your answers are saved to `jobhunt_profile.json`, so next time you can just say `y` to reuse them (or `n` to change the goal).

### How the "goal -> related jobs" mapping works

Built-in job families:

| If your goal mentions... | It searches |
|---|---|
| software, developer, backend, frontend, full stack, web, python, java | Software engineering |
| qa, test, sdet | QA / test automation |
| data, analytics | Data engineer / analyst |
| machine learning, ai, llm | AI / ML |
| devops, cloud, sre, platform, sysadmin | DevOps / cloud / SRE |
| **cyber / security** | **SOC analyst + NOC / network ops + security engineering / OpSec** |
| soc, incident, threat | Security operations (SOC) |
| noc, network | Network operations (NOC) |
| opsec, appsec, pentest | Security engineering / OpSec |
| help desk, support, technician, field | IT / support / field tech |

If your goal isn't in that table (say "technical writer" or "product analyst"), the local AI is asked for 10-14 related job titles and those are searched instead.

At "entry level" it throws out titles with senior / staff / lead / principal / manager / director / II / III and so on. It also drops internships, commission-only and unpaid roles.

---

## Reading the results

Open **`jobhunt_results.xlsx`**:

- **Apply Shortlist** - start here. Roles scored Apply/Maybe, best fit first.
- **All Scored Jobs** - everything it found.
- **Filtered Out** - skipped roles, kept so you can see why.
- **Read Me** - the settings used for that run.

Useful columns: `fit_score` (0-100), `why_apply`, `matching_skills`, `skill_gaps`, `salary_text`, `remote_status`, `url`. **Always confirm pay and requirements on the employer's own page before applying** - salary is often missing from postings and scores come from a small local AI, so treat them as a sorted to-do list, not gospel.

---

## Tuning

Everything is at the top of `jobhunt_agent.py`:

- `MAX_JOBS_TO_SCORE = 80` - the AI scoring is the slow part (several seconds per job per resume). Lower it for faster runs.
- `GREENHOUSE_BOARDS` / `LEVER_BOARDS` - company job boards that get searched. Add any company that hosts jobs at `boards.greenhouse.io/NAME` or `jobs.lever.co/NAME`; dead names are skipped with a message. The default list is tech-company heavy, so for a niche (e.g. security), adding companies in your field helps a lot.
- `TRACKS` - the job-family table above. Copy a block to add your own niche: `match` = words in your goal title, `titles` = words a job title must contain.

## Troubleshooting

- **"Ollama is not running"** - launch the Ollama app (Windows/Mac) or run `ollama serve` (Linux), then re-run.
- **"model is not downloaded"** - run `ollama pull qwen3:8b` (or whatever `OLLAMA_MODEL` is set to).
- **Very slow scoring** - use `qwen3:4b`, lower `MAX_JOBS_TO_SCORE`, or close other heavy apps.
- **"No matching jobs"** - broaden the goal title, add more locations, or allow remote. Public job feeds are best-effort and some boards rate-limit.
- **"No extractable text in resume"** - your PDF is a scan. Re-export it as a text PDF or use a .docx.
- **Remote jobs listed for another country are skipped on purpose** (this tool is US-focused).

## Notes

This only reads public job feeds. It doesn't log into anything, apply for you, or get around CAPTCHAs or access controls.
