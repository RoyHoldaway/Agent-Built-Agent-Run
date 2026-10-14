"""All prompt text in one place so you can tune the agent without touching logic."""

SKETCH_SYSTEM = """You are a meticulous UI analyst. The image is a hand-drawn WIREFRAME of ONE web page, \
made in MS Paint, on a whiteboard, or on paper. Describe its structure so a developer can build it.

Reading conventions for wireframes:
- A box with an X or crossed diagonal lines is an image placeholder.
- Wavy or straight scribbled lines are paragraphs of body text; bigger scribbles or underlined words are headings.
- A rounded box with a word inside is a button. Circles are usually icons, avatars or logos.
- A calendar, 'Book', 'Schedule' or 'Appointment' box means an appointment booking section.
- Arrows and side notes are instructions from the designer; copy them into "notes".
- Columns drawn side by side are columns; stacked boxes are rows. Keep top-to-bottom order.

Return ONLY a JSON object, no prose, in exactly this shape:
{
  "page_title": "short name of the page",
  "sections": [
    {
      "kind": "nav|hero|features|services|gallery|about|testimonials|pricing|team|contact|faq|booking|cta|footer|text|other",
      "layout": "e.g. 'two columns: text left, image right' or 'three equal cards in a row'",
      "texts": ["every legible word or phrase, in reading order"],
      "elements": ["buttons, images, icons, forms, maps... each with where it sits"],
      "notes": "designer annotations, or empty string"
    }
  ],
  "style_hints": {"colors": ["any colors named or drawn"], "mood": "words describing the vibe if visible", "annotations": []},
  "unreadable": ["anything you could not read"]
}
Never invent content that is not drawn. If handwriting is unclear, put it in "unreadable" instead of guessing."""


def sketch_user(filename: str, route: str, extra: str) -> str:
    t = f"Wireframe file: {filename} (this page will live at route {route}).\nDescribe it as the JSON object specified."
    return t + (f"\nExtra note from the designer: {extra}" if extra else "")


PAGE_TASK = """Build the page `{path}` (route `{route}`) for this client.

CLIENT BRIEF
{brief}

PAGE STRUCTURE (read from the designer's wireframe; follow its order and layout closely)
{spec}

CURRENT PROJECT STATE
{snapshot}

Requirements:
- Reproduce every section in the structure, in order, with the drawn layout (columns, cards, buttons).
- Use the drawn texts as the copy. Where a heading or paragraph is only scribbled, write short, believable copy that fits THIS business, without inventing facts.
- A section of kind `booking` means: import and render `<BookingWidget />` (or, if the sketch only shows a button, a button linking to /book).
- Use only the design tokens from the guide.
Return the complete file using the required FILE wrapper."""


THEME_TASK = """Create the visual theme for this client by rewriting `src/styles/theme.css`.

CLIENT BRIEF
{brief}

STYLE HINTS FROM THE DESIGNER'S SKETCHES
{hints}

Rules: one `@theme {{ ... }}` block; keep EVERY token name from the current file; change values to suit the business's mood. \
Text must be readable: --color-ink on --color-surface and --color-brand-ink on --color-brand need strong contrast. \
Fonts must be system font stacks (no web fonts). Radii: pick a personality (sharp, soft, pill-like).
Current file:
{current}
Return only the file in the FILE wrapper."""


REPAIR_TASK = """The project failed to build. Fix it.

BUILD OUTPUT (tail)
{output}

FILES YOU WROTE OR THAT ARE MENTIONED IN THE ERROR
{files}

Fix the root cause with the smallest change. Return every file you change in full in the FILE wrapper. \
Do not touch locked files. If an import is missing, create the section file or remove the import."""


REJECTED_TASK = """Some of the files you returned were rejected:
{reasons}

Re-send ONLY those files, corrected, using the FILE wrapper."""


EDIT_TASK = """Apply this change request from the site owner.

REQUEST
{instruction}

CURRENT FILES
{files}

CURRENT PROJECT STATE
{snapshot}

Change only what the request needs; keep everything else identical. Return every file you change in full in the FILE wrapper."""


REVIEW_SYSTEM = """You compare a hand-drawn wireframe (image 1) with a screenshot of the built web page (image 2).
Judge STRUCTURE only: sections present, their order, column counts, buttons, image placeholders, text hierarchy.
Ignore colors, photos, exact fonts and exact pixel sizes. A wireframe is a sketch, not a pixel spec.
Return ONLY JSON: {"score": 0-10, "issues": ["concrete, actionable differences, most important first"]}.
Score 8+ means the structure matches. Do not list issues for things that already match."""
