"""The agent loop.

  sketch image --(vision model)--> page spec JSON --(coder model)--> .astro file
        --> guarded write --> npm build --> [errors -> coder repairs, up to N times]
        --> optional: screenshot vs sketch -> vision critique -> coder revises

Principle: the model only does what needs judgment (reading drawings, writing markup).
Everything checkable (file permissions, nav, contrast, build success) is plain code.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from . import prompts
from .config import Settings
from .images import prepare
from .llm import LLM, image_message
from .project import Project, parse_files

Log = Callable[[str], None]
GUIDE_NAME = "AGENT_GUIDE.md"


def route_for(stem: str) -> tuple[str, str]:
    """Sketch filename -> (route, page file). home.png / index.png -> '/', 'about us.png' -> '/about-us'."""
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "page"
    slug = re.sub(r"-?(sketch|wireframe|draft|\d+)$", "", slug) or slug
    if slug in {"home", "index", "homepage", "main", "landing"}:
        return "/", "src/pages/index.astro"
    return f"/{slug}", f"src/pages/{slug}.astro"


class Agent:
    def __init__(self, project: Project, settings: Settings, log: Log = print):
        self.p = project
        self.s = settings
        self.llm = LLM(settings)
        self.log = log
        self.guide = (project.root / GUIDE_NAME).read_text(encoding="utf-8")

    # ------------------------------------------------------------------ vision
    def read_sketch(self, image: Path, extra: str = "") -> dict:
        route, _ = route_for(image.stem)
        self.log(f"  reading sketch {image.name} -> {route}")
        msgs = [
            {"role": "system", "content": prompts.SKETCH_SYSTEM},
            image_message(prompts.sketch_user(image.name, route, extra), [prepare(image)]),
        ]
        spec = self.llm.chat_json(msgs, vision=True)
        if not isinstance(spec.get("sections"), list) or not spec["sections"]:
            raise RuntimeError(f"vision model found no sections in {image.name}; try a clearer/larger drawing")
        out = self.p.state / "specs" / f"{image.stem}.json"
        out.write_text(json.dumps(spec, indent=2), encoding="utf-8")
        return spec

    # ------------------------------------------------------------------ code generation
    def _system(self) -> dict:
        return {"role": "system", "content": "You are a senior front-end engineer.\n\n" + self.guide}

    def _generate(self, task: str, label: str) -> list[str]:
        """Send a task, write the files it returns (with one retry for rejected files). Returns written paths."""
        msgs = [self._system(), {"role": "user", "content": task}]
        written: list[str] = []
        for attempt in range(3):
            reply = self.llm.chat(msgs)
            files, broken = parse_files(reply)
            res = self.p.apply(files)
            written += [w for w in res.written if w not in written]
            rejected = {**broken, **res.rejected}
            for path in res.written:
                self.log(f"    wrote {path}")
            if not files and not broken:
                rejected = {"(none)": "no files in the required '=== FILE: path ===' wrapper"}
            if not rejected:
                return written
            for path, why in rejected.items():
                self.log(f"    rejected {path}: {why}")
            reasons = "\n".join(f"- {p}: {w}" for p, w in rejected.items())
            msgs += [{"role": "assistant", "content": reply},
                     {"role": "user", "content": prompts.REJECTED_TASK.format(reasons=reasons)}]
        self.log(f"  ! {label}: some files could not be written after retries")
        return written

    def make_theme(self, hints: list[dict]) -> None:
        self.log("• theme")
        task = prompts.THEME_TASK.format(
            brief=json.dumps(self.p.brief, indent=2),
            hints=json.dumps(hints, indent=2) if hints else "(none; choose something suitable for the business)",
            current=self.p.read("src/styles/theme.css"),
        )
        self._generate(task, "theme")

    def make_page(self, route: str, path: str, spec: dict) -> list[str]:
        self.log(f"• page {route}")
        task = prompts.PAGE_TASK.format(
            path=path, route=route, brief=json.dumps(self.p.brief, indent=2),
            spec=json.dumps({k: v for k, v in spec.items() if k != "style_hints"}, indent=2),
            snapshot=self.p.snapshot(),
        )
        return self._generate(task, path)

    def edit(self, instruction: str, files: list[str]) -> list[str]:
        self.log("• edit")
        blob = "\n\n".join(f"=== FILE: {f} ===\n{self.p.read(f)}\n=== END FILE ===" for f in files)
        return self._generate(prompts.EDIT_TASK.format(instruction=instruction, files=blob, snapshot=self.p.snapshot()), "edit")

    # ------------------------------------------------------------------ build + repair
    def build(self) -> tuple[bool, str]:
        root = self.p.root
        env = {
            **os.environ,
            "ASTRO_TELEMETRY_DISABLED": "1",
            "NO_COLOR": "1",
        }

        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if not npm:
            return False, "npm not found. Install Node.js from nodejs.org."

        if not (root / "node_modules").is_dir():
            self.log("  installing npm packages (first build only)…")
            r = subprocess.run(
                ["cmd", "/c", "npm.cmd", "install", "--no-audit", "--no-fund"],
                cwd=root,
                capture_output=True,
                text=True,
                env=env,
            )
            if r.returncode:
                return False, (r.stdout + "\n" + r.stderr)[-3000:]

        r = subprocess.run(
            ["cmd", "/c", "npm.cmd", "run", "build"],
            cwd=root,
            capture_output=True,
            text=True,
            env=env,
        )
        return r.returncode == 0, (r.stdout + "\n" + r.stderr)[-4000:]

    def build_and_repair(self, touched: list[str]) -> bool:
        touched = list(touched)
        for attempt in range(self.s.max_repairs + 1):
            self.log("  building…")
            ok, out = self.build()
            if ok:
                self.log("  build ok")
                return True
            self.log(f"  build failed (attempt {attempt + 1}/{self.s.max_repairs + 1})")
            if attempt == self.s.max_repairs:
                (self.p.state / "last-build-error.txt").write_text(out, encoding="utf-8")
                self.log(f"  ! giving up; error saved to {self.p.state / 'last-build-error.txt'}")
                return False
            mentioned = [m for m in dict.fromkeys(re.findall(r"(?:src/[\w./-]+|site\.config\.json)", out))
                         if (self.p.root / m).is_file()]
            names = list(dict.fromkeys([*mentioned, *touched]))[:5]
            blob = "\n\n".join(f"=== FILE: {f} ===\n{self.p.read(f)}\n=== END FILE ===" for f in names)
            touched += self._generate(prompts.REPAIR_TASK.format(output=out[-3000:], files=blob), "repair")
        return False

    # ------------------------------------------------------------------ visual review
    def review(self, sketch: Path, route: str, path: str, rounds: int = 1) -> None:
        from .preview import screenshots  # optional dependency, import lazily
        for i in range(rounds):
            self.log(f"• review {route} (round {i + 1})")
            shots = screenshots(self.p.root, route, self.p.state / "shots", Path(path).stem)
            msgs = [
                {"role": "system", "content": prompts.REVIEW_SYSTEM},
                image_message("Image 1 is the wireframe. Image 2 is the built page. Compare structure.",
                              [prepare(sketch), prepare(shots["desktop"])]),
            ]
            verdict = self.llm.chat_json(msgs, vision=True)
            issues = [str(x) for x in verdict.get("issues", [])]
            if shots.get("mobile_overflow"):
                issues.append("On a 390px-wide phone the page scrolls sideways. Fix overflowing elements (use flex-wrap, min-w-0, responsive grid columns).")
            score = verdict.get("score", "?")
            self.log(f"  structure score {score}/10, {len(issues)} issue(s)")
            if (isinstance(score, (int, float)) and score >= 8 and not issues) or not issues:
                return
            files = [path, *self.p.sections()[:6]]
            changed = self.edit("Fix these differences from the wireframe:\n- " + "\n- ".join(issues), files)
            if not changed or not self.build_and_repair(changed):
                return

    # ------------------------------------------------------------------ top level
    # ------------------------------------------------------------------ top level
    def design(self, sketches: list[Path], review_rounds: int = 0, notes: str = "") -> bool:
        import time

        started = time.time()
        total_stages = 5 + (1 if review_rounds else 0)
        stage = 0

        def progress(label: str) -> None:
            nonlocal stage
            stage += 1
            percent = int(stage / total_stages * 100)
            filled = int(percent / 5)
            bar = "#" * filled + "-" * (20 - filled)
            elapsed = time.time() - started
            print(
                f"\n[{bar}] {percent:3d}% | {label} | "
                f"{elapsed:.0f}s elapsed",
                flush=True,
            )

        print(f"Starting site design for {len(sketches)} sketch(es).", flush=True)

        specs: list[tuple[Path, str, str, dict]] = []

        progress("Analyzing sketches")
        for i, img in enumerate(sketches, start=1):
            print(f"  Sketch {i}/{len(sketches)}: {img.name}", flush=True)
            route, path = route_for(img.stem)
            specs.append((img, route, path, self.read_sketch(img, notes)))

        progress("Generating theme")
        hints = [s.get("style_hints") for *_, s in specs if s.get("style_hints")]
        self.make_theme(hints)

        touched: list[str] = ["src/styles/theme.css"]

        progress("Generating pages")
        for i, (img, route, path, spec) in enumerate(specs, start=1):
            print(f"  Page {i}/{len(specs)}: {route}", flush=True)
            touched += self.make_page(route, path, spec)

        progress("Building and repairing site")
        self.p.set_nav_from_pages()
        ok = self.build_and_repair(touched)

        self.p.commit(
            f"Design from {len(sketches)} sketch(es)"
            + ("" if ok else " [build failing]")
        )

        if ok and review_rounds:
            progress("Visual review")
            for img, route, path, _ in specs:
                self.review(img, route, path, review_rounds)
            self.p.commit("Visual review fixes")

        if not ok:
            print("Site build failed. Check the errors above.", flush=True)

        elapsed = time.time() - started
        print(
            f"\nDesign {'completed successfully' if ok else 'finished with errors'} "
            f"in {elapsed / 60:.1f} minutes.",
            flush=True,
        )
        return ok