# Local Lead Discovery Agent

Discovers local businesses by city/category, deduplicates them, analyzes their websites, scores website-sales opportunities with local Qwen/Ollama, and writes a human-review queue.

## Requirements
- Python 3.11+
- Ollama running locally with `qwen3:8b`
- Google Places API key with Places API (New) enabled, if using Google discovery
- `requests`, `beautifulsoup4`

Install:

```powershell
..\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add your Google Places API key. Do not commit `.env`.

## First test
1. Edit `config/cities.json` and `config/categories.json`.
2. Run discovery only:

```powershell
..\\.venv\\Scripts\\python.exe lead_discovery_agent.py --discover
```

3. Run website analysis + Qwen scoring:

```powershell
..\\.venv\\Scripts\\python.exe lead_discovery_agent.py --analyze
```

4. Run everything:

```powershell
..\\.venv\\Scripts\\python.exe lead_discovery_agent.py --all
```

Outputs are JSON. The main review queue is `output/new_leads.json`.

## Important
The agent never emails or contacts a business. It only discovers, analyzes, scores, and proposes leads. Human approval is required before your outreach agent uses a lead.

Google Places search is intentionally capped per city/category and cached in `state/businesses.json` to prevent repeated discovery of the same businesses.
