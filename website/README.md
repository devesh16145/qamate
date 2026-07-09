# QAmate marketing site

Static landing page for [QAmate](https://github.com/devesh16145/qamate).

## Local preview

```bash
cd website
python -m http.server 8080
# or: npx serve .
```

Visit `http://localhost:8080`.

## Deploy to Vercel (recommended)

Assets are self-contained under `website/assets/branding/` (no parent-folder paths).

### One-time (you create the project in the Vercel UI)

1. Push this repo to GitHub (`devesh16145/qamate`).
2. [vercel.com/new](https://vercel.com/new) → Import the **qamate** repository.
3. **Project name:** `qamate`
4. **Root Directory:** `website` ← important
5. **Framework Preset:** Other
6. **Build Command:** leave empty
7. **Output Directory:** `.`
8. Deploy.

Live URL: `https://qamate.vercel.app` (or your custom domain).

### CLI (optional, after `npx vercel login`)

From the repo root:

```bash
cd website
npx vercel --prod
```

Link to an existing Vercel project:

```bash
cd website
npx vercel link
npx vercel --prod
```

### Custom domain

Vercel → Project → **Settings → Domains** → add e.g. `qamate.dev`, then update DNS.

After adding a custom domain, update `canonical` and `og:url` in `index.html`.

## GitHub Pages (alternative)

**Settings → Pages** → branch `main`, folder `/website` → `https://devesh16145.github.io/qamate/`

## Files

| File | Purpose |
|------|---------|
| `index.html` | Landing page |
| `styles.css` | Brand styles |
| `assets/branding/*.svg` | Logos (copies of repo `assets/branding/`) |
| `favicon.svg` | Tab icon |
| `og-image.svg` | Social preview (1200×630) |
| `vercel.json` | Vercel headers + clean URLs |

Canonical primary: `#1D4ED8`. Regenerate `website/assets/branding/` after editing root `assets/branding/`:

```powershell
Copy-Item ..\assets\branding\*.svg .\assets\branding\ -Force
```
