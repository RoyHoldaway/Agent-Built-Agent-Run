"""A fake OpenAI-compatible Qwen server for testing the agent without a GPU.

It recognises which prompt it was sent and answers like a (mostly) good model:
  - sketch prompt  -> a page-spec JSON (wrapped in chatter + <think> to exercise the parsers)
  - theme prompt   -> theme.css with a LOW-contrast button color (agent must auto-fix)
  - page prompt    -> an .astro page; the FIRST attempt imports a missing component (build must fail)
  - repair prompt  -> creates the missing component
  - also tries to write a locked file once (must be rejected, then re-sent corrected)
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

state = {"pages": 0, "repairs": 0, "locked_attempted": False, "log": []}

SPEC = {"page_title": "Home", "sections": [
    {"kind": "hero", "layout": "centered heading with button", "texts": ["Fresh cuts, friendly faces"], "elements": ["button 'Book now'"], "notes": ""},
    {"kind": "services", "layout": "three cards in a row", "texts": ["Cut", "Color", "Style"], "elements": ["3 image placeholders"], "notes": ""},
    {"kind": "booking", "layout": "calendar box", "texts": ["Book an appointment"], "elements": ["calendar"], "notes": ""}],
    "style_hints": {"colors": ["warm orange"], "mood": "friendly", "annotations": []}, "unreadable": []}

THEME = """@theme {
  --color-brand: #f5c542;
  --color-brand-ink: #ffffff;
  --color-surface: #fffaf3;
  --color-surface-alt: #fdeccf;
  --color-ink: #8a8a8a;
  --color-muted: #6b5d4a;
  --color-line: #ead9bd;
  --color-danger: #dc2626;
  --color-ok: #16a34a;
  --font-display: Georgia, serif;
  --font-body: ui-sans-serif, system-ui, sans-serif;
  --radius-card: 1.5rem;
  --radius-control: 999px;
}"""

PAGE_BROKEN = """=== FILE: src/pages/{name}.astro ===
---
import Base from '../layouts/Base.astro';
import Cards from '../components/sections/ServiceCards.astro';
import BookingWidget from '../components/BookingWidget.astro';
export const prerender = true;
---
<Base title="{title}">
  <section class="mx-auto max-w-6xl px-4 py-20"><h1 class="text-5xl font-bold">Fresh cuts, friendly faces</h1>
  <a href="/book" class="mt-6 inline-block rounded-control bg-brand px-6 py-3 font-semibold text-brand-ink">Book now</a></section>
  <Cards />
  <section class="mx-auto max-w-3xl px-4 pb-20"><BookingWidget /></section>
</Base>
=== END FILE ===
=== FILE: src/lib/db.ts ===
export const hacked = true;
=== END FILE ===
"""

PAGE_FIXED_LOCKED = "I fixed it.\n"  # the locked-file retry returns only the page again (below)

CARDS = """=== FILE: src/components/sections/ServiceCards.astro ===
---
import config from '../../../site.config.json';
---
<section class="bg-surface-alt"><div class="mx-auto grid max-w-6xl gap-6 px-4 py-16 sm:grid-cols-3">
  {config.services.map((s) => (<div class="rounded-card border border-line bg-surface p-6"><h3 class="text-xl font-bold">{s.name}</h3><p class="text-muted">{s.durationMin} min</p></div>))}
</div></section>
=== END FILE ===
"""

def reply_for(body: dict) -> str:
    msgs = body["messages"]
    last = msgs[-1]
    text = last["content"] if isinstance(last["content"], str) else " ".join(p.get("text", "") for p in last["content"] if p["type"] == "text")
    has_image = isinstance(last["content"], list) and any(p["type"] == "image_url" for p in last["content"])
    state["log"].append(("image" if has_image else "text", text[:60].replace("\n", " ")))

    if has_image and "Compare structure" in text:
        return json.dumps({"score": 9, "issues": []})
    if has_image:
        return "<think>hmm let me look</think>Sure! Here is the JSON:\n```json\n" + json.dumps(SPEC) + "\n```"
    if "Create the visual theme" in text:
        return "Here you go\n=== FILE: src/styles/theme.css ===\n" + THEME + "\n=== END FILE ==="
    if "were rejected" in text:
        # corrected re-send of only the page (locked file omitted)
        return PAGE_BROKEN.split("=== FILE: src/lib/db.ts ===")[0].format(name=state["name"], title=state["title"])
    if "failed to build" in text:
        state["repairs"] += 1
        return CARDS
    if "Build the page" in text:
        state["pages"] += 1
        import re
        m = re.search(r"Build the page `src/pages/([a-z0-9-]+)\.astro`", text)
        state["name"] = m.group(1)
        state["title"] = state["name"].title()
        return PAGE_BROKEN.format(name=state["name"], title=state["title"])
    if "Apply this change request" in text:
        return "=== FILE: src/pages/index.astro ===\n" + "---\nimport Base from '../layouts/Base.astro';\nexport const prerender = true;\n---\n<Base><h1>Edited</h1></Base>\n" + "=== END FILE ==="
    return "unexpected prompt"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._send({"data": [{"id": "qwen3-coder:30b"}, {"id": "qwen3-vl:8b"}]})
        elif self.path == "/_state":
            self._send(state)
        else:
            self._send({}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        self._send({"choices": [{"message": {"role": "assistant", "content": reply_for(body)}}]})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    HTTPServer(("127.0.0.1", port), H).serve_forever()
