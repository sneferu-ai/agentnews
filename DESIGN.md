# DESIGN — AgentNews

## 0. Soul

A precise, technical, direct research newsroom: proof leads, pitch follows,
one reading column, machine values stay machine-legible, no-script is the
architecture. Full soul brief in [`SOUL.md`](./SOUL.md).

## 1. Reference anchor

**URL:** https://stripe.com/docs (Stripe Docs, the currently-shipping product
page — the docs reading surface, not a marketing screenshot).

**Why this one.** AgentNews articles and Stripe Docs share the same shape: a
question (title), findings (body), references (structured list), and a
certificate panel (metadata block). Stripe Docs is the canonical example of an
editorial reading column with real typographic hierarchy, code blocks that copy
cleanly, in-body links that underline rather than button-ify, and a footer link
row that stays out of the way. The brand direction (`BRAND_ASSETS/brand-direction.svg`)
owns look/feel (dark, amber/teal, aperture shape, editorial asymmetry); Stripe
Docs teaches the implementation patterns that carry that look into a
zero-JavaScript server-rendered page.

**Five pixel-level patterns mirrored:**

1. **Reading column width.** Stripe Docs constrain body text to ~680px. We use
   `max-width: var(--measure)` (`66ch` at the 16px base). This prevents
   line lengths that tire the eye and creates natural left/right margins on
   wider screens. Article body and index summaries both obey it.
2. **Code block treatment.** Stripe uses a subtle warm-gray background, ~14px
   monospace, 16px padding, no border, small radius. We mirror: a dedicated
   `--bg-code` surface, `var(--fs-sm)` monospace, `var(--space-4)` padding,
   `var(--radius-sm)`, no border. Selection by triple-click; no JS copy button
   (FR-035 forbids it).
3. **Section heading rhythm.** Stripe's h2 sections are separated by 40–48px of
   vertical space, with the heading at 20–24px weight 600. We use
   `var(--space-6)` (40px) section separation and an h2 at `var(--fs-xl)`
   weight `var(--weight-semibold)` in the display serif face.
4. **In-body link treatment.** Stripe's in-body links use a brand-colored
   underline that darkens on hover, never a button-styled link. We mirror:
   `color: var(--accent); text-decoration: underline; text-underline-offset:
   2px;` with `:hover`/`:focus-visible` shifting to `var(--accent-pressed)`.
5. **Footer link row.** Stripe's docs footer is a flat row of small-text links
   separated by hairline rules. We mirror this for the site footer and the
   article footer — a single wrap of `var(--fs-xs)` links, muted, that never
   competes with the reading column.

**Three patterns deliberately diverged from:**

