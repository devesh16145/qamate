# QAmate — Branding

Official brand direction: browser-window icon with `</>` code mark + pass checkmark, **QA** in blue + **mate** in ink/white, tagline *AI E2E Testing*.

Reference sheet: `assets/branding/qamate-brand-sheet.png`

---

## Logo files

| File | Use |
|------|-----|
| `assets/branding/qamate-mark.svg` | Primary icon mark (app chrome, favicon source) — browser window + `</>` + pass badge; exposes `#qamate-mark` symbol |
| `assets/branding/qamate-logo.svg` | Horizontal lockup (light bg) — mark + **QAmate** wordmark + *AI E2E Testing* tagline |
| `assets/branding/qamate-logo-dark.svg` | Horizontal lockup for dark backgrounds — `mate` in `#F8FAFC`, `QA` in `#3B82F6` |
| `assets/branding/favicon.svg` | Simplified mark optimized for 16–32px (thicker strokes, fewer details, dark keyline square) |
| `assets/branding/qamate-mark-32.png` | OS window/taskbar icon (export for Windows; optional) |
| `assets/branding/qamate-brand-sheet.png` | Master reference (lockups, palette, pillars) |
| `assets/branding/qamate-logo-icon.png` | Legacy icon (Q-window); superseded by `qamate-mark.svg` |
| `assets/branding/qamate-logo-full.png` | Legacy horizontal lockup; superseded by `qamate-logo.svg` |

**Export checklist** (from brand sheet): 512×512 app icon, 1200×630 OG image, favicon 32×32, light + dark horizontal lockups.

---

## Color system (polished)

Refined from the brand sheet for better UI contrast and accessibility (WCAG AA on body text).

### Brand core

| Token | Hex | Use |
|-------|-----|-----|
| `brand-primary` | `#1D4ED8` | **Canonical primary** — QA wordmark, icon chrome (title bar + badge), primary buttons, links |
| `brand-primary-deep` | `#1E40AF` | Hover / pressed states, emphasis |
| `brand-primary-bright` | `#3B82F6` | On-dark accents & links (hero, dark lockup QA) |
| `brand-primary-soft` | `#EFF6FF` | Tinted panels, chips, selection wash |
| `brand-primary-border` | `#BFDBFE` | Focus rings, soft borders |

> **Canonical primary is `#1D4ED8`** (the brand-sheet blue). The older `#2563EB` is deprecated as the primary; on dark surfaces use `#3B82F6` for wordmark/link contrast.

### Neutrals

| Token | Light | Dark | Use |
|-------|-------|------|-----|
| `ink` | `#0F172A` | `#F8FAFC` | Headlines, **mate** on light bg |
| `ink-deep` | `#0B1220` | `#0B1220` | Marketing hero, dark lockups |
| `muted` | `#64748B` | `#94A3B8` | Tagline, secondary copy |
| `surface` | `#F8FAFC` | `#0F172A` | Page background |
| `surface-raised` | `#FFFFFF` | `#1E293B` | Cards, editor panes |
| `border` | `#E2E8F0` | `#334155` | Dividers, inputs |
| `border-strong` | `#CBD5E1` | `#475569` | Emphasis borders |

### Semantic

| Token | Hex | Use |
|-------|-----|-----|
| `pass` | `#16A34A` | Checkmark badge, test passed |
| `pass-soft` | `#DCFCE7` | Pass backgrounds |
| `fail` | `#DC2626` | Test failed |
| `warn` | `#D97706` | Flaky / guided mode |

### Wordmark colors

- **Light background:** `QA` → `#1D4ED8`, `mate` → `#0F172A`
- **Dark background:** `QA` → `#3B82F6`, `mate` → `#F8FAFC`
- **Tagline:** `#475569` (light) / `#94A3B8` (dark) with hairline rules `#CBD5E1` (light) / `#334155` (dark)

---

## Icon mark

- Rounded browser window (rx=8 on the 64 grid), blue title bar `#1D4ED8` (11px tall), three white window dots
- White content area with bold `</>` in ink `#0F172A` (vector paths, never `<text>`)
- Blue circular pass badge `#1D4ED8` (r=10) overlapping bottom-right, white checkmark + hairline keyline
- Single source of truth: `qamate-mark.svg` exposes `<symbol id="qamate-mark">` — reuse via `<use href="…/qamate-mark.svg#qamate-mark">` or `<img>`
- App icon: mark only on `#FFFFFF` or `#0B1220` rounded square

---

## Brand pillars

| Pillar | Icon | Message |
|--------|------|---------|
| Trust & Reliability | Shield | Self-healing locators, verify-before-ship agent |
| Developer First | `</>` | Playwright-native, BYOK, open source MIT |
| AI-Forward | Sparkle | Agent authors and fixes tests in a live browser |
| Clarity & Focus | Target | Checkpoints, JUnit results, clear pass/fail |

---

## Typography

| Role | Font | Weight |
|------|------|--------|
| Wordmark & UI | **Inter** | 600–700 for headings, 400–500 body |
| Code / logs | **JetBrains Mono** | 400 |

- Wordmark: tight tracking, no all-caps except **QA**
- Tagline: 11–12px, uppercase optional, letter-spacing `0.06em`

---

## Voice

- **Name:** QAmate — QA + mate (testing companion)
- **Tagline:** AI E2E Testing
- **One-liner:** Open-source desktop IDE for self-healing Playwright tests and an AI agent that authors and verifies suites.
- **Tone:** Premium, trustworthy, developer-first — not playful mascot energy

---

## App integration (Electron IDE)

| Surface | Asset / path |
|---------|----------------|
| Titlebar mark (icon only) | `assets/branding/qamate-mark.svg` → `src/index.html` `.tb-logo` (20×20, left of menu) |
| Tab / window favicon | `assets/branding/favicon.svg` → `<link rel="icon">` in `src/index.html`, `src/agent.html` |
| OS window / taskbar icon | `main.js` `resolveAppIcon()` — prefers `qamate-mark-32.png`, falls back to SVG (often no-op on Windows) |

No wordmark text in the titlebar — mark only. Export `qamate-mark-32.png` or `.ico` from the brand sheet for reliable Windows taskbar icons.

---

## App CSS mapping

The IDE (`src/styles.css`) maps brand tokens to:

| Brand | CSS variable (light) |
|-------|----------------------|
| `brand-primary` | `--accent` |
| `brand-primary-deep` | `--accent-hover` |
| `brand-primary-soft` | `--accent-bg` |
| `ink` | `--text` |
| `surface` | `--tool` |
| `border` | `--border` |

---

## Image generation prompt (iterations)

```
Brand: QAmate — AI E2E Testing. Open-source Playwright IDE with self-healing locators.

STYLE: Premium developer-tool identity. Flat vector. Rounded browser window icon with blue header (#1D4ED8), white body, black </> symbol, blue circle checkmark badge bottom-right. Clean sans-serif wordmark: "QA" in #1D4ED8, "mate" in #0F172A. Tagline "AI E2E Testing" in #475569 with thin horizontal rules.

COLORS: Primary #1D4ED8, deep #1E40AF, bright #3B82F6, ink #0F172A, surface #F8FAFC, border #E2E8F0, pass green #16A34A.

NO: mascots, gradients, glossy 3D, stock photos, circuit-board clichés.

Deliver: 512×512 app icon, horizontal lockup light + dark (#0B1220), 1200×630 social banner.
```
