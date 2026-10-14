import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from siteagent.llm import extract_json, strip_thinking  # noqa: E402
from siteagent.pipeline import route_for  # noqa: E402
from siteagent.project import Project, contrast, fix_theme_contrast, parse_files, parse_hours  # noqa: E402


@pytest.fixture()
def proj(tmp_path):
    return Project.create(tmp_path / "p", {"name": "Test Co", "services": [{"name": "Cut", "durationMin": 30, "price": 10}]})


PAGE = "---\nimport Base from '../layouts/Base.astro';\nexport const prerender = true;\n---\n<Base><h1>Hi</h1></Base>\n"


def test_route_for():
    assert route_for("Home") == ("/", "src/pages/index.astro")
    assert route_for("about us") == ("/about-us", "src/pages/about-us.astro")
    assert route_for("services_sketch") == ("/services", "src/pages/services.astro")
    assert route_for("Contact 2") == ("/contact", "src/pages/contact.astro")


def test_extract_json_handles_chatter_and_braces_in_strings():
    t = 'Sure!\n```json\n{"a": "x}y", "b": {"c": [1,2]}}\n```\nhope that helps'
    assert extract_json(t) == {"a": "x}y", "b": {"c": [1, 2]}}
    with pytest.raises(ValueError):
        extract_json("no json here")
    with pytest.raises(ValueError):
        extract_json('{"a": ')


def test_strip_thinking():
    assert strip_thinking("<think>blah\nblah</think>\nanswer") == "answer"
    assert strip_thinking("answer<think>never closed") == "answer"


def test_parse_files_fences_and_truncation():
    reply = "ok\n=== FILE: a.txt ===\n```astro\nhello\n```\n=== END FILE ===\n=== FILE: b.txt ===\ncut off"
    files, broken = parse_files(reply)
    assert files == {"a.txt": "hello\n"}
    assert "b.txt" in broken


def test_contrast_fix():
    css = "@theme { --color-brand: #f5c542; --color-brand-ink: #ffffff; --color-ink: #999999; --color-surface: #ffffff; }"
    out, notes = fix_theme_contrast(css)
    assert len(notes) == 2
    assert "--color-brand-ink: #0f172a" in out
    assert contrast("#0f172a", "#f5c542") > 4.5


def test_parse_hours():
    h = parse_hours("mon-fri=09:00-17:00,sat=10:00-14:00")
    assert h["wed"] == ["09:00", "17:00"] and h["sat"] == ["10:00", "14:00"] and h["sun"] is None
    with pytest.raises(ValueError):
        parse_hours("funday=1-2")


@pytest.mark.parametrize("path", [
    "src/lib/db.ts", "src/pages/api/book.ts", "src/pages/admin/index.astro", "src/middleware.ts",
    "src/components/BookingWidget.astro", "src/layouts/Base.astro", "package.json", "astro.config.mjs",
    "../outside.astro", "src/pages/../lib/db.ts", "src/pages/Upper.astro", "src/pages/sub/dir.astro", ".env",
])
def test_locked_and_escaping_paths_rejected(proj, path):
    res = proj.apply({path: PAGE})
    assert path.removeprefix("./") in res.rejected and not res.written


def test_good_page_written_and_bad_content_rejected(proj):
    assert proj.apply({"src/pages/about.astro": PAGE}).written == ["src/pages/about.astro"]
    bad = {
        "src/pages/a.astro": PAGE.replace("<h1>", "<script src='https://cdn.example.com/x.js'></script><h1>"),
        "src/pages/b.astro": PAGE.replace("<h1>", "<h1>{process.env.ADMIN_PASSWORD}"),
        "src/pages/c.astro": "---\n---\n<h1>no layout</h1>",
        "src/pages/book.astro": PAGE,  # lost the BookingWidget
        "src/pages/d.astro": PAGE.replace("<h1>", "<BookingWidget /><BookingWidget /><h1>"),
        "src/styles/theme.css": ":root{}",
        "src/components/Footer.astro": "<link href='https://fonts.googleapis.com/css' rel='stylesheet'>",
    }
    res = proj.apply(bad)
    assert set(res.rejected) == set(bad), res


def test_config_merge_and_validation(proj):
    ok = proj.apply({"site.config.json": json.dumps({"business": {"tagline": "New line"}})})
    assert ok.written and proj.config()["business"]["tagline"] == "New line"
    assert proj.config()["business"]["name"] == "Test Co"  # merged, not replaced
    for bad in [
        {"nav": [{"label": "x", "href": "nope"}]},
        {"services": []},
        {"services": [{"name": "x", "durationMin": "30"}]},
        {"hours": {"funday": ["09:00", "10:00"]}},
        {"hours": {"mon": ["9am", "5pm"]}},
        {"hours": {"mon": ["17:00", "09:00"]}},
        {"secrets": {"a": 1}},
    ]:
        assert proj.apply({"site.config.json": json.dumps(bad)}).rejected, bad
    assert proj.apply({"site.config.json": "not json"}).rejected


def test_nav_is_deterministic(proj):
    proj.apply({"src/pages/about.astro": PAGE, "src/pages/services.astro": PAGE})
    proj.set_nav_from_pages()
    assert [n["href"] for n in proj.config()["nav"]] == ["/", "/about", "/services", "/book"]
