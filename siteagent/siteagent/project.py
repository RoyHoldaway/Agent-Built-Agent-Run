"""A client project = a copy of the template + a hidden .siteagent/ folder (brief, specs, screenshots).

The most important job of this file is *guarding the template*: the model may only write to
an allow-list of design files, and each write is validated. The booking engine, admin panel,
auth and API are locked, so a confused model can't break the parts that must always work.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"
STATE = ".siteagent"
MAX_FILE_BYTES = 60_000

ALLOW = [
    re.compile(r"^src/pages/[a-z0-9][a-z0-9-]*\.astro$"),
    re.compile(r"^src/components/sections/[A-Za-z][A-Za-z0-9]*\.astro$"),
    re.compile(r"^src/components/(Header|Footer)\.astro$"),
    re.compile(r"^src/styles/theme\.css$"),
    re.compile(r"^site\.config\.json$"),
]
FORBIDDEN_IN_ASTRO = [
    (re.compile(r"node:|process\.env|import\.meta\.env"), "server secrets/node APIs are not allowed in design files"),
    (re.compile(r"/api/admin"), "design files must not touch admin APIs"),
    (re.compile(r"<script[^>]+src=[\"']?(https?:)?//", re.I), "no external <script src>"),
    (re.compile(r"<link[^>]+href=[\"']?(https?:)?//", re.I), "no external <link href> (fonts/CDNs)"),
    (re.compile(r"@import\s+url\(", re.I), "no external @import"),
]
CONFIG_KEYS = {"business", "nav", "services", "booking", "hours"}
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class ApplyResult:
    written: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)


FILE_RE = re.compile(r"^=== FILE: ([^\n]+?) ===[ \t]*\n(.*?)\n=== END FILE ===", re.S | re.M)


def parse_files(reply: str) -> tuple[dict[str, str], dict[str, str]]:
    """Return ({path: content}, {path: problem}) from the model's reply."""
    files: dict[str, str] = {}
    for m in FILE_RE.finditer(reply):
        body = m.group(2)
        body = re.sub(r"\A\s*```[a-zA-Z]*\n", "", body)   # models love wrapping in fences anyway
        body = re.sub(r"\n```\s*\Z", "", body)
        files[m.group(1).strip()] = body.rstrip() + "\n"
    broken = {}
    for p in re.findall(r"^=== FILE: ([^\n]+?) ===", reply, re.M):
        if p.strip() not in files:
            broken[p.strip()] = "output was cut off or missing '=== END FILE ===' (send the complete file)"
    return files, broken


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    ch = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in ch]
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def fix_theme_contrast(css: str) -> tuple[str, list[str]]:
    """Deterministic a11y guard: model-picked colors often fail WCAG. Fix the two pairs that matter most."""
    notes: list[str] = []

    def get(name: str) -> str | None:
        m = re.search(rf"--color-{name}\s*:\s*(#[0-9a-fA-F]{{3,6}})\s*;", css)
        return m.group(1) if m else None

    def put(name: str, value: str) -> None:
        nonlocal css
        css = re.sub(rf"(--color-{name}\s*:\s*)#[0-9a-fA-F]{{3,6}}", rf"\g<1>{value}", css)

    brand, bink = get("brand"), get("brand-ink")
    if brand and bink and contrast(brand, bink) < 4.5:
        best = max(("#ffffff", "#0f172a"), key=lambda c: contrast(brand, c))
        put("brand-ink", best)
        notes.append(f"button text {bink} on {brand} failed contrast; changed to {best}")
    ink, surface = get("ink"), get("surface")
    if ink and surface and contrast(ink, surface) < 7:
        best = "#0f172a" if _luminance(surface) > 0.5 else "#f8fafc"
        put("ink", best)
        notes.append(f"body text {ink} on {surface} was low contrast; changed to {best}")
    return css, notes


def _deep_merge(base: dict, new: dict) -> dict:
    out = dict(base)
    for k, v in new.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def parse_hours(spec: str) -> dict:
    """'mon-fri=09:00-17:00,sat=10:00-14:00' -> {"mon": [..], ..., "sun": None}"""
    out: dict = {d: None for d in DAYS}
    for part in filter(None, (p.strip() for p in spec.split(","))):
        days, _, rng = part.partition("=")
        a, _, b = days.partition("-")
        a, b = a.strip().lower()[:3], (b or a).strip().lower()[:3]
        if a not in DAYS or b not in DAYS or "-" not in rng:
            raise ValueError(f"bad hours entry {part!r}; use e.g. mon-fri=09:00-17:00")
        o, c = (x.strip() for x in rng.split("-", 1))
        for d in DAYS[DAYS.index(a):DAYS.index(b) + 1]:
            out[d] = [o, c]
    return out


