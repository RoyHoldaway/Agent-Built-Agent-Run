# siteagent

Local agent that turns **a client brief + hand-drawn sketches** into a deployable website with a **working appointment system and an admin panel**, using Qwen running on your machine.

## The idea (read this first)

A local model is great at reading a drawing and writing markup, and bad at inventing a reliable booking backend from scratch. So the work is split:

| Piece | Who does it |
|---|---|
| Booking engine, availability, double-booking protection, admin login, days off, weekly hours, services, bookings list | **`template/`** (fixed, tested code, the model can't touch it) |
| Reading your sketch | **Qwen vision** → structured "page spec" JSON |
| Writing pages, picking colors/fonts | **Qwen coder** → only into an allow-list of design files |
| File permissions, nav, color contrast, build check | **plain Python** (deterministic) |
| Build errors | fed back to Qwen to fix, up to 3 rounds |

```
sketch.png ──vision──▶ spec.json ──coder──▶ pages/*.astro ──▶ guarded write ──▶ npm build
                                                                  ▲                │ fails?
                                                                  └── coder repairs ◀┘
```
Every step is a git commit inside the client project, so `git revert` is your undo button.

## Stack (and why)
- **Astro 7** (server output): fast, mostly-static HTML, tiny JS, API routes in the same project.
- **Tailwind CSS v4**: design tokens live in one file (`theme.css`), so re-branding = rewriting one file.
- **SQLite via `node:sqlite`** (built into Node): no database server to rent or maintain per client.
- Vanilla TypeScript for the booking widget and admin; no UI framework to keep updated.
- Needs **Node 22.12+** (`node:sqlite` prints an "experimental" warning on Node 22; the start script silences it).

## Setup
```bash
cd siteagent
pip install -e ".[review]"          # [review] adds Playwright for the screenshot-vs-sketch check
playwright install chromium         # only if you want --review
cp .env.example .env                # point at your Qwen server, set model names
ollama pull qwen3-coder:30b && ollama pull qwen3-vl:8b      # or whatever Qwen tags you run
OLLAMA_CONTEXT_LENGTH=32768 ollama serve                     # important: default context is too small for pages
siteagent doctor                    # checks server, models, node/npm/git
```
Any OpenAI-compatible server works (Ollama, LM Studio, vLLM, llama.cpp, DashScope). Edit the model names in `.env` to match the Qwen versions you actually have.

## Workflow with a client
1. **Create the project** (brief is what the model writes copy from):
   ```bash
   siteagent new "Bella Salon" --industry "hair salon" --phone 970-555-0100 \
     --service "Cut:45:35" --service "Color:90:90" \
     --hours "mon-fri=09:00-18:00,sat=10:00-14:00" --tone "warm, modern, earthy"
   ```
2. **Draw the pages** with them (Paint, or pencil + phone photo). **Name each file after the page**: `home.png`, `about.png`, `services.png`. The filename becomes the URL. Tips that help the vision model: label boxes in plain words, X through image boxes, draw a calendar box where booking goes, write colors you want next to things.
3. **Generate**: `siteagent design bella-salon home.png about.png services.png`
   Add `--review 1` to screenshot each page and have the vision model compare it to your sketch and fix differences.
4. **Look at it**: `siteagent dev bella-salon` (admin password in dev is `dev-password` at `/admin`).
5. **Tweak in plain English**: `siteagent edit bella-salon "make the hero darker and add a gallery section"` (`--file src/pages/about.astro` to limit scope).

## Admin panel (`/admin`)
Password login (signed HttpOnly cookie, rate-limited). Tabs: **Bookings** (cancel/restore), **Days off** (single day or date range, with a warning if existing bookings are affected), **Weekly hours**, **Services** (name, length, price, bookable on/off), **Settings** (slot spacing, simultaneous appointments, minimum notice, how far ahead people can book).
Booking is double-booking-safe (checked inside a database write lock) and honors the business time zone.

## Deploying a client site
Any host that runs Node and has a **persistent disk** (VPS, Fly.io, Render, Railway):
```bash
npm install && npm run build
ADMIN_PASSWORD='long-random' SESSION_SECRET='another-long-random' DB_PATH=/data/site.db HOST=0.0.0.0 PORT=8080 npm start
```
Put it behind HTTPS (the admin cookie is `Secure` in production). The database is created on first start from `site.config.json`; after that the admin panel owns hours/services (editing the JSON later does not change them).

## Tuning the agent
- `template/AGENT_GUIDE.md`: the rules and component list the model sees. If Qwen keeps making the same mistake, add a line here.
- `siteagent/prompts.py`: all prompt text.
- `siteagent/project.py` → `ALLOW` list and checks: what the model may write.
- Smaller models: raise `SITEAGENT_MAX_REPAIRS`, keep sketches simple (one page per image), and prefer 3-6 sections per page.

## Tests
```bash
python -m pytest tests/test_units.py                       # guards, parsers, contrast
python tests/test_template.py <built-template-dir>          # 45 API checks: booking, days off, hours, auth
python tests/mock_qwen.py 8765 &                            # fake Qwen for dry-running the whole agent
QWEN_BASE_URL=http://127.0.0.1:8765/v1 siteagent doctor
```

## Known limits (v0.1)
- No email/SMS confirmations yet. `template/src/lib/notify.ts` is the hook (Resend/Postmark/Twilio drop in there).
- Days off close whole days; no partial-day blocks or per-staff calendars yet.
- One server process / one SQLite file per client (fine for small businesses).
- Handwriting recognition quality depends on the vision model; unreadable text is flagged in `.siteagent/specs/*.json`.
