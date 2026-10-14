from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from .config import Settings
from .images import IMAGE_EXTS, find_images
from .llm import LLM
from .pipeline import Agent
from .project import Project, parse_hours

CLIENTS = Path("clients")


def _slug(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _services(items: list[str]) -> list[dict]:
    out = []
    for it in items:
        parts = [p.strip() for p in it.split(":")]
        if len(parts) < 2 or not parts[1].isdigit():
            raise SystemExit(f"bad --service {it!r}; use 'Name:minutes[:price]' e.g. 'Haircut:45:35'")
        out.append({"name": parts[0], "durationMin": int(parts[1]), "price": float(parts[2]) if len(parts) > 2 else 0})
    return out


def _open(slug: str) -> Project:
    try:
        return Project.open(CLIENTS / slug)
    except FileNotFoundError as e:
        raise SystemExit(str(e))


def cmd_new(a):
    brief = {
        "name": a.name, "industry": a.industry, "tagline": a.tagline or "", "about": a.about or "",
        "phone": a.phone or "", "email": a.email or "", "address": a.address or "",
        "tone": a.tone or "", "timezone": a.timezone,
        "services": _services(a.service),
    }
    if a.hours:
        brief["hours"] = parse_hours(a.hours)
    brief = {k: v for k, v in brief.items() if v}
    slug = a.slug or _slug(a.name)
    p = Project.create(CLIENTS / slug, brief)
    print(f"Created {p.root}\n"
          f"  1. Put sketches in   {p.state / 'sketches'}   (name them home.png, about.png, services.png ...)\n"
          f"  2. Run               siteagent design {slug}")


def cmd_design(a):
    p = _open(a.slug)
    paths = [Path(x) for x in a.sketch] if a.sketch else find_images(p.state / "sketches")
    paths = [x for x in paths if x.suffix.lower() in IMAGE_EXTS]
    if not paths:
        raise SystemExit(f"No sketches found. Add images to {p.state / 'sketches'} or pass paths.")
    dest, ready = p.state / "sketches", []
    for x in paths:
        if not x.is_file():
            raise SystemExit(f"not a file: {x}")
        target = dest / x.name
        if x.resolve() != target.resolve():
            shutil.copy(x, target)  # keep originals with the project
        ready.append(target)
    paths = ready
    ok = Agent(p, Settings.load()).design(paths, review_rounds=a.review, notes=a.note or "")
    print("\nDone. Preview with:  siteagent dev " + a.slug if ok else
          f"\nBuild still failing. See {p.state / 'last-build-error.txt'}, then try: siteagent edit {a.slug} \"fix the build error\"")
    sys.exit(0 if ok else 1)


def cmd_edit(a):
    p = _open(a.slug)
    files = a.file or [f for f in p.pages() if not f.endswith(("book.astro",))] + ["src/styles/theme.css"]
    agent = Agent(p, Settings.load())
    changed = agent.edit(a.instruction, files)
    ok = bool(changed) and agent.build_and_repair(changed)
    p.commit(f"Edit: {a.instruction[:60]}")
    sys.exit(0 if ok else 1)


def cmd_build(a):
    ok, out = Agent(_open(a.slug), Settings.load()).build()
    print(out if not ok else "build ok")
    sys.exit(0 if ok else 1)


def cmd_dev(a):
    p = _open(a.slug)
    if not (p.root / "node_modules").is_dir():
        subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=p.root, check=True)
    env_note = "Admin login in dev: password 'dev-password' at /admin"
    print(env_note)
    subprocess.run(["npm", "run", "dev", "--", "--open"], cwd=p.root)


def cmd_doctor(_):
    s = Settings.load()
    print(f"model server : {s.base_url}\ntext model   : {s.text_model}\nvision model : {s.vision_model}")
    ok = True
    try:
        models = LLM(s).list_models()
        for label, m in (("text", s.text_model), ("vision", s.vision_model)):
            have = m in models or any(x.split(":")[0] == m.split(":")[0] for x in models)
            print(f"  {label} model {'found' if have else 'NOT FOUND'}: {m}")
            ok &= have
        if not ok:
            print(f"  available: {', '.join(models) or '(none)'}\n  -> `ollama pull <name>` or edit .env")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"  cannot reach model server: {e}")
    for tool in ("node", "npm", "git"):
        path = shutil.which(tool)
        print(f"  {tool}: {path or 'MISSING'}")
        ok &= bool(path) or tool == "git"
    try:
        import playwright  # noqa: F401
        print("  playwright: installed (review loop available)")
    except ImportError:
        print("  playwright: not installed (optional; needed for --review)")
    sys.exit(0 if ok else 1)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="siteagent", description="Turn a client brief + hand-drawn sketches into a booking-enabled website.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="start a client project")
    n.add_argument("name"); n.add_argument("--slug")
    n.add_argument("--industry", required=True, help="e.g. 'hair salon'")
    n.add_argument("--tagline"); n.add_argument("--about", help="a few sentences about the business")
    n.add_argument("--phone"); n.add_argument("--email"); n.add_argument("--address")
    n.add_argument("--tone", help="e.g. 'warm, modern, earthy colors'")
    n.add_argument("--timezone", default="America/Denver")
    n.add_argument("--service", action="append", default=[], help="'Name:minutes[:price]' (repeatable)")
    n.add_argument("--hours", help="'mon-fri=09:00-17:00,sat=10:00-14:00' (days not listed are closed)")
    n.set_defaults(fn=cmd_new)

    d = sub.add_parser("design", help="sketches -> pages")
    d.add_argument("slug"); d.add_argument("sketch", nargs="*", help="image paths (default: everything in the project's sketches folder)")
    d.add_argument("--review", type=int, default=0, metavar="ROUNDS", help="screenshot-vs-sketch fix rounds per page (needs playwright)")
    d.add_argument("--note", help="extra instruction passed to the sketch reader")
    d.set_defaults(fn=cmd_design)

    e = sub.add_parser("edit", help="change an existing site with plain English")
    e.add_argument("slug"); e.add_argument("instruction")
    e.add_argument("--file", action="append", help="limit to these files (repeatable)")
    e.set_defaults(fn=cmd_edit)

    b = sub.add_parser("build"); b.add_argument("slug"); b.set_defaults(fn=cmd_build)
    v = sub.add_parser("dev", help="run the dev server"); v.add_argument("slug"); v.set_defaults(fn=cmd_dev)
    sub.add_parser("doctor", help="check model server and tools").set_defaults(fn=cmd_doctor)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
