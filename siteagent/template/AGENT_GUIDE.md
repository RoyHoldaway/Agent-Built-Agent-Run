# Site template guide (read fully before writing code)

You are editing a small-business website built on a fixed template.
Stack: **Astro 7** (server output, Node adapter) + **Tailwind CSS v4** + vanilla TypeScript/JS in `<script>` tags. No React/Vue, no jQuery, no CSS frameworks other than Tailwind, no external CDN scripts or fonts.

## What you may change
| Path | Rule |
|---|---|
| `src/pages/*.astro` | One file per page. Route = filename (`about.astro` -> `/about`, `index.astro` -> `/`). |
| `src/components/sections/*.astro` | Optional reusable sections. Only create one if used on 2+ pages; otherwise put markup in the page. |
| `src/components/Header.astro`, `Footer.astro` | May restyle. Keep nav driven by `config.nav`. |
| `src/styles/theme.css` | Design tokens (colors, fonts, radii). This is how you brand the site. |
| `site.config.json` | Business info, nav, services, hours. |

**Locked (writes are rejected):** `src/lib/**`, `src/pages/api/**`, `src/pages/admin/**`, `src/middleware.ts`, `src/components/BookingWidget.astro`, `src/layouts/Base.astro`, `astro.config.mjs`, `package.json`.
`src/pages/book.astro` may be restyled but MUST keep `<BookingWidget />`.

## Page skeleton (copy this shape exactly)
```astro
---
import Base from '../layouts/Base.astro';
import config from '../../site.config.json';
export const prerender = true;
---
<Base title="About" description="One sentence for search engines.">
  <section class="mx-auto max-w-6xl px-4 py-16"> ... </section>
</Base>
```
Imports are relative to the file (`../layouts/Base.astro` from `src/pages/`, `../../layouts/Base.astro` from `src/components/sections/`).
Pages that only show content MUST have `export const prerender = true;`.

## Design tokens (Tailwind v4 generates utilities from these)
Colors: `bg-brand text-brand-ink bg-surface bg-surface-alt text-ink text-muted border-line text-danger text-ok`
Fonts: `font-display` (headings) `font-body`. Radii: `rounded-card` `rounded-control`.
Use ONLY these color names so a re-theme restyles everything. Opacity modifiers are fine (`bg-brand/10`). Do not hardcode hex colors in pages.
To rebrand, rewrite `src/styles/theme.css`: keep a single `@theme { ... }` block with the same token names, change the values.

## Conventions
- Mobile first. Use responsive prefixes (`sm:` `md:` `lg:`). Container: `mx-auto max-w-6xl px-4`. Section spacing: `py-16 sm:py-24`.
- Semantic HTML: one `<h1>` per page, `<section>`, `<nav>`, `<ul>` for lists, real `<button>`/`<a>`. Images need `alt`. Buttons/links need visible focus (already global).
- Appointments: the working booking flow already exists. To show it inline on any page: `import BookingWidget from '../components/BookingWidget.astro';` then `<BookingWidget />` (at most once per page). Otherwise just link to `/book`. Never write your own booking form, calendar, or fetch calls to `/api/*`.
- Primary call-to-action buttons link to `/book`. Button style: `rounded-control bg-brand px-6 py-3 font-semibold text-brand-ink hover:opacity-90`.
- Images: the owner has not supplied photos yet. Use `<img src="/placeholder.svg" alt="..." class="aspect-[4/3] w-full rounded-card object-cover" loading="lazy">`. Never invent image URLs.
- Never invent facts: phone numbers, addresses, prices, hours, awards, testimonials, staff names. Use values from the brief/config; if missing, omit the element or write neutral copy.
- Business info comes from `config.business` (`name, tagline, phone, email, address`). Services/prices shown on the site should come from `config.services`.
- Small interactions (menu toggle, accordion, tabs) use plain JS in an Astro `<script>` tag, or CSS (`<details>`, `:target`). No libraries.
- Add a mobile menu only if the sketch shows one; the default Header already works on mobile.
- Keep each file focused (< 250 lines). Do not leave TODO comments or lorem ipsum.

## Output format (strict)
Return every file you create or change in full, using exactly this wrapper, and nothing else outside the wrappers except a one-line summary:
```
=== FILE: src/pages/about.astro ===
<full file contents>
=== END FILE ===
```
Never output partial files, diffs, or "rest unchanged" placeholders.
