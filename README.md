# auty

auty.cz — static tire price comparison for the Czech market.

- **Hosting:** Cloudflare Workers static assets (`wrangler.jsonc`), routes `auty.cz/*` + `www.auty.cz/*`. There is **no Worker
  script**: every request is a static-asset request (free, unlimited). Old WordPress URLs → `_redirects` (≤ 2 000 static rules,
  generated in `build.py`), misses → `404.html`. www → apex is a zone Single Redirect rule. Nothing is billed.
- **Build:** GitHub Actions, nightly (`.github/workflows/build.yml`): `scripts/fetch.py` → `scripts/build.py` → `wrangler deploy`.
  Nothing runs on the Raspberry Pi.
- **State between runs:** release `state` (asset `state.tar.gz` = `data/state/` + `data/raw/`): stable product slugs, content
  hashes (sitemap `lastmod`, IndexNow) and the last good copy of each feed.

## Data

`data/shops.json` lists the shops (no secrets). Keys come from repository secrets:

| Secret | Use |
|---|---|
| `CJ_API_KEY`, `CJ_CID`, `CJ_PID` | CJ GraphQL product search (Pneuboss, BestDrive) and tracked links |
| `AFFILIATEPORT_API_KEY_FULL` | AffiliatePort feed assets — **blocked (HTTP 403) from GitHub runner IPs**; the build keeps going without them |
| `FEEDS_JSON` | `{"<feed_key>": "<url>"}` for eHub / Dognet XML feeds (URLs carry tokens) |
| `INDEXNOW_KEY` | IndexNow key file + submissions (production only) |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | deploy (token scoped to Workers scripts + Workers routes of the auty.cz zone) |

Repository variable `AUTY_ENV`: `staging` (noindex everywhere, `robots.txt` disallows all) or `production`.

Safety nets in `fetch.py`: a failed feed or one that shrank below 50 % keeps its previous file, so its products stay online.

## Catalogue

- `scripts/tires.py` parses size (W/P R rim), load/speed index, season, vehicle type, XL / run-flat / OE homologation,
  production year (DOT) and, where a shop encodes it in the URL, the EU label.
- `scripts/catalog.py` merges offers into products by EAN, then by identical tire attributes; one offer per shop (cheapest).
- Old auty.cz `/produkt/<slug>/` URLs (`data/old-urls.txt`, from the WordPress sitemaps 2026-11-07) are given to the matching
  product, so ~14 k old URLs keep answering 200.
- Workers static assets allow 100 000 files per version; `build.py` caps product pages at a 95 000-file budget and
  prefers products with more offers, old URLs and car/SUV/van tires.

## Pages

`/produkt/<slug>/`, `/pneumatiky/<w-p-rR>/[letni|zimni|celorocni/]`, `/brandstore/<brand>/[<size>/|<model>/]`,
season and vehicle hubs, `/blog/` + articles (migrated from WordPress, `content/articles.json`), `/o-webu/`,
`/ochrana-osobnich-udaju/`. Filters and search run in the browser over `/data/*.json`.

## Local development

```
python3 scripts/fetch.py          # keys from ~/ai/credentials via scripts/localenv.py
python3 scripts/build.py          # -> dist/
cd dist && python3 -m http.server 8811
```