class Project:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.state = self.root / STATE

    # ---------- lifecycle ----------
    @classmethod
    def create(cls, root: Path, brief: dict) -> "Project":
        if root.exists() and any(root.iterdir()):
            raise FileExistsError(f"{root} already exists and is not empty")
        shutil.copytree(TEMPLATE_DIR, root, ignore=shutil.ignore_patterns("node_modules", "dist", ".astro", "data", ".env"))
        p = cls(root)
        for sub in ("sketches", "specs", "shots"):
            (p.state / sub).mkdir(parents=True, exist_ok=True)
        (p.state / "brief.json").write_text(json.dumps(brief, indent=2), encoding="utf-8")
        p._seed_config(brief)
        p.git("init", "-q")
        p.commit("Initial template + client brief")
        return p

    @classmethod
    def open(cls, root: Path) -> "Project":
        p = cls(root)
        if not (p.state / "brief.json").is_file():
            raise FileNotFoundError(f"{root} is not a siteagent project (no {STATE}/brief.json)")
        return p

    @property
    def brief(self) -> dict:
        return json.loads((self.state / "brief.json").read_text(encoding="utf-8"))

    def _seed_config(self, brief: dict) -> None:
        cfg = self.config()
        b = cfg["business"]
        for k in ("name", "tagline", "phone", "email", "address", "timezone"):
            if brief.get(k):
                b[k] = brief[k]
        if brief.get("services"):
            cfg["services"] = brief["services"]
        if brief.get("hours"):
            cfg["hours"] = brief["hours"]
        self.write_config(cfg)

    # ---------- reading ----------
    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def config(self) -> dict:
        return json.loads(self.read("site.config.json"))

    def write_config(self, cfg: dict) -> None:
        (self.root / "site.config.json").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

    def pages(self) -> list[str]:
        return sorted(str(p.relative_to(self.root)) for p in (self.root / "src/pages").glob("*.astro"))

    def sections(self) -> list[str]:
        return sorted(str(p.relative_to(self.root)) for p in (self.root / "src/components/sections").glob("*.astro"))

    def snapshot(self) -> str:
        """What the model should know about the current state, kept short."""
        return (
            f"Existing pages: {', '.join(self.pages()) or 'none'}\n"
            f"Existing section components: {', '.join(self.sections()) or 'none'}\n\n"
            f"--- src/styles/theme.css ---\n{self.read('src/styles/theme.css')}\n"
            f"--- site.config.json ---\n{self.read('site.config.json')}"
        )

    # ---------- guarded writing ----------
    def apply(self, files: dict[str, str]) -> ApplyResult:
        res = ApplyResult()
        for raw, content in files.items():
            rel = raw.strip().replace("\\", "/").removeprefix("./")
            reason = self._check(rel, content)
            if reason:
                res.rejected[rel] = reason
                continue
            if rel == "site.config.json":
                content = json.dumps(_deep_merge(self.config(), json.loads(content)), indent=2) + "\n"
            if rel == "src/styles/theme.css":
                content, notes = fix_theme_contrast(content)
                for n in notes:
                    print(f"    a11y fix: {n}")
            dest = self.root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            res.written.append(rel)
        return res

    def _check(self, rel: str, content: str) -> str | None:
        if ".." in rel.split("/") or rel.startswith("/"):
            return "path escapes the project"
        if not any(r.match(rel) for r in ALLOW):
            return "path is locked or not in the allowed list (see guide)"
        if len(content.encode()) > MAX_FILE_BYTES:
            return f"file too large (> {MAX_FILE_BYTES // 1000} KB)"
        if not content.strip():
            return "empty file"
        if rel.endswith(".astro"):
            for rx, why in FORBIDDEN_IN_ASTRO:
                if rx.search(content):
                    return why
            if rel.startswith("src/pages/") and "layouts/Base.astro" not in content:
                return "pages must import and use layouts/Base.astro"
            if rel == "src/pages/book.astro" and "BookingWidget" not in content:
                return "book.astro must keep <BookingWidget />"
            if content.count("<BookingWidget") > 1:
                return "<BookingWidget /> may appear only once per page"
        if rel.endswith(".css"):
            if "@theme" not in content:
                return "theme.css must contain an @theme { ... } block"
            if FORBIDDEN_IN_ASTRO[-1][0].search(content):
                return FORBIDDEN_IN_ASTRO[-1][1]
        if rel == "site.config.json":
            try:
                new = json.loads(content)
            except json.JSONDecodeError as e:
                return f"invalid JSON: {e}"
            if not isinstance(new, dict) or not set(new) <= CONFIG_KEYS:
                return f"top-level keys must be within {sorted(CONFIG_KEYS)}"
            merged = _deep_merge(self.config(), new)
            if not isinstance(merged["nav"], list) or not all(
                isinstance(n, dict) and str(n.get("href", "")).startswith("/") and n.get("label") for n in merged["nav"]
            ):
                return "nav must be a list of {label, href} with hrefs starting with /"
            if not isinstance(merged["services"], list) or not merged["services"] or not all(
                isinstance(s, dict) and s.get("name") and isinstance(s.get("durationMin"), int) and s["durationMin"] > 0
                for s in merged["services"]
            ):
                return "services must be a non-empty list of {name, durationMin:int, price}"
            if set(merged["hours"]) != set(DAYS):
                return "hours must have exactly mon..sun keys"
            tm = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
            for day, rng in merged["hours"].items():
                if rng is None:
                    continue
                if not (isinstance(rng, list) and len(rng) == 2 and all(isinstance(x, str) and tm.match(x) for x in rng)
                        and rng[0] < rng[1]):
                    return f"hours.{day} must be null (closed) or [\"HH:MM\", \"HH:MM\"] with open before close"
        return None

    # ---------- git (cheap undo for every step) ----------
    def git(self, *args: str) -> bool:
        try:
            r = subprocess.run(
                ["git", "-c", "user.name=siteagent", "-c", "user.email=siteagent@local", *args],
                cwd=self.root, capture_output=True, text=True, timeout=60,
            )
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def commit(self, message: str) -> None:
        if self.git("add", "-A"):
            self.git("commit", "-q", "-m", message)

    def set_nav_from_pages(self) -> None:
        """Deterministic nav: Home, other pages A-Z, Book last. Models are bad at keeping this in sync."""
        names = [Path(p).stem for p in self.pages()]
        order = ["index"] + sorted(n for n in names if n not in ("index", "book")) + (["book"] if "book" in names else [])
        nav = [{"label": "Home" if n == "index" else n.replace("-", " ").title(), "href": "/" if n == "index" else f"/{n}"}
               for n in order if n in names]
        cfg = self.config()
        cfg["nav"] = nav
        self.write_config(cfg)