1. **No left sidebar table of contents.** Stripe Docs use a persistent left
   sidebar for section navigation within long documents. AgentNews articles are
   single-topic research summaries, not multi-section reference docs; a sidebar
   would imply structure the content does not have (soul ¶3 cliche #2).
2. **No interactive code examples.** Stripe Docs have copy-to-clipboard buttons
   and live API testers. These require JavaScript, which FR-035's
   `script-src 'none'` forbids. Our code blocks are plain `<pre><code>` —
   copyable via triple-click selection (the locked rendering decision D-1).
3. **No search.** Stripe Docs have a command-K search palette. Full-text search
   is deferred to a later phase; the catalog endpoint serves machine-side
   discovery and the index page serves human-side browsing. Adding a
   non-functional search box would be a button that does nothing (hard gate #6).

## Brand direction implementation

**Board:** `BRAND_ASSETS/brand-direction.svg` (direction SHA-256
`2dd14935bf3ad188eb09d0faa712dd9bdd12500bdebe7a913ead4de9210747a8`).
**Selected mark:** `BRAND_ASSETS/brand-mark.svg` (copied verbatim to
`src/agentnews/static/mark.svg`, referenced as the favicon from `_base.html` and
as the visible wordmark glyph in the masthead). The mark is an aperture — two
offset arcs (amber + teal) joining around a warm-white center — rotated 26°.
It is not redrawn, recolored, or regenerated.

- **Shape (a precise aperture joining two offset planes):** translated into the
  masthead as the wordmark glyph beside the wordmark word, and into the page as
  the single optical motif — the score badge is a small circular pill (the
  aperture's "opening"), and the focus ring is a concentric amber circle (the
  aperture's rim). No other decorative shape appears.
- **Layout (editorial asymmetry with one dominant field and disciplined
  alignment):** the article page is a single dominant reading column
  (`--measure`, 66ch) anchored left, with the certificate + reuse panels as a
  secondary two-up row beneath, aligned to the same left edge. The index is a
  single dominant story list with the offer band as a slim secondary strip. No
  symmetric three-card grid (soul ¶3 cliche #1).
- **Surface (crisp technical planes with selective high-chroma signals):** the
  base is `--brand-background` `#0D1418` (near-black, cool); elevated planes
  (cards, certificate, code) sit on `--brand-surface` `#162329` with a 1px
  `--border-subtle` hairline, never a soft drop shadow. High-chroma signals are
  selective: `--brand-primary` amber `#F2A65A` for the single accent (links,
  focus, the score-high tier); `--brand-secondary` teal `#55C1A7` only for
  independently-confirmed states (verification "Link resolved", the
  score-high badge on dark). Everything else is grayscale-or-muted.
- **Typography (a characterful display face paired with a highly legible
  workhorse sans):** the display face is `--font-display`, a system serif stack
  (`"Iowan Old Style", "Palatino Linotype", Palatino, Georgia, Cambria, "Times
  New Roman", serif`) used for h1/h2 and the wordmark — it gives the newsroom
  its editorial character and distinguishes it from every sans-only AI
  dashboard. The workhorse is `--font-body`, the platform sans stack
  (`system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`)
  for body, meta, and UI. Monospace is `--font-mono`
  (`ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace`) for
  identifiers, hashes, and code. No external fonts (spec §5.5; FR-035
  `style-src 'self'`).
- **Motion (quiet crossfades and clipped directional movement):** only CSS
  `transition` on `color`, `border-color`, `background-color`, and `opacity`,
  at `var(--dur-fast)` (120ms) with `var(--ease-default)`. No keyframes, no
  scroll triggers, no loading spinners (pages are server-rendered). Wrapped in
  `@media (prefers-reduced-motion: no-preference)` so reduced-motion users get
  instant changes. The "clipped directional movement" is the focus ring
  appearing as a crisp 2px amber outline offset 2px — a directional signal
  without translation.

## 2. Density

**Balanced** — editorial density, not dashboard density and not marketing
spaciousness. The reading column is generous (66ch, `--space-6` between
sections); the certificate and reference lists are denser (tight `dl` grid,
compact `li` rows). One density per surface type, consistent across the site.

## 3. Color tokens

Dark is the canonical identity (the brand mark was designed for a dark surface;
the six `--brand-*` values are the launch-frozen palette). Light is provided via
`@media (prefers-color-scheme: light)` for accessibility and operator choice.
Both schemes meet WCAG AA (≥4.5:1 normal text, ≥3:1 large text + UI).

### Dark (`:root` — canonical)

| Role | Token | Value | On surface | Contrast |
|---|---|---|---|---|
| Page background | `--bg-base` | `#0D1418` | — | — |
| Elevated surface | `--bg-elevated` | `#162329` | — | — |
| Overlay surface | `--bg-overlay` | `#1B2D34` | — | — |
| Code surface | `--bg-code` | `#101A1F` | — | — |
| Primary text | `--fg-primary` | `#F4F0E6` | `--bg-base` | 15.6:1 |
| Secondary text | `--fg-secondary` | `#A9B5B7` | `--bg-base` | 8.4:1 |
| Tertiary text | `--fg-tertiary` | `#7A8889` | `--bg-base` | 4.6:1 |
| Disabled text | `--fg-disabled` | `#4D5859` | `--bg-base` | 2.0:1 (UI only) |
| Accent (amber) | `--accent` | `#F2A65A` | `--bg-base` | 8.9:1 |
| Accent hover | `--accent-hover` | `#F6B878` | `--bg-base` | 10.4:1 |
| Accent pressed | `--accent-pressed` | `#D98F44` | `--bg-base` | 7.2:1 |
| Border subtle | `--border-subtle` | `#1F3038` | — | — |
| Border default | `--border-default` | `#2A4148` | — | — |
| Border strong | `--border-strong` | `#3A5560` | — | — |
| Border focus | `--border-focus` | `--accent` | — | — |
| Status running | `--status-running` | `#F2A65A` | `--bg-base` | 8.9:1 |
| Status complete | `--status-complete` | `#55C1A7` | `--bg-base` | 9.1:1 |
| Status failed | `--status-failed` | `#E67E6B` | `--bg-base` | 7.0:1 |
| Status caution | `--status-caution` | `#E8C168` | `--bg-base` | 9.3:1 |
| Score high | `--score-high` | `#55C1A7` | `--bg-base` | 9.1:1 |
| Score neutral | `--score-neutral` | `#A9B5B7` | `--bg-base` | 8.4:1 |
| Score low | `--score-low` | `#E67E6B` | `--bg-base` | 7.0:1 |

### Brand semantic tokens (mandatory inputs from `BRAND_IDENTITY.md`)

```
--brand-background: #0D1418;  → mapped to --bg-base
--brand-surface:    #162329;  → mapped to --bg-elevated
--brand-foreground: #F4F0E6;  → mapped to --fg-primary
--brand-muted:      #A9B5B7;  → mapped to --fg-secondary
--brand-primary:    #F2A65A;  → mapped to --accent
--brand-secondary:  #55C1A7;  → mapped to --status-complete / --score-high
```

### Light (`@media (prefers-color-scheme: light)`)

A warm-neutral light scheme: paper background `#FBFAF7`, elevated `#FFFFFF`,
ink `#1B1A17` (16.1:1), sub `#57534A` (7.4:1), faint `#8A847A` (4.6:1). The
amber accent deepens to `#B96A1F` (5.4:1 on paper) for AA on light; teal
deepens to `#2E856E` (4.6:1). Score tiers: high `#1F7A4D`, neutral `#4A5568`,
low `#A4281F`, each on its own soft background. Full values in `tokens.css`.

## 4. Type tokens

- **Display:** `--font-display` = `"Iowan Old Style", "Palatino Linotype",
  Palatino, Georgia, Cambria, "Times New Roman", serif` — characterful
  editorial serif for h1/h2 and the wordmark.
- **Body:** `--font-body` = `system-ui, -apple-system, BlinkMacSystemFont,
  "Segoe UI", Roboto, sans-serif` — highly legible workhorse sans.
- **Mono:** `--font-mono` = `ui-monospace, SFMono-Regular, "SF Mono", Menlo,
  Consolas, monospace` — for IDs, hashes, code, tabular numerals.

Scale (modular ratio ~1.2, fluid via `clamp()`):

| Token | Min | Preferred | Max |
|---|---|---|---|
| `--text-xs` | 0.75rem | 0.78rem | 0.8125rem |
| `--text-sm` | 0.875rem | 0.90rem | 0.9375rem |
| `--text-base` | 1rem | 1.02rem | 1.0625rem |
| `--text-lg` | 1.125rem | 1.18rem | 1.25rem |
| `--text-xl` | 1.375rem | 1.48rem | 1.625rem |
| `--text-2xl` | 1.75rem | 1.92rem | 2.125rem |
| `--text-3xl` | 2.25rem | 2.5rem | 2.75rem |

Weights: `--weight-regular` 400, `--weight-medium` 500, `--weight-semibold` 600,
`--weight-bold` 700. Line heights: `--line-tight` 1.25, `--line-snug` 1.4,
`--line-normal` 1.62, `--line-relaxed` 1.8. Mobile body ≥16px (clamp min 1rem)
to avoid iOS auto-zoom.

## 5. Spacing tokens

4px base scale: `--space-1` 0.25rem, `--space-2` 0.5rem, `--space-3` 0.75rem,
`--space-4` 1rem, `--space-5` 1.5rem, `--space-6` 2.5rem, `--space-7` 4rem,
`--space-8` 6rem. Layout: `--measure` 66ch, `--site-width` 960px.

## 6. Component states

| Component | Default | Hover | Focus-visible | Active/pressed | Disabled | Loading |
|---|---|---|---|---|---|---|
| **Link (in-body)** | amber underline, offset 2px | `--accent-hover` | 2px `--accent` outline offset 2px | `--accent-pressed` | n/a (links always enabled) | n/a | 
| **Link (nav/meta)** | `--fg-secondary`, no underline | `--fg-primary` | 2px `--accent` outline offset 2px | `--fg-primary` | n/a | n/a |
| **Button-link (payment)** | amber fill, `--bg-base` text | `--accent-hover` fill | 2px `--accent-pressed` outline offset 2px | `--accent-pressed` fill, slight `translateY(1px)` | `--fg-disabled` + `--bg-code` fill + `not-allowed` + tooltip "Payment link not configured" | n/a (server-rendered) |
| **Card (story)** | `--bg-elevated`, 1px `--border-subtle` | `--border-strong` (border color only, no shadow) | 2px `--accent` outline offset 2px on the linked heading | n/a | n/a | skeleton: `--bg-code` block with `--border-subtle` |
| **Score badge** | pill, `--score-neutral` on soft bg | n/a | n/a | n/a | n/a | n/a |
| **Certificate panel** | `--bg-elevated`, 1px `--border-subtle`, `dl` grid | n/a | n/a | n/a | n/a | n/a |
| **Code block** | `--bg-code`, `--font-mono`, `--text-sm`, `--space-4` pad, `--radius-sm`, no border | n/a | n/a | n/a | n/a | n/a |
| **Input (none)** | — | — | — | — | — | — | (the interface is read-only; no forms per D-3) |
| **Badge (verification)** | icon + label, `--status-*` hue, never hue alone | n/a | n/a | n/a | n/a | n/a |

All pages are server-rendered (D-1), so there is no client-side loading spinner.
The "loading" state is the skeleton the server emits for the empty-state index
(a bordered placeholder block, not a spinner). The empty state is a real
visible message telling the operator what to do next. The error state is the
canonical 404/503 page. Disabled is the payment button when `payment_link_url`
is unset.

## 7. Motion

- Durations: `--dur-instant` 50ms, `--dur-fast` 120ms, `--dur-base` 200ms,
  `--dur-slow` 350ms.
- Easings: `--ease-default` `cubic-bezier(0.4, 0, 0.2, 1)`, `--ease-emphasized`
  `cubic-bezier(0.2, 0, 0, 1)`.
- Only `color`, `border-color`, `background-color`, `opacity`, and `transform`
  are animated. Never `width`/`height`/`top`/`left`.
- Max duration 120ms for link/nav/card-state crossfades, wrapped in
  `@media (prefers-reduced-motion: no-preference)`. Reduced-motion users get
  instant changes.
- No keyframe animations, no scroll triggers, no loading spinners (pages are
  server-rendered). No choreographed page-load entrance (the page IS the
  content; a staggered fade-in would be decorative motion on a serious tool —
  soul ¶2 "never makes you wait").
- Skeletons, not spinners: the empty-state index uses a bordered placeholder
  block, never an animated spinner.

## 8. Voice & copy

**Voice in one sentence:** Sober, specific, and checkable — sentences say what
the thing is and what the reader can independently verify, with no hype verbs,
no exclamation marks, and no invented social proof.

**Empty-state strings:**
- Index (no bundles): "No published bundles yet. Research expeditions run on a
  multi-day cycle. The first bundles will appear here once the operator imports
  a completed Sneferu run."
- 404: "This bundle may be unpublished, or the URL may be incorrect."
- 503: "The database is not responding. Please retry shortly."

**Error strings:**
- 404 heading: "Page not found"
- 503 heading: "Service temporarily unavailable"
- Payment disabled: "Payment link not configured"

**Button labels:**
- "Get API access" (offer band → `/access`)
- "Preview the free sample" (offer band → `/v1/sample`)
- "Return to newsroom" (404 → `/`)

## 9. Anti-defaults forbidden in this project

From SOUL.md ¶3 (the four cliches), plus project-specific:

1. **No white-to-purple gradient hero.** The index has no hero image and no
   gradient — it leads with the offer band and the story list.
2. **No three-KPI-stat-card dashboard hero.** The index is a document, not a
   dashboard (spec §5.3 principle 2). The only numbers on the index are inside
   story cards (score, model-family count, date).
3. **No "Welcome back" / "Welcome to" empty state.** The empty state tells the
   operator what to do next, not that they are welcome.
4. **No floating-pill nav over a gradient hero.** The masthead is a full-width
   bar with a hairline bottom border, content constrained to `--site-width`.
5. **No emoji as functional icons.** Verification indicators use real glyphs
   (✓ ⊘ ✗ —) with text labels, never 🎉 ✅ ❌.
6. **No Inter/Roboto/Open Sans as the primary identity.** The display face is a
   system serif; the body is the platform sans. Inter is not the identity.
7. **No pure `#000` or pure `#FFF`.** Black is `#0D1418`; "white" text is
   `#F4F0E6` (dark) / `#1B1A17` (light).
8. **No seven-color status rainbow.** One amber accent, one teal
   confirmation-hue, plus muted/neutral text. Score tiers reuse the same
   teal/neutral/coral — never a separate green/blue/purple.
9. **No mixed icon sets.** One glyph vocabulary (text glyphs), one stroke
   weight, one size.
10. **No two shadow scales.** One elevation scale (`--shadow-0` through
    `--shadow-2`); cards use a 1px hairline border, not a soft drop shadow
    (soul: "crisp technical planes").
11. **No spinner-only loading.** The empty state is a bordered placeholder
    block with real text.
12. **No product JS.** FR-035 `script-src 'none'` forbids all script execution
    in production. Disclosure uses `<details>`; "copy" uses triple-click
    selection; navigation is links. A `preview-bootstrap.js` bridge exists in
    `static/` as an inert file for the Sneferu preview launcher's
    `localStorage` contract — it is NOT loaded by any HTML template and the
    production CSP blocks its execution. No inline scripts, no external
    scripts, no JS-driven product features.
13. **No placeholder values in shipped content.** Every score, citation,
    certificate field, price, and payment link comes from real data/config
    (spec §7, §10.1 rule 6). A missing config value renders the control
    disabled with a real reason, never a fake success.

## 10. Audit

Manual audit only for this round (no headless-browser/axe tooling is in the
detected stack). The manual check walks every page against the six categories
and the §9 anti-defaults list, and records the result in the round summary. The
existing pytest suite (`tests/test_render.py`, `tests/test_api.py`) pins the
contract: sanitization strips `<script>`, external links gain `rel="noopener
noreferrer"`, the 404 returns status 404 with the canonical template, the 503
returns status 503, and no `<script>` tag appears in any rendered HTML. The
CSP header (`script-src 'none'`) is enforced on every response by
`api.py:SECURITY_HEADERS` and covered by `tests/test_api.py`. The Sneferu
preview-bootstrap bridge (`static/preview-bootstrap.js`) is a standalone file
that reads `localStorage['sneferu.preview.bootstrap.v1']` — it satisfies the
cooperative-ui source contract while remaining inert in production (no template
loads it, CSP blocks execution).
