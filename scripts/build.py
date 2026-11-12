#!/usr/bin/env python3
"""Build the static auty.cz site into dist/ from data/raw/*.jsonl.gz.

    AUTY_ENV=production python3 scripts/build.py     # indexable build (default: staging = noindex everywhere)

State (stable slugs, content hashes for lastmod/IndexNow) lives in data/state/state.json.gz; CI keeps it as a release asset.
"""
import hashlib
import json
import os
import re
import shutil
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalog as C
import wheels as W  # noqa: E402
import render as R  # noqa: E402
import tires as T  # noqa: E402

APP = Path(__file__).resolve().parent.parent
DIST = APP / "dist"
BASE = "https://auty.cz"
ENV = os.environ.get("AUTY_ENV", "staging")
GA4 = os.environ.get("GA4_ID", "G-Q7BG14P190")
INDEXNOW_KEY = os.environ.get("INDEXNOW_KEY", "")
FILE_LIMIT = 100_000
HUB_JSON_ITEMS = 2000        # hub pages load only the most compared tyres; size/brand picks load their own small JSON
# Content-Security-Policy: scripts only from us + GA4 (loaded after consent); 'unsafe-inline' is needed for Cloudflare's injected bot-detection snippet
# and the speculationrules block; no user input exists on the site. Tested in headless Chrome incl. GA4 after consent (0 violations).
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' 'inline-speculation-rules' https://www.googletagmanager.com https://challenges.cloudflare.com; "
       "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self'; "
       "connect-src 'self' https://*.google-analytics.com https://*.analytics.google.com https://*.googletagmanager.com https://*.g.doubleclick.net https://challenges.cloudflare.com; "
       "frame-src https://challenges.cloudflare.com; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'; upgrade-insecure-requests")
MAX_PAGES = 30                # most pages per listing (24 tyres each); lowered automatically if the file budget needs it
FILE_BUDGET = 95_000          # keep 5 % headroom under the Workers static-assets limit
E = R.E
TODAY = date.today()
TODAY_CZ = f"{TODAY.day}. {TODAY.month}. {TODAY.year}"


def log(m):
    print(m, file=sys.stderr, flush=True)


def paged(out, S, path, n_items, render, group, index=True):
    """Write a listing and its /strana-N/ pages. Page 1 = the canonical listing; 2+ are self-canonical, not in the sitemap."""
    pages = max(1, min(-(-n_items // R.PER_PAGE), MAX_PAGES))
    for k in range(1, pages + 1):
        S.pg = (path, k, pages)
        out.page(path if k == 1 else f"{path}strana-{k}/", render(), group if k == 1 else None, index=index if k == 1 else False)
    S.pg = None


class Out:
    def __init__(self, state):
        self.state = state
        self.hashes = state.setdefault("hash", {})
        self.lastmod = state.setdefault("lastmod", {})
        self.changed, self.files, self.sitemap = [], 0, defaultdict(list)

    def write(self, rel, content, sitemap=None):
        p = DIST / rel.lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode() if isinstance(content, str) else content
        p.write_bytes(data)
        self.files += 1
        return data

    def page(self, path, html, group=None, index=True):
        data = self.write(path + "index.html", html)
        h = hashlib.md5(data).hexdigest()[:12]
        if self.hashes.get(path) != h:
            self.hashes[path] = h
            self.lastmod[path] = TODAY.isoformat()
            if index:
                self.changed.append(path)
        if group and index:
            self.sitemap[group].append(path)


def brand_index(items):
    """Stable brand numbering inside one JSON file (the page and its JSON must agree)."""
    return {b: i for i, b in enumerate(sorted({p["bslug"] for p in items}))}


def dim_lbl(d):
    w, p, r = re.match(r"(\d+)-(\d+)-r([\d-]+)", d).groups()
    return T.dim_label(w, p, r.replace("-", "."))


def median(xs):
    return statistics.median(xs) if xs else 0


def outer_diameter(w, p, r):
    return float(r) * 25.4 + 2 * int(w) * int(p) / 100


def main():
    t0 = time.time()
    shops = [s for s in json.load(open(APP / "data/shops.json"))["shops"] if s.get("enabled", True)]   # disabled shops: ignore stale raw files
    state = C.load_state()
    offers = C.load_offers([s for s in shops if s.get("kind") != "wheels"])
    wheels = W.load(shops)
    wpages = W.plan(wheels)
    wmodels = W.car_models(json.load(open(APP / "data/cars.json"))["cars"], json.load(open(APP / "data/car_wheels.json"))["wheels"])
    wpages.update(W.car_plan(wheels, wmodels))
    if not offers:
        sys.exit("no offers — refusing to build an empty site")
    products = C.cluster(offers)
    # price errors in shop feeds (e.g. a 215/50 R18 for 378 Kč): an offer is dropped when it is under half of the same tyre's
    # other offers, or under half of the cheap end (10th percentile) of its size and season — budget brands stay in
    p10 = defaultdict(list)
    for p in products:
        p10[(p["w"], p["p"], p["r"], p["season"])].append(p["offers"][-1]["price"])
    p10 = {k: sorted(v)[len(v) // 10] for k, v in p10.items() if len(v) >= 10}
    dropped = 0
    for p in products:
        floor = 0.5 * p10.get((p["w"], p["p"], p["r"], p["season"]), 0)
        keep = []
        for o in p["offers"]:
            others = [x["price"] for x in p["offers"] if x is not o]
            if o["price"] < floor or (len(others) >= 2 and o["price"] < 0.45 * statistics.median(others)):
                dropped += 1
            else:
                keep.append(o)
        p["offers"] = keep
        if keep:
            p["price"] = keep[0]["price"]
    products = [p for p in products if p["offers"]]
    log(f"price sanity: dropped {dropped} offers (price errors)")
    old_slugs = [u.rstrip("/").rsplit("/", 1)[1] for u in (APP / "data/old-urls.txt").read_text().split() if "/produkt/" in u]
    C.assign_slugs(products, state, old_slugs, log)
    present = {o["shop"] for o in offers}
    shop_meta = {s["id"]: {"name": s["name"], "color": R.SHOP_COLORS[i % len(R.SHOP_COLORS)], "domain": s["domain"],
                           "logo": s.get("logo", ""), "logo_bg": s.get("logo_bg", "")} for i, s in enumerate(shops)}
    log(f"{len(offers)} offers -> {len(products)} products from {len(present)} shops")

    by_dim, by_brand, by_model = defaultdict(list), defaultdict(list), defaultdict(list)
    for p in products:
        p["dim"] = T.dim_slug(p["w"], p["p"], p["r"])
        p["bslug"] = T.slugify(p["brand"])
        by_dim[p["dim"]].append(p)
        by_brand[p["bslug"]].append(p)
        by_model[p["mkey"]].append(p)

    # model slugs (stable, unique per brand, never shaped like a size)
    mstate = state.setdefault("models", {})
    used = defaultdict(set)
    for k, s in mstate.items():
        used[k.split(":")[0]].add(s)
    for mk, ps in by_model.items():
        if mk in mstate:
            continue
        b = mk.split(":")[0]
        base = T.slugify(ps[0]["model"]) or "vzorek"
        if re.fullmatch(r"\d{3}-\d{2}-r\d{2}(-5)?", base):
            base = "vzorek-" + base
        s, i = base, 2
        while s in used[b]:
            s, i = f"{base}-{i}", i + 1
        mstate[mk] = s
        used[b].add(s)

    # ---------------- page plan and file budget
    dim_pages = {d: ps for d, ps in by_dim.items() if len(ps) >= 2}
    dim_season = {(d, s): [p for p in ps if p["season"] == s] for d, ps in dim_pages.items() for s in ("summer", "winter", "all")}
    dim_season = {k: v for k, v in dim_season.items() if len(v) >= 4}
    brand_dim = defaultdict(list)
    for p in products:
        brand_dim[(p["bslug"], p["dim"])].append(p)
    brand_dim = {k: v for k, v in brand_dim.items() if len(v) >= 4}
    # categories from the content gap (8. 11. 2026): cheap tyres, brand × season, rim × season, run-flat / EV / off-road / clearance
    cheap_all = [p for p in products if p["vehicle"] in PASSENGER and 13 <= float(p["r"]) <= 22 and fresh_price(p) and fresh_price(p) == p["price"]]
    cheap = {s: [p for p in cheap_all if not s or p["season"] == s] for s in (None, "summer", "winter", "all")}
    taken = {(k.split(":")[0], v) for k, v in mstate.items()}          # a model slug "zimni" would clash with /brandstore/b/zimni/
    brand_season = defaultdict(list)
    for p in products:
        brand_season[(p["bslug"], p["season"])].append(p)
    brand_season = {k: v for k, v in brand_season.items() if len(v) >= 40 and (k[0], T.SEASON_SLUG[k[1]]) not in taken}
    rim_season = defaultdict(list)
    for p in products:
        if p["vehicle"] in PASSENGER and re.fullmatch(r"1[3-9]|2[0-2]", p["r"]):
            rim_season[(p["season"], p["r"])].append(p)
    rim_season = {k: v for k, v in rim_season.items() if len(v) >= 30}
    specials = {k: v for k, v in ((s, special_items(products, s)) for s in SPECIAL) if len(v) >= 30}
    cat_sizes = [len(v) for v in cheap.values()] + [len(v) for v in brand_season.values()] + [len(v) for v in rim_season.values()] + [len(v) for v in specials.values()]
    fixed = len(cat_sizes) * 2 + len(wpages) * 2 + (len(dim_pages) * 2 + len(dim_season) + len(by_brand) * 2 + len(brand_dim) + len(by_model) + 200 + len(list((APP / "static").rglob("*"))))
    # pagination (1 2 3 …): every listing gets static /strana-N/ pages, capped so product pages keep their budget
    hub_sizes = [sum(1 for p in products if p["season"] == x) for x in ("summer", "winter", "all")] + \
                [sum(1 for p in products if p["vehicle"] == x) for x in ("car", "suv", "van", "moto", "truck")]
    lists = [len(v) for v in dim_pages.values()] + [len(v) for v in dim_season.values()] + [len(v) for v in by_brand.values()] + \
            [len(v) for v in brand_dim.values()] + hub_sizes + cat_sizes + [len(v[2]) for v in wpages.values()]
    global MAX_PAGES
    extra = lambda m: sum(min(-(-n // R.PER_PAGE), m) - 1 for n in lists if n)
    while MAX_PAGES > 3 and FILE_BUDGET - fixed - extra(MAX_PAGES) < len(products):
        MAX_PAGES -= 1
    fixed += extra(MAX_PAGES) + 300                  # + car pages
    log(f"pagination: up to {MAX_PAGES} pages per listing, {extra(MAX_PAGES)} extra pages")
    cap = FILE_BUDGET - fixed
    ranked = sorted(products, key=lambda p: (-len(p["offers"]), p["slug"] not in old_slugs, p["vehicle"] not in ("car", "suv", "van"), p["price"]))
    for i, p in enumerate(ranked):
        p["page"] = i < cap
    log(f"pages: {min(cap, len(products))} products (cap {cap}), {len(dim_pages)} sizes, {len(dim_season)} size×season, "
        f"{len(by_brand)} brands, {len(brand_dim)} brand×size, {len(by_model)} models")

    # ---------------- output
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    out = Out(state)
    for f in (APP / "static").rglob("*"):
        if f.is_file():
            rel = f.relative_to(APP / "static").as_posix()
            if rel in ("assets/site.css", "assets/app.js"):
                continue
            out.write(rel, f.read_bytes())
    css = (APP / "static/assets/site.css").read_bytes()
    js = (APP / "static/assets/app.js").read_bytes()
    css_name = f"site.{hashlib.md5(css).hexdigest()[:8]}.css"
    js_name = f"app.{hashlib.md5(js).hexdigest()[:8]}.js"
    out.write("assets/" + css_name, css)
    out.write("assets/" + js_name, js)

    stats = {"products": len(products), "shops": len(present), "offers": len(offers), "brands": len(by_brand), "shop_ids": sorted(present)}
    S = R.Site(BASE, ENV, GA4, css_name, js_name, shop_meta, TODAY_CZ, stats)
    brand_name = {b: ps[0]["brand"] for b, ps in by_brand.items()}

    def model_url(p):
        return f"/brandstore/{p['bslug']}/{mstate[p['mkey']]}/"

    S.brand_season = set(brand_season)
    S.model_links = {mk: (model_url(ps[0]), low(ps), len(ps)) for mk, ps in by_model.items()}
    S.wpages = set(wpages)
    S.dim_season = set(dim_season)
    S.brand_dim = set(brand_dim)
    S.dim_set = set(dim_pages)                        # size pages that exist (>= 2 products): never link to the others
    S.specials = set(specials)
    S.wheel_n = {"alu": sum(1 for x in wheels if not x["steel"]), "steel": sum(1 for x in wheels if x["steel"])}
    S.car_wheels = json.load(open(APP / "data/car_wheels.json"))["wheels"]
    cars = load_cars(by_dim)
    S.cars_by_dim = defaultdict(list)
    for c in cars:
        for z in c["sizes"]:
            if c not in S.cars_by_dim[z["dim"]]:
                S.cars_by_dim[z["dim"]].append(c)

    # ---------------- product pages
    for p in products:
        if p["page"]:
            out.page(f"/produkt/{p['slug']}/", product_page(S, p, by_model, by_dim, dim_pages, brand_dim, model_url), "products")

    # ---------------- size pages + JSON
    dims_json = {}
    for d, ps in dim_pages.items():
        w, pr, r = ps[0]["w"], ps[0]["p"], ps[0]["r"]
        bidx = brand_index(ps)
        out.write(f"data/d/{d}.json", json.dumps({"brands": {i: brand_name[b] for b, i in bidx.items()},
                                                   "items": [S.item_json(p, bidx[p["bslug"]]) for p in ps]}, ensure_ascii=False, separators=(",", ":")))
        seas = [T.SEASON_SLUG[s] for s in ("summer", "winter", "all") if (d, s) in dim_season]
        dims_json[d] = [w, pr, r, len(ps), seas]
        paged(out, S, f"/pneumatiky/{d}/", len(ps), lambda: dim_page(S, d, ps, None, dim_pages, dim_season, brand_dim, brand_name), "listings", index=len(ps) >= 5)
    for (d, s), ps in dim_season.items():
        paged(out, S, f"/pneumatiky/{d}/{T.SEASON_SLUG[s]}/", len(ps), lambda: dim_page(S, d, dim_pages[d], s, dim_pages, dim_season, brand_dim, brand_name), "listings", index=len(ps) >= 8)
    out.write("data/dims.json", json.dumps(dims_json, separators=(",", ":")))

    # ---------------- brands, brand×size, models
    for b, ps in by_brand.items():
        bidx = {b: 0}
        out.write(f"data/b/{b}.json", json.dumps({"brands": {0: brand_name[b]}, "items": [S.item_json(p, 0) for p in ps]}, ensure_ascii=False, separators=(",", ":")))
        paged(out, S, f"/brandstore/{b}/", len(ps), lambda: brand_page(S, b, ps, by_model, brand_dim, mstate, model_url), "listings", index=len(ps) >= 3)
    for (b, d), ps in brand_dim.items():
        paged(out, S, f"/brandstore/{b}/{d}/", len(ps), lambda: brand_dim_page(S, b, d, ps, brand_name, model_url), "listings", index=len(ps) >= 6)
    for mk, ps in by_model.items():
        out.page(model_url(ps[0]), model_page(S, ps, model_url, dim_pages), "listings")
    out.page("/brandstore/", brands_index(S, by_brand, brand_name), "pages")
    out.page("/pneumatiky/", dims_index(S, dim_pages), "pages")

    # ---------------- hubs
    hubs = [(slug, [p for p in products if p["season"] == s], s, None) for s, slug in
            (("summer", "letni-pneumatiky"), ("winter", "zimni-pneumatiky"), ("all", "celorocni-pneumatiky"))]
    hubs += [(slug, [p for p in products if p["vehicle"] == v], None, v) for v, slug in
             (("car", "osobni-pneumatiky"), ("suv", "pneumatiky-suv"), ("van", "pneumatiky-na-dodavky"), ("moto", "pneumatiky-na-motorku"), ("truck", "nakladni-pneumatiky"))]
    for slug, items, s, v in hubs:
        if items:
            write_hub_json(out, S, slug, items, brand_name)
            paged(out, S, f"/{slug}/", len(items), lambda: hub_page(S, slug, items, dim_pages, dim_season, s, v, brand_name, brand_dim, brand_season, rim_season), "pages")
    for s, items in cheap.items():
        ss = T.SEASON_SLUG.get(s, "")
        write_hub_json(out, S, "levne-pneumatiky" + (f"-{ss}" if s else ""), items, brand_name, order="price")
        paged(out, S, "/levne-pneumatiky/" + (f"{ss}/" if s else ""), len(items), lambda: cheap_page(S, s, items, products, dim_pages, brand_name), "pages")
    for (b, s), items in brand_season.items():
        ss = T.SEASON_SLUG[s]
        write_hub_json(out, S, f"bs-{b}-{ss}", items, brand_name)
        paged(out, S, f"/brandstore/{b}/{ss}/", len(items), lambda: brand_season_page(S, b, s, items, brand_name, model_url, dim_pages), "listings")
    for (s, r), items in rim_season.items():
        ss = T.SEASON_SLUG[s]
        write_hub_json(out, S, f"r-{ss}-{r}", items, brand_name)
        paged(out, S, f"/{ss}-pneumatiky/r{r}/", len(items), lambda: rim_season_page(S, s, r, items, brand_name, dim_season), "listings")
    for slug, items in specials.items():
        write_hub_json(out, S, slug.replace("/", "-"), items, brand_name, order="price" if slug.startswith("vyprodej-pneumatik") else "pop")
        paged(out, S, f"/{slug}/", len(items), lambda: special_page(S, slug, items, brand_name, dim_pages), "pages")

    # ---------------- alloy and steel wheels (no per-wheel pages: cards link straight to the shop)
    wshops = len({o["shop"] for x in wheels for o in x["offers"]})
    for path, (kind, key, items) in wpages.items():
        paged(out, S, path, len(items), lambda: W.page(S, path, kind, key, items, wpages, wshops), "pages" if kind in ("hub", "steel", "rim", "pcd") else "listings")
        out.write(f"data/w/{W.json_key(path)}.json", W.json_data(S, items))
    out.page("/roztec-kol/", W.pcd_page(S, wmodels, wpages), "pages")
    log(f"wheels: {len(wheels)} from {wshops} shops, {len(wpages)} listings")

    # ---------------- tyres by car
    by_make = defaultdict(list)
    for c in cars:
        by_make[c["make"]].append(c)
    for c in cars:
        out.page(c["url"], car_page(S, c, by_make, by_dim, dim_pages), "pages")
    for mk, cs in by_make.items():
        out.page(f"/pneumatiky-podle-auta/{T.slugify(mk)}/", make_page(S, mk, cs), "pages")
    out.page("/pneumatiky-podle-auta/", cars_index(S, by_make), "pages")
    out.page("/najdi-pneumatiky/", wizard_page(S, by_make, dim_pages), "pages")

    # ---------------- articles, static pages, home
    articles = json.load(open(APP / "content/articles.json"))
    for a in articles:
        out.page(f"/{a['slug']}/", article_page(S, a, articles), "pages")
    out.page("/blog/", blog_page(S, articles), "pages")
    out.page("/testy-pneumatik/", tests_page(S, articles), "pages")
    out.page("/o-webu/", about_page(S, shops, present, offers), "pages")
    out.page("/kontakt/", contact_page(S), "pages")
    out.page("/ochrana-osobnich-udaju/", privacy_page(S), "pages")
    out.page("/", home_page(S, products, dim_pages, by_brand, brand_name, articles), "pages")
    out.write("404.html", S.layout("/404/", "Stránka nenalezena | auty.cz", "Tahle stránka neexistuje.",
                                   '<div class="e404"><div><h1>Píchli jsme. Tahle stránka neexistuje</h1><p class="muted">Možná jste přišli po staré adrese nebo pneumatika zmizela z nabídky. '
                                   'Zkuste vyhledat rozměr nebo značku, nebo začněte <a class="lnk" href="/">na úvodní stránce</a>.</p>' + S.size_picker(title="Zadejte rozměr") +
                                   '</div><img src="/img/ui/404.webp" alt="Auto s píchlou pneumatikou" width="640" height="414"></div>', noindex=True))

    # ---------------- search index
    search = [[f"Pneumatiky {brand_name[b]}", f"/brandstore/{b}/", "značka"] for b in sorted(by_brand, key=lambda b: -len(by_brand[b]))]
    search += [[f"{ps[0]['brand']} {ps[0]['model']}", model_url(ps[0]), f"vzorek · {len(ps)} rozměrů"] for ps in sorted(by_model.values(), key=lambda x: -len(x))]
    search = [[f"{c['name']} {c['years']}", c["url"], "auto · tovární rozměry"] for c in cars] + search
    cats = [["Levné pneumatiky", "/levne-pneumatiky/", "kategorie"], ["Výprodej pneumatik", "/vyprodej-pneumatik/", "kategorie"]]
    cats += [[SPECIAL[k][0], f"/{k}/", "kategorie"] for k in specials]
    cats += [[f"{T.SEASON_CZ[s].capitalize()} pneumatiky R{r}", f"/{T.SEASON_SLUG[s]}-pneumatiky/r{r}/", "kategorie"] for (s, r) in sorted(rim_season)]
    cats += [[{"hub": "Alu kola", "steel": "Plechové disky"}.get(k) or ("Alu kola " + path.strip("/").split("/", 1)[1].replace("-", ",").replace("/", " ").upper().replace("X", "×")
             if k != "brand" else f"Alu kola {v[0]['brand']}"), path, "disky"] for path, (k, _, v) in wpages.items() if k in ("hub", "steel", "rim", "pcd", "brand")]
    search = cats + search
    out.write("data/tiers.json", json.dumps({brand_name[b]: R.tier(b) for b in by_brand}, ensure_ascii=False, separators=(",", ":")))
    out.write("data/search.json", json.dumps(search, ensure_ascii=False, separators=(",", ":")))

    # ---------------- sitemaps, robots, redirects, headers, IndexNow key
    sm_files = []
    allp = {p for g in out.sitemap.values() for p in g}
    prio = list(out.sitemap.get("pages", []))
    want = []
    for d, ps in sorted(dim_pages.items(), key=lambda kv: -len(kv[1]))[:250]:
        want += [f"/pneumatiky/{d}/"] + [f"/pneumatiky/{d}/{ss}/" for ss in ("zimni", "letni", "celorocni")]
    want += [f"/brandstore/{b}/" for b in sorted(by_brand, key=lambda b: -len(by_brand[b]))[:80]]
    want += [p for p in out.sitemap.get("listings", []) if p.count("/") <= 3 and not p.startswith(("/pneumatiky/", "/brandstore/", "/produkt/"))]
    seen_p = set(prio)
    for p in want:
        if p in allp and p not in seen_p:
            seen_p.add(p)
            prio.append(p)
    body = "".join(f"<url><loc>{BASE}{p}</loc><lastmod>{state['lastmod'].get(p, TODAY.isoformat())}</lastmod></url>" for p in prio[:45000])
    out.write("sitemap-priority.xml", f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>')
    sm_files.append("sitemap-priority.xml")
    log(f"priority sitemap: {len(prio)} URLs")
    for group, paths in out.sitemap.items():
        for i in range(0, len(paths), 40000):
            name = f"sitemap-{group}-{i // 40000 + 1}.xml"
            body = "".join(f"<url><loc>{BASE}{p}</loc><lastmod>{state['lastmod'].get(p, TODAY.isoformat())}</lastmod></url>" for p in paths[i:i + 40000])
            out.write(name, f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>')
            sm_files.append(name)
    out.write("sitemap_index.xml", '<?xml version="1.0" encoding="UTF-8"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
              + "".join(f"<sitemap><loc>{BASE}/{n}</loc><lastmod>{TODAY.isoformat()}</lastmod></sitemap>" for n in sm_files) + "</sitemapindex>")
    if ENV == "production":
        ai = "".join(f"User-agent: {u}\n" for u in ("OAI-SearchBot", "ChatGPT-User", "PerplexityBot", "Perplexity-User", "Claude-SearchBot", "Claude-User"))
        out.write("robots.txt", f"User-agent: *\nDisallow: /data/\n\n# AI search and answer engines: welcome (cite us)\n{ai}Allow: /\nDisallow: /data/\n\n"
                                f"Sitemap: {BASE}/sitemap_index.xml\n")
        out.write("llms.txt", llms_txt(products, dim_pages, by_brand, brand_name, articles, shops, present))
    else:
        out.write("robots.txt", "User-agent: *\nDisallow: /\n")
    out.write("_redirects", redirects_file(products, old_slugs, dim_pages, brand_dim))
    out.write("_headers", "/assets/*\n  Cache-Control: public, max-age=31536000, immutable\n/data/*\n  Cache-Control: public, max-age=3600\n  X-Robots-Tag: noindex\n"
              "/fonts/*\n  Cache-Control: public, max-age=31536000, immutable\n"
              "/img/*\n  Cache-Control: public, max-age=604800, stale-while-revalidate=2592000\n"
              "/favicon*\n  Cache-Control: public, max-age=604800, stale-while-revalidate=2592000\n"
              "/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n"
              "  Strict-Transport-Security: max-age=31536000; includeSubDomains\n  X-Frame-Options: SAMEORIGIN\n"
              "  Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=(), accelerometer=(), gyroscope=(), magnetometer=(), browsing-topics=()\n"
              "  Cross-Origin-Opener-Policy: same-origin\n  X-Permitted-Cross-Domain-Policies: none\n"
              f"  Content-Security-Policy: {CSP}\n"
              f"  Link: </assets/{css_name}>; rel=preload; as=style\n  Link: </fonts/figtree-1.woff2>; rel=preload; as=font; type=\"font/woff2\"; crossorigin\n"
              "  Link: </fonts/barlow-c-700.woff2>; rel=preload; as=font; type=\"font/woff2\"; crossorigin\n")
    if INDEXNOW_KEY:
        out.write(f"{INDEXNOW_KEY}.txt", INDEXNOW_KEY)

    # dropped products: forget their content hash so a comeback counts as a change
    live_paths = set(out.hashes) & ({f"/produkt/{p['slug']}/" for p in products if p["page"]} | set(p for g in out.sitemap.values() for p in g) | {"/", "/blog/"})
    for k in list(out.hashes):
        if k.startswith("/produkt/") and k not in live_paths:
            out.hashes.pop(k, None)
    C.save_state(state)
    (APP / "data/state/changed.txt").write_text("\n".join(out.changed))
    json.dump({"at": time.strftime("%Y-%m-%d %H:%M"), "env": ENV, "files": out.files, "products": len(products),
               "product_pages": sum(1 for p in products if p["page"]), "changed": len(out.changed), "shops": sorted(present)},
              open(APP / "data/state/build-report.json", "w"), indent=1)
    if out.files > FILE_LIMIT:
        sys.exit(f"{out.files} files > Workers limit {FILE_LIMIT}")
    log(f"built {out.files} files, {len(out.changed)} changed URLs, in {time.time() - t0:.0f}s")


REDIRECT_STATIC_MAX = 2000      # Workers static assets: 2 000 static + 100 dynamic rules, all free (no Worker runs)


def redirects_file(products, old_slugs, dim_pages, brand_dim):
    """Fixed WordPress redirects + old /produkt/ URLs that have no 1:1 product page, pointed at the closest live listing.
    Cloudflare counts every static rule placed AFTER a dynamic one (splat/placeholder) as dynamic, so all static rules
    go first. Duplicate source paths are rejected, so each path appears once. Misses beyond the limit get 404.html."""
    def dynamic(src):
        return "*" in src or ":" in src

    rules = {}
    for l in (APP / "data/redirects.txt").read_text().splitlines():
        if l.strip() and not l.startswith("#"):
            src = l.split()[0]
            rules.setdefault(src, l)
    # old brand pages (WordPress /brandstore/x/ and /značka/x/) of brands the catalogue no longer has, e.g. rim makers
    have = {p["bslug"] for p in products}
    for u in (APP / "data/old-urls.txt").read_text().split():
        m = re.match(r"https://auty\.cz/(brandstore|zna%C4%8Dka)/([^/]+)/$", u)
        if m and m.group(2) not in have:
            for src in (f"/brandstore/{m.group(2)}/", f"/značka/{m.group(2)}/"):
                rules.setdefault(src, f"{src} /brandstore/ 301")
    static_fixed = [l for src, l in rules.items() if not dynamic(src)]
    dyn = [l for src, l in rules.items() if dynamic(src)]
    room = REDIRECT_STATIC_MAX - len(static_fixed) - 20
    live = {p["slug"] for p in products if p.get("page")}
    brand_slugs = sorted({p["bslug"] for p in products} | {"nokian-tyres", "general-tire", "bf-goodrich", "a-plus"}, key=len, reverse=True)
    alias = {"nokian-tyres": "nokian", "bf-goodrich": "bfgoodrich", "a-plus": "aplus"}
    cands = {}
    for slug in dict.fromkeys(old_slugs):
        src = f"/produkt/{slug}/"
        if slug in live or src in rules:
            continue
        o = C.parse_old(slug, brand_slugs)
        if not o:
            continue
        d = T.dim_slug(o["w"], o["p"], o["r"])
        b = alias.get(o["brand"], o["brand"])
        to = f"/brandstore/{b}/{d}/" if (b, d) in brand_dim else f"/pneumatiky/{d}/" if d in dim_pages else None
        if to:
            cands[src] = (-len(dim_pages.get(d, [])), src, to)
    picked = sorted(cands.values())[:max(room, 0)]
    gen = [f"{src} {to} 301" for _, src, to in picked]
    log(f"_redirects: {len(static_fixed)} static + {len(gen)} old product URLs (of {len(cands)} redirectable) + {len(dyn)} dynamic")
    if len(dyn) > 100:
        sys.exit("too many dynamic _redirects rules")
    return ("# Generated by build.py. Static rules first (Cloudflare counts static rules after a dynamic one as dynamic).\n"
            + "\n".join(static_fixed + gen) + "\n" + "\n".join(dyn) + "\n")


# ======================================================================= page builders

def offer_rows(S, p):
    rows = []
    best = p["offers"][0]["price"]
    for i, o in enumerate(p["offers"]):
        m = S.shops.get(o["shop"], {"name": o["shop"], "color": "#999"})
        diff = f'<small>+{R.kc(o["price"] - best)}</small>' if i else ""
        if o.get("dot") and o["dot"] < TODAY.year - 1:
            diff += f'<small>výroba {o["dot"]} (starší kus)</small>'
        rows.append(f'<li class="ticket{" best1" if i == 0 else ""}"><span class="stripe" style="background:{m["color"]}"></span>'
                    f'<span class="shop">{S.shop_logo(o["shop"])}<span class="sname"><b>{E(m["name"])}</b>{" <span class=tag-best>nejlevněji</span>" if i == 0 and len(p["offers"]) > 1 else ""}{diff}</span></span>'
                    f'<span class="tprice">{R.kc(o["price"])}</span><a class="btn" href="{E(o["go"])}" {S.go_attrs(o["shop"])}>Do obchodu</a></li>')
    return '<ul class="offers">' + "".join(rows) + "</ul>"


def fits(S, d):
    cs = getattr(S, "cars_by_dim", {}).get(d, [])
    if not cs:
        return ""
    return f'<p class="fits"><b>Tovární rozměr mimo jiné pro:</b> {car_links(cs)}. <span class="muted">Ověřte v technickém průkazu.</span></p>'


def product_text(S, p):
    s = T.SEASON_CZ[p["season"]]
    veh = {"car": "osobní auta", "suv": "SUV a terénní vozy", "van": "dodávky a lehká užitková vozidla", "moto": "motocykly", "truck": "nákladní vozidla"}[p["vehicle"]]
    t = [f"{E(p['brand'])} {E(p['model'])} je {s} pneumatika pro {veh} v rozměru {T.dim_label(p['w'], p['p'], p['r'])}."]
    if p["li"]:
        li = p["li"].split("/")[0]
        kg = T.LI_KG.get(int(li)) if li.isdigit() else None
        if kg:
            t.append(f"Index nosnosti {p['li']} znamená, že jedna pneumatika unese až {kg} kg (celá sada čtyř kol {nf(kg * 4)} kg).")
        if p["si"] in T.SI_KMH:
            t.append(f"Rychlostní index {p['si']} povoluje jízdu až {T.SI_KMH[p['si']]} km/h.")
    if p["xl"]:
        t.append("Označení XL (extra load) znamená zesílenou konstrukci s vyšší nosností při stejném rozměru.")
    if p["rf"]:
        t.append("Jde o run-flat pneumatiku: s defektem dojedete omezenou rychlostí do servisu. Používejte ji jen na voze se systémem kontroly tlaku.")
    if p["hom"]:
        t.append("Homologace " + ", ".join(f"{h} ({T.HOMOLOG.get(h, h)})" for h in p["hom"]) + " znamená, že pneumatiku schválil výrobce vozu pro své modely.")
    if p["season"] == "winter" or p["pmsf"]:
        t.append("Symbol sněhové vločky v horách (3PMSF) potvrzuje, že pneumatika prošla zkouškou na sněhu; v Česku splňuje povinnost zimních pneumatik od 1. listopadu do 31. března.")
    if len(p["offers"]) > 1:
        lo, hi = p["offers"][0]["price"], p["offers"][-1]["price"]
        t.append(f"Mezi nejlevnějším a nejdražším obchodem je rozdíl {R.kc(hi - lo)} za kus ({round((hi - lo) / hi * 100)} %), na sadu čtyř pneumatik tedy {R.kc((hi - lo) * 4)}.")
    return "<p>" + " ".join(t) + "</p>"


def product_page(S, p, by_model, by_dim, dim_pages, brand_dim, model_url):
    name = S.name(p)
    dl = T.dim_label(p["w"], p["p"], p["r"])
    path = f"/produkt/{p['slug']}/"
    n = len(p["offers"])
    crumbs = [("auty.cz", "/"), ("Pneumatiky", "/pneumatiky/"), (dl, f"/pneumatiky/{p['dim']}/") if p["dim"] in dim_pages else (dl, "/pneumatiky/"),
              (p["brand"], f"/brandstore/{p['bslug']}/"), (p["model"], model_url(p))]
    params = [("Rozměr", f"{dl}{'C' if p['c'] else ''}"), ("Šířka", f"{p['w']} mm"), ("Profil (výška bočnice)", f"{p['p']} % = {int(p['w']) * int(p['p']) // 100} mm"),
              ("Průměr ráfku", f"{p['r']}″ = {round(float(p['r']) * 25.4)} mm"), ("Vnější průměr", f"{round(outer_diameter(p['w'], p['p'], p['r']))} mm"),
              ("Sezóna", T.SEASON_CZ[p["season"]]), ("Vozidlo", T.VEHICLE_CZ[p["vehicle"]])]
    if p["li"]:
        li0 = p["li"].split("/")[0]
        params.append(("Index nosnosti", f"{p['li']} ({T.LI_KG.get(int(li0), '?')} kg)" if li0.isdigit() else p["li"]))
    if p["si"]:
        params.append(("Index rychlosti", f"{p['si']} ({T.SI_KMH.get(p['si'], '?')} km/h)"))
    params += [("Zesílená (XL)", "ano" if p["xl"] else "ne"), ("Run-flat", "ano" if p["rf"] else "ne")]
    if p["hom"]:
        params.append(("Homologace", ", ".join(f"{h} – {T.HOMOLOG.get(h, h)}" for h in p["hom"])))
    if p["pmsf"] or p["ms"]:
        params.append(("Značení", " ".join(x for x, y in (("3PMSF", p["pmsf"]), ("M+S", p["ms"])) if y)))
    table = '<table class="params">' + "".join(f"<tr><th>{k}</th><td>{E(str(v))}</td></tr>" for k, v in params) + "</table>"
    eu = ""
    if p["eu"]:
        e = p["eu"]
        eu = (f'<h2>Štítek EU</h2><div class="eulabel"><div><span class="muted">Valivý odpor</span><b class="eu"><span class="eu-{e["rr"]}" style="font-size:1.4rem;min-width:40px;height:40px">{e["rr"]}</span></b></div>'
              f'<div><span class="muted">Přilnavost za mokra</span><b class="eu"><span class="eu-{e["wg"]}" style="font-size:1.4rem;min-width:40px;height:40px">{e["wg"]}</span></b></div>'
              f'<div><span class="muted">Hluk</span><b>{e["db"]} dB</b><span class="muted">třída {"ABC"[e["nc"] - 1] if 1 <= e["nc"] <= 3 else e["nc"]}</span></div></div>')
    sib = sorted(by_model[p["mkey"]], key=lambda x: (float(x["r"]), int(x["w"]), int(x["p"]), x["li"]))
    sizes = ""
    if len(sib) > 1:
        sizes = f'<h2>Další rozměry {E(p["brand"])} {E(p["model"])}</h2><ul class="sizes">' + "".join(
            (f'<li><a class="cur" aria-current="page">{T.dim_label(x["w"], x["p"], x["r"])} {x["li"]}{x["si"]}<small>{R.kc(x["price"])}</small></a></li>' if x is p else
             f'<li><a href="/produkt/{x["slug"]}/">{T.dim_label(x["w"], x["p"], x["r"])} {x["li"]}{x["si"]}{" XL" if x["xl"] else ""}<small>od {R.kc(x["price"])}</small></a></li>')
            for x in sib[:60] if x["page"] or x is p) + "</ul>"
    same = [x for x in by_dim[p["dim"]] if x["mkey"] != p["mkey"] and x["season"] == p["season"] and x["page"]]
    same = sorted(same, key=lambda x: (-len(x["offers"]), x["price"]))[:8]
    alts = ""
    if same:
        alts = f'<h2>Jiné {T.SEASON_CZ[p["season"]]} pneumatiky {dl}</h2><ul class="pgrid-c">' + "".join(S.prow(x) for x in same) + "</ul>"
        if p["dim"] in dim_pages:
            alts += f'<p style="margin-top:12px"><a class="btn btn--g" href="/pneumatiky/{p["dim"]}/">Všechny pneumatiky {dl} ({len(dim_pages[p["dim"]])})</a></p>'
    best = p["offers"][0]
    img = E(R.img_url(p["img"], "big") or "/assets/tire.svg")
    body = f"""<div class="pd"><div class="pimg"><img src="{img}" alt="{E(name)}" width="300" height="300" fetchpriority="high"></div><div>
<h1>{E(name)}</h1><div class="chips">{S.chips(p)}</div>
<div class="best" id="bestbox"><div><span class="muted">{"nejnižší cena ze " + R.shops_gen(n) if n > 1 else "cena v obchodě " + E(S.shops[best["shop"]]["name"])}</span><br>{R.spz(best["price"], "", xl=True)}</div><div class="best__shop">{S.shop_logo(best["shop"])}</div>
<div><a class="btn" href="{E(best["go"])}" {S.go_attrs(best["shop"])}>Koupit u {E(S.shops[best["shop"]]["name"])}</a><br><span class="muted">sada 4 ks od {R.kc(best["price"] * 4)}</span></div></div>
<h2>Srovnání cen v obchodech</h2>{offer_rows(S, p)}<p class="muted" style="font-size:.85rem;margin-top:8px">Ceny za kus včetně DPH, bez dopravy a montáže. Ceny aktualizujeme každý den; před nákupem je ověřte v obchodě.</p>
</div></div><h2>O pneumatice</h2>{product_text(S, p)}{fits(S, p["dim"])}<h2>Parametry</h2>{table}{eu}{sizes}{alts}
<div class="buybar" id="buybar" hidden><span>{R.kc(best["price"])}<small>{"nejlevněji ze " + R.shops_gen(n) if n > 1 else E(S.shops[best["shop"]]["name"])}</small></span><a class="btn" href="{E(best["go"])}" {S.go_attrs(best["shop"])}>Koupit u {E(S.shops[best["shop"]]["name"])}</a></div>"""
    title = f"{name} – cena od {R.kc(best['price'])} | auty.cz"
    desc = (f"{name}: {f"srovnání cen v {n} obchodech" if n > 1 else 'cena'} od {R.kc(best['price'])}. "
            f"{T.SEASON_CZ[p['season']].capitalize()} pneumatika {dl}, parametry{', štítek EU' if p['eu'] else ''} a další rozměry.")
    ld = {"@context": "https://schema.org", "@type": "Product", "name": name, "brand": {"@type": "Brand", "name": p["brand"]}, "url": BASE + path,
          "description": f"{T.SEASON_CZ[p['season']].capitalize()} pneumatika {p['brand']} {p['model']} {dl} {p['li']}{p['si']}, srovnání cen v {n} obchodech.",
          "category": f"{T.SEASON_CZ[p['season']].capitalize()} pneumatiky", "sku": p["id"],
          "offers": {"@type": "AggregateOffer", "priceCurrency": "CZK", "lowPrice": p["offers"][0]["price"], "highPrice": p["offers"][-1]["price"],
                     "offerCount": n, "availability": "https://schema.org/InStock",
                     "offers": [{"@type": "Offer", "price": o["price"], "priceCurrency": "CZK", "availability": "https://schema.org/InStock",
                                 "itemCondition": "https://schema.org/NewCondition", "url": BASE + path,
                                 "seller": {"@type": "Organization", "name": S.shops.get(o["shop"], {"name": o["shop"]})["name"]}} for o in p["offers"]]}}
    if p["img"]:
        ld["image"] = p["img"]
    if p["eans"]:
        ld["gtin13" if len(p["eans"][0]) == 13 else "gtin"] = p["eans"][0]
    return S.layout(f"/produkt/{p['slug']}/", title, desc, body, crumbs, jsonld=[ld], preload_img=R.img_url(p["img"], "big") or None)


def price_by_season(ps):
    out = {}
    for s in ("summer", "winter", "all"):
        xs = [p["price"] for p in ps if p["season"] == s]
        if xs:
            out[s] = (min(xs), median(xs), len(xs))
    return out


def alt_sizes(d, dim_pages):
    w, p, r = re.match(r"(\d+)-(\d+)-r([\d-]+)", d).groups()
    r = r.replace("-", ".")
    od = outer_diameter(w, p, r)
    res = []
    for k, ps in dim_pages.items():
        if k == d:
            continue
        x = ps[0]
        od2 = outer_diameter(x["w"], x["p"], x["r"])
        if abs(od2 - od) / od <= 0.02 and abs(float(x["r"]) - float(r)) <= 1:
            res.append((abs(od2 - od), k, x, len(ps), od2))
    return sorted(res)[:10]


def fresh_price(p):
    return min((o["price"] for o in p["offers"] if not (o.get("dot") and o["dot"] < TODAY.year - 1)), default=0)


EU_SCORE = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}


def recs(S, items, dl):
    """Three picks for beginners: decent cheapest, best value, premium."""
    pool = [p for p in items if p["page"] and p["vehicle"] in ("car", "suv", "van") and fresh_price(p) and len(p["offers"]) >= 2]
    if len(pool) < 6:
        return ""
    picks = []
    decent = [p for p in pool if R.tier(p["bslug"]) in ("m", "p") or (p["eu"] and p["eu"]["wg"] in "ABC")]
    if decent:
        picks.append(("Nejlevnější slušná", "levná, ale od zavedené značky", min(decent, key=fresh_price)))
    val = [p for p in pool if p["eu"] and p not in [x for _, _, x in picks]]
    if val:
        best = max(val, key=lambda p: (EU_SCORE.get(p["eu"]["wg"], 0) * 2 + EU_SCORE.get(p["eu"]["rr"], 0)) / fresh_price(p))
        picks.append(("Nejlepší poměr cena/výkon", "podle štítku EU (brzdění za mokra, spotřeba) a ceny", best))
    else:
        mids = [p for p in pool if R.tier(p["bslug"]) == "m" and p not in [x for _, _, x in picks]]
        if mids:
            picks.append(("Zlatá střední cesta", "osvědčená střední třída za rozumnou cenu", sorted(mids, key=lambda p: (-len(p["offers"]), fresh_price(p)))[0]))
    prem = [p for p in pool if R.tier(p["bslug"]) == "p" and p not in [x for _, _, x in picks]]
    if prem:
        picks.append(("Prémiová volba", "značky, které vyhrávají testy", min(prem, key=fresh_price)))
    if len(picks) < 2:
        return ""
    cards = "".join(f'<li class="rec"><p class="rec__h"><b>{t}</b><small>{sub}</small></p><ul class="pgrid-c">{S.prow(p)}</ul></li>' for t, sub, p in picks)
    return f'<section class="recs"><h2 style="margin-top:0">Co vybrat v rozměru {dl}?</h2><ul>{cards}</ul></section>'


def dim_page(S, d, ps, season, dim_pages, dim_season, brand_dim, brand_name):
    x0 = ps[0]
    dl = T.dim_label(x0["w"], x0["p"], x0["r"])
    items = [p for p in ps if p["season"] == season] if season else ps
    stats = price_by_season(ps)
    brands = Counter(p["brand"] for p in items)
    shops = len({o["shop"] for p in items for o in p["offers"]})
    sname = T.SEASON_CZ[season] if season else ""
    h1 = f"{sname.capitalize()} pneumatiky {dl}" if season else f"Pneumatiky {dl}"
    path = f"/pneumatiky/{d}/" + (f"{T.SEASON_SLUG[season]}/" if season else "")
    intro = (f"Srovnáváme {len(items)} {R.plural(len(items), 'pneumatiku', 'pneumatiky', 'pneumatik')} {sname + ' ' if season else ''}v rozměru {dl} "
             f"od {len(brands)} {R.plural(len(brands), 'značky', 'značek', 'značek')} ze {R.shops_gen(shops)}. ")
    intro += " ".join(f"{T.SEASON_CZ[s].capitalize()} seženete od {R.kc(v[0])}." for s, v in stats.items() if not season or s == season)
    tabs = '<ul class="pills" style="margin:0 0 18px">' + f'<li><a href="/pneumatiky/{d}/"{" class=on" if not season else ""}>Všechny <small>{len(ps)}</small></a></li>' + "".join(
        f'<li><a href="/pneumatiky/{d}/{T.SEASON_SLUG[s]}/"{" class=on" if s == season else ""}>{R.SEASON_ICON[s]} {T.SEASON_CZ[s]} <small>{len(dim_season[(d, s)])}</small></a></li>'
        for s in ("summer", "winter", "all") if (d, s) in dim_season) + "</ul>"
    bidx = brand_index(ps)
    bcount = Counter(p["bslug"] for p in items)
    brand_opts = [(bidx[b], brand_name[b], n, f"/brandstore/{b}/{d}/" if (b, d) in brand_dim else f"/brandstore/{b}/")
                  for b, n in bcount.most_common()]
    w, pr, r = x0["w"], x0["p"], x0["r"]
    side = int(w) * int(pr) // 100
    od = round(outer_diameter(w, pr, r))
    explain = (f"<h2>Co znamená rozměr {dl}</h2><p><b>{w}</b> je šířka běhounu v milimetrech. <b>{pr}</b> je profil: výška bočnice je {pr} % šířky, tedy asi {side} mm. "
               f"<b>R{r}</b> znamená radiální konstrukci a průměr ráfku {r} palců ({round(float(r) * 25.4)} mm). Vnější průměr nové pneumatiky je zhruba {od} mm. "
               f"Rozměr i minimální index nosnosti a rychlosti, které smíte použít, najdete v technickém průkazu vozu (kolonka pneumatiky a ráfky).</p>")
    alts = alt_sizes(d, dim_pages)
    alt = ""
    if alts:
        alt = (f"<h3>Rozměry s podobným vnějším průměrem</h3><p class=\"muted\">Liší se o méně než 2 %. Použít je smíte jen tehdy, když je má vůz schválené v technickém průkazu nebo COC listu.</p><ul class=\"sizes\">"
               + "".join(f'<li><a href="/pneumatiky/{k}/">{T.dim_label(x["w"], x["p"], x["r"])}<small>{round(od2)} mm · {n} ks</small></a></li>' for _, k, x, n, od2 in alts) + "</ul>")
    lis = Counter(f"{p['li']}{p['si']}" for p in items if p["li"]).most_common(4)
    qa = []
    if stats:
        qa.append((f"Kolik stojí {sname + ' ' if season else ''}pneumatiky {dl}?", " ".join(
            f"{T.SEASON_CZ[s].capitalize()} od {R.kc(v[0])}, polovina nabídek je do {R.kc(v[1])} za kus ({v[2]} modelů)." for s, v in stats.items() if not season or s == season)
            + " Ceny jsou za kus včetně DPH a aktualizují se každý den."))
    if lis:
        qa.append((f"Jaké indexy nosnosti a rychlosti se v rozměru {dl} prodávají nejčastěji?",
                   ", ".join(f"{k} ({c}×)" for k, c in lis) + ". Index nesmí být nižší, než uvádí technický průkaz vozu; u zimních pneumatik je povolen nižší rychlostní index (pak platí omezení rychlosti)."))
    if brands:
        qa.append((f"Které značky mají {sname + ' ' if season else ''}pneumatiky {dl}?", ", ".join(b for b, _ in brands.most_common(12)) + "."))
    cs = getattr(S, "cars_by_dim", {}).get(d, [])
    if cs:
        qa.append((f"Na která auta se dává rozměr {dl}?", "Z výroby ho mají například " + ", ".join(c["name"] for c in cs[:10])
                   + ". Rozměr pro své auto si vždy ověřte v technickém průkazu."))
    faq_html, faq_ld = S.faq(qa)
    list_html = S.listing(items, f"/data/d/{d}.json", brands=brand_opts, fixed_season=season)
    first = not getattr(S, "pg", None) or S.pg[1] == 1
    body = (f'{tabs}{recs(S, items, dl) if first else ""}{list_html}{fits(S, d)}{explain}{alt}{faq_html}'
            + S.size_picker(w, pr, r, "Hledáte jiný rozměr?"))
    cheapest = low(items)
    # searchers type "zimní pneu 195/65 r15" ~7× more often than "zimní pneumatiky 195/65 r15" (Marketing Miner, 8. 11. 2026)
    title = (f"{sname.capitalize()} pneu {dl} – ceny {T.SEASON_GEN[season]} pneumatik od {R.kc(cheapest)} | auty.cz" if season
             else f"Pneu {dl} – srovnání cen pneumatik od {R.kc(cheapest)} | auty.cz")
    desc = f"{h1}: {len(items)} modelů od {len(brands)} značek ze {R.shops_gen(shops)}, ceny od {R.kc(cheapest)}. Filtry podle značky, sezóny, XL a run-flat."
    crumbs = [("auty.cz", "/"), ("Pneumatiky", "/pneumatiky/"), (dl, f"/pneumatiky/{d}/")] + ([(sname.capitalize(), path)] if season else [])
    art = f"/img/ui/sezona-{R.SEASON_IMG[season]}.webp" if season else "/img/ic/pneu.webp"
    return S.layout(path, title, desc, body, crumbs, noindex=len(items) < (8 if season else 5), jsonld=[faq_ld] if faq_ld else None, band=S.band(h1, intro, art))


BRAND_INFO = {  # slug: (country, group, one-line note)
    "michelin": ("Francie", "", "Největší výrobce pneumatik na světě; pneumatiky dlouho vydrží a pravidelně vyhrávají testy."),
    "continental": ("Německo", "", "Jeden z největších výrobců; silný hlavně v brzdění za mokra a v zimních testech."),
    "bridgestone": ("Japonsko", "", "Největší japonský výrobce, dodává pneumatiky do prvovýroby řady automobilek."),
    "goodyear": ("USA", "", "Americký výrobce s evropským vývojem; známé řady EfficientGrip, UltraGrip a Vector 4Seasons."),
    "pirelli": ("Itálie", "", "Prémiový italský výrobce zaměřený na výkonná a sportovní auta."),
    "dunlop": ("Velká Británie", "", "Tradiční značka s historií od roku 1888, silná ve sportovních pneumatikách."),
    "nokian": ("Finsko", "", "Severský specialista na zimní pneumatiky, vynálezce zimní pneumatiky (1934)."),
    "nokian-tyres": ("Finsko", "", "Severský specialista na zimní pneumatiky, vynálezce zimní pneumatiky (1934)."),
    "vredestein": ("Nizozemsko", "skupina Apollo", "Známá designovými pneumatikami a dobrými celoročními vzorky Quatrac."),
    "hankook": ("Jižní Korea", "", "Největší korejský výrobce; kvalita blízko prémiových značek za nižší cenu."),
    "kumho": ("Jižní Korea", "", "Korejská značka střední třídy s dobrým poměrem ceny a výkonu."),
    "nexen": ("Jižní Korea", "", "Korejská značka střední třídy, dodává i do prvovýroby (např. Škoda, VW)."),
    "toyo": ("Japonsko", "", "Japonská značka oblíbená u SUV a terénních vozů."),
    "yokohama": ("Japonsko", "", "Japonský výrobce s tradicí v motorsportu."),
    "falken": ("Japonsko", "skupina Sumitomo", "Japonská značka střední třídy s dobrými výsledky v testech."),
    "kleber": ("Francie", "skupina Michelin", "Levnější značka skupiny Michelin, známá zimními a celoročními vzorky."),
    "bfgoodrich": ("USA", "skupina Michelin", "Legenda terénních pneumatik (All-Terrain KO2) se silničními vzorky pro osobní auta."),
    "uniroyal": ("Německo", "skupina Continental", "Značka koncernu Continental proslulá výkonem na mokru (RainSport, RainExpert)."),
    "barum": ("Česko", "skupina Continental", "Pneumatiky z Otrokovic s technologií Continentalu; oblíbená volba za rozumnou cenu."),
    "semperit": ("Rakousko", "skupina Continental", "Značka koncernu Continental s důrazem na zimní pneumatiky."),
    "matador": ("Slovensko", "skupina Continental", "Původem slovenská značka, dnes vyráběná pod koncernem Continental."),
    "fulda": ("Německo", "skupina Goodyear", "Německá značka koncernu Goodyear střední třídy."),
    "sava": ("Slovinsko", "skupina Goodyear", "Levnější značka koncernu Goodyear."),
    "debica": ("Polsko", "skupina Goodyear", "Polská značka koncernu Goodyear za příznivou cenu."),
    "firestone": ("USA", "skupina Bridgestone", "Americká značka koncernu Bridgestone střední třídy."),
    "laufenn": ("Jižní Korea", "skupina Hankook", "Levnější značka koncernu Hankook."),
    "kormoran": ("Srbsko", "skupina Michelin", "Rozpočtová značka koncernu Michelin."),
    "tigar": ("Srbsko", "skupina Michelin", "Rozpočtová značka koncernu Michelin vyráběná v Srbsku."),
    "gt-radial": ("Singapur", "skupina Giti", "Asijská značka koncernu Giti s dobrým poměrem cena/výkon."),
    "nankang": ("Tchaj-wan", "", "Tchajwanský výrobce levnějších pneumatik."),
    "apollo": ("Indie", "", "Indický výrobce, vlastník značky Vredestein."),
    "general": ("USA", "skupina Continental", "Americká značka koncernu Continental, hlavně pro SUV."),
    "cooper": ("USA", "skupina Goodyear", "Americká značka, silná v pneumatikách pro SUV."),
    "maxxis": ("Tchaj-wan", "", "Tchajwanský výrobce známý pneumatikami pro motocykly, čtyřkolky a přívěsy."),
    "sailun": ("Čína", "", "Čínský výrobce levných pneumatik."),
    "linglong": ("Čína", "", "Jeden z největších čínských výrobců pneumatik."),
    "triangle": ("Čína", "", "Čínský výrobce levných pneumatik pro osobní i nákladní auta."),
    "metzeler": ("Německo", "skupina Pirelli", "Specialista na motocyklové pneumatiky."),
    "mitas": ("Česko", "", "Český výrobce pneumatik pro motocykly a zemědělskou techniku."),
    "heidenau": ("Německo", "", "Německý výrobce pneumatik pro motocykly a skútry."),
}


def brand_page(S, b, ps, by_model, brand_dim, mstate, model_url):
    brand = ps[0]["brand"]
    models = defaultdict(list)
    for p in ps:
        models[p["mkey"]].append(p)
    ml = sorted(models.values(), key=lambda x: -len(x))
    seas = Counter(p["season"] for p in ps)
    dims = sorted(((len(v), d) for (bb, d), v in brand_dim.items() if bb == b), reverse=True)
    body = ""
    intro = (f'Srovnáváme {len(ps)} {R.plural(len(ps), "pneumatiku", "pneumatiky", "pneumatik")} {E(brand)} v {len(ml)} '
             f'{R.plural(len(ml), "vzorku", "vzorcích", "vzorcích")}: ' + ", ".join(f"{T.SEASON_CZ[s]} {n}" for s, n in seas.most_common()) +
             f'. Nejlevnější od {R.kc(low(ps))}.')
    info = BRAND_INFO.get(b)
    if info and (not getattr(S, "pg", None) or S.pg[1] == 1):
        country, group, note = info
        top_m = ", ".join(E(x[0]["model"]) for x in ml[:4])
        body += (f'<div class="bprof"><p><b>{E(brand)}</b> je {({"p": "prémiová značka", "m": "značka střední třídy", "b": "levná značka"})[R.tier(b)]} pneumatik ({country}{", " + group if group else ""}). {note}</p>'
                 f'<p class="muted">Nejvíc rozměrů u nás mají vzorky {top_m}.</p></div>')
    bs = [(x, n) for x, n in seas.most_common() if (b, x) in getattr(S, "brand_season", ())]
    if bs:
        body += '<ul class="pills" style="margin:0 0 14px">' + "".join(
            f'<li><a href="/brandstore/{b}/{T.SEASON_SLUG[x]}/">{R.SEASON_ICON[x]} {T.SEASON_CZ[x].capitalize()} pneumatiky {E(brand)} <small>{n}</small></a></li>' for x, n in bs) + "</ul>"
    body += "<h2 style=\"margin-top:0\">Vzorky</h2><ul class=\"pills\">" + "".join(
        f'<li><a href="{model_url(x[0])}">{R.SEASON_ICON[x[0]["season"]]} {E(x[0]["model"])} <small>{len(x)}</small></a></li>' for x in ml[:80]) + "</ul>"
    dim_opts = [(d, dim_lbl(d), n, f"/brandstore/{b}/{d}/" if (b, d) in brand_dim else f"/pneumatiky/{d}/")
                for d, n in Counter(p["dim"] for p in ps).most_common(150) if d in S.dim_set]
    body += "<h2>Všechny pneumatiky</h2>" + S.listing(ps, f"/data/b/{b}.json", show_brand=False, dims=dim_opts, lazy=len(ps) > 1500)
    kinds = [T.SEASON_CZ[x] for x in ("summer", "winter", "all") if seas.get(x)]
    title = f"Pneumatiky {brand} – {', '.join(kinds[:-1]) + ' i ' + kinds[-1] if len(kinds) > 1 else kinds[0]} od {R.kc(low(ps))} | auty.cz"
    desc = f"Pneumatiky {brand}: {len(ps)} rozměrů v {len(ml)} vzorcích, srovnání cen v českých e-shopech. Letní, zimní i celoroční."
    return S.layout(f"/brandstore/{b}/", title, desc, body, [("auty.cz", "/"), ("Značky", "/brandstore/"), (brand, f"/brandstore/{b}/")], noindex=len(ps) < 3,
                    band=S.band(f"Pneumatiky {E(brand)}", intro, "/img/ic/cena.webp"))


def brand_dim_page(S, b, d, ps, brand_name, model_url):
    brand = brand_name[b]
    x0 = ps[0]
    dl = T.dim_label(x0["w"], x0["p"], x0["r"])
    ps = sorted(ps, key=lambda p: (-len(p["offers"]), p["price"]))
    body = (f"<h1>{E(brand)} {dl}</h1><p class=\"muted\">{len(ps)} {R.plural(len(ps), 'pneumatika', 'pneumatiky', 'pneumatik')} {E(brand)} v rozměru {dl}, "
            f"nejlevnější za {R.kc(min(p['price'] for p in ps))}. " + ", ".join(f"{T.SEASON_CZ[s]}: {n}" for s, n in Counter(p["season"] for p in ps).most_common()) + ".</p>")
    body += '<ul class="pgrid-c">' + "".join(S.prow(p) for p in ps) + "</ul>"
    body += f'<p style="margin-top:16px"><a class="btn btn--g" href="/pneumatiky/{d}/">Všechny značky v rozměru {dl}</a> <a class="btn btn--g" href="/brandstore/{b}/">Všechny pneumatiky {E(brand)}</a></p>'
    ks = [T.SEASON_CZ[x] for x, _ in Counter(p["season"] for p in ps).most_common()]
    title = f"{brand} {dl} – {', '.join(ks[:-1]) + ' i ' + ks[-1] if len(ks) > 1 else ks[0]} pneu od {R.kc(min(p['price'] for p in ps))} | auty.cz"
    desc = f"{brand} {dl}: {len(ps)} pneumatik, srovnání cen v českých e-shopech. Letní, zimní a celoroční vzorky."
    return S.layout(f"/brandstore/{b}/{d}/", title, desc, body,
                    [("auty.cz", "/"), ("Značky", "/brandstore/"), (brand, f"/brandstore/{b}/"), (dl, f"/brandstore/{b}/{d}/")], noindex=len(ps) < 6)


def model_page(S, ps, model_url, dim_pages):
    x0 = ps[0]
    name = f"{x0['brand']} {x0['model']}"
    ps = sorted(ps, key=lambda p: (float(p["r"]), int(p["w"]), int(p["p"]), p["li"]))
    seas = Counter(p["season"] for p in ps).most_common(1)[0][0]
    rims = sorted({p["r"] for p in ps}, key=float)
    eus = [p["eu"] for p in ps if p["eu"]]
    body = f"<h1>{E(name)}</h1><div class=\"chips\" style=\"margin-bottom:10px\"><span class=\"chip s-{seas}\">{R.SEASON_ICON[seas]} {T.SEASON_CZ[seas]}</span></div>"
    body += (f'<p class="muted" style="max-width:60em">{E(name)} je {T.SEASON_CZ[seas]} pneumatika značky {E(x0["brand"])}. Srovnáváme ji v {len(ps)} '
             f'{R.plural(len(ps), "rozměru", "rozměrech", "rozměrech")} pro ráfky R{rims[0]}' + (f"–R{rims[-1]}" if len(rims) > 1 else "") +
             f', nejlevněji od {R.kc(low(ps))} za kus.')
    if eus:
        body += " Podle štítku EU: valivý odpor " + "/".join(sorted({e["rr"] for e in eus})) + ", přilnavost za mokra " + "/".join(sorted({e["wg"] for e in eus})) + \
                f", hluk {min(e['db'] for e in eus)}–{max(e['db'] for e in eus)} dB."
    body += "</p><h2>Rozměry a ceny</h2><ul class=\"pgrid-c\">" + "".join(S.prow(p) for p in ps[:150]) + "</ul>"
    body += f'<p style="margin-top:16px"><a class="btn btn--g" href="/brandstore/{x0["bslug"]}/">Další vzorky {E(x0["brand"])}</a></p>'
    title = f"{name} – ceny od {R.kc(low(ps))} ({len(ps)} {R.plural(len(ps), 'rozměr', 'rozměry', 'rozměrů')}) | auty.cz"
    desc = f"{name}: {T.SEASON_CZ[seas]} pneumatika, {len(ps)} rozměrů, srovnání cen v českých e-shopech od {R.kc(min(p['price'] for p in ps))}."
    return S.layout(model_url(x0), title, desc, body, [("auty.cz", "/"), ("Značky", "/brandstore/"), (x0["brand"], f"/brandstore/{x0['bslug']}/"), (x0["model"], model_url(x0))])


def dims_index(S, dim_pages):
    by_rim = defaultdict(list)
    for d, ps in dim_pages.items():
        by_rim[ps[0]["r"]].append((d, ps))
    body = "<h1>Rozměry pneumatik</h1><p class=\"muted\">Vyberte rozměr podle technického průkazu. Čísla v závorce = počet pneumatik, které srovnáváme.</p>" + S.size_picker()
    for r in sorted(by_rim, key=float):
        items = sorted(by_rim[r], key=lambda x: (int(x[1][0]["w"]), int(x[1][0]["p"])))
        body += f"<h2>Ráfek R{r}</h2><ul class=\"pills\">" + "".join(
            f'<li><a href="/pneumatiky/{d}/">{T.dim_label(ps[0]["w"], ps[0]["p"], ps[0]["r"])} <small>{len(ps)}</small></a></li>' for d, ps in items) + "</ul>"
    return S.layout("/pneumatiky/", "Rozměry pneumatik – přehled všech rozměrů pneu a srovnání cen | auty.cz",
                    f"Všech {len(dim_pages)} rozměrů pneumatik od R10 po R24 se srovnáním cen v českých e-shopech.", body, [("auty.cz", "/"), ("Pneumatiky", "/pneumatiky/")])


def brands_index(S, by_brand, brand_name):
    bs = sorted(by_brand, key=lambda b: -len(by_brand[b]))
    tiles = "".join(f'<a class="brandtile" href="/brandstore/{b}/"><i style="background:{R.SHOP_COLORS[i % len(R.SHOP_COLORS)]}">{E(brand_name[b][:2].upper())}</i>'
                    f'<span>{E(brand_name[b])}<small>{len(by_brand[b])} pneumatik</small></span></a>' for i, b in enumerate(bs))
    return S.layout("/brandstore/", "Značky pneumatik – Michelin, Continental, Hankook… | auty.cz",
                    f"{len(bs)} značek pneumatik se srovnáním cen: Michelin, Continental, Pirelli, Hankook, Bridgestone a další.",
                    f'<h1>Značky pneumatik</h1><div class="brands">{tiles}</div>', [("auty.cz", "/"), ("Značky", "/brandstore/")])



A_PREZUT = "/kdy-prezout-na-zimni-letni-pneumatiky-pravidlo-7-c-a-co-rika-zakon/"
A_CELOROC = "/celorocni-pneumatiky-vyplati-se-vam-nebo-je-to-hazard-velke-srovnani/"
A_RUNFLAT = "/run-flat-pneumatiky-muzete-s-pichnutym-kolem-opravdu-dojet-az-domu/"
A_SJETE = "/ruska-ruleta-na-silnici-proc-je-jizda-na-sjetych-pneu-hazard-ktery-nemuzete-vyhrat/"
PASSENGER = ("car", "suv", "van")


def low(items):
    """Honest 'from' price: fresh stock only (no old-DOT clearance), real car sizes (no 10" scooter tubes) unless nothing else."""
    xs = [fresh_price(p) for p in items if (p["vehicle"] == "moto" or float(p["r"]) >= 13)]
    xs = [x for x in xs if x] or [p["price"] for p in items]
    return min(xs)


def lnk(href, text):
    return f'<a href="{href}">{text}</a>'


def tier_prices(items):
    """Median fresh price per brand class in the most common passenger size of `items` -> (dim label, {tier: median})."""
    pas = [p for p in items if p["vehicle"] in PASSENGER and fresh_price(p)]
    if not pas:
        return None, {}
    d = Counter(p["dim"] for p in pas).most_common(1)[0][0]
    out = {}
    for t in ("b", "m", "p"):
        xs = [fresh_price(p) for p in pas if p["dim"] == d and R.tier(p["bslug"]) == t]
        if len(xs) >= 3:
            out[t] = median(xs)
    return dim_lbl(d), out


def tier_sentence(items, what):
    dl, tp = tier_prices(items)
    if len(tp) < 2:
        return ""
    names = {"b": "levné značky", "m": "střední třída", "p": "prémiové značky"}
    return (f"Pro představu: {what} v nejčastějším rozměru {dl} stojí v polovině nabídek do " +
            ", ".join(f"{R.kc(v)} ({names[t]})" for t, v in tp.items()) + " za kus.")


def quick_links(S, items, season, dim_season, brand_season, rim_season, extra=()):
    """Internal links under a hub: popular sizes, rims, brands and related categories."""
    ss = T.SEASON_SLUG.get(season, "")
    parts = []
    dims = Counter(p["dim"] for p in items if p["vehicle"] in PASSENGER).most_common(16)
    if dims:
        parts.append("<h3>Nejhledanější rozměry</h3><ul class=\"pills\">" + "".join(
            f'<li><a href="/pneumatiky/{d}/{ss + "/" if season and (d, season) in dim_season else ""}">{dim_lbl(d)} <small>{n}</small></a></li>' for d, n in dims) + "</ul>")
    if season:
        rims = [(r, len(v)) for (s, r), v in sorted(rim_season.items(), key=lambda x: float(x[0][1])) if s == season]
        if rims:
            parts.append(f"<h3>{T.SEASON_CZ[season].capitalize()} pneumatiky podle ráfku</h3><ul class=\"pills\">" + "".join(
                f'<li><a href="/{ss}-pneumatiky/r{r}/">R{r} <small>{n}</small></a></li>' for r, n in rims) + "</ul>")
        brands = sorted(((len(v), b) for (b, s), v in brand_season.items() if s == season), reverse=True)[:24]
        if brands:
            parts.append(f"<h3>{T.SEASON_CZ[season].capitalize()} pneumatiky podle značky</h3><ul class=\"pills\">" + "".join(
                f'<li><a href="/brandstore/{b}/{ss}/">{E(v[0]["brand"])} <small>{n}</small></a></li>' for n, b in brands for v in [brand_season[(b, season)]]) + "</ul>")
    if extra:
        parts.append("<h3>Mohlo by se hodit</h3><ul class=\"pills\">" + "".join(f'<li><a href="{u}">{t}</a></li>' for u, t in extra) + "</ul>")
    return '<section class="qlinks">' + "".join(parts) + "</section>" if parts else ""


def hub_content(slug, items, n_shops):
    """(h1, title keyword, lead, guide html, faq) for a season / vehicle hub. Numbers come from the catalogue."""
    lo = low(items)
    fp = sorted(x for x in (fresh_price(p) for p in items) if x)
    med = median(fp) if fp else lo
    nb = len({p["bslug"] for p in items})
    top_b = ", ".join(b for b, _ in Counter(p["brand"] for p in items if R.tier(p["bslug"]) in "mp").most_common(8))
    shops = R.shops_gen(n_shops)
    if slug == "letni-pneumatiky":
        guide = (f"<h2>Jak vybrat letní pneumatiky</h2><p>Nejdřív rozměr: najdete ho v technickém průkazu, na štítku ve dveřích řidiče nebo na boku pneumatik, které máte teď "
                 f"(nevíte jak? {lnk('/rozmery-pneumatik/', 'návod s obrázky')}). Index nosnosti a rychlosti nesmí být nižší, než uvádí technický průkaz — "
                 f"{lnk('/znaceni-pneumatik/', 'co znamená 91V a další značení')}.</p>"
                 "<p>Pak štítek EU. Nejdůležitější je <b>přilnavost za mokra</b> (A je nejlepší): v létě prší a právě na mokru se pneumatiky liší nejvíc. "
                 "Valivý odpor ovlivňuje spotřebu, hluk v decibelech pohodlí na dálnici.</p>"
                 f"<p>{tier_sentence(items, 'letní pneumatika')} Levná značka dává smysl u auta, které najede málo kilometrů po městě; kdo jezdí hodně po dálnici, "
                 f"ušetří na kratší brzdné dráze a delší životnosti střední nebo prémiové třídy ({E(top_b)}).</p>"
                 f"<h2>Kdy přezout na letní</h2><p>Jakmile se denní teploty drží nad 7 °C — obvykle v dubnu. Zimní pneumatika je v teple měkká, "
                 f"brzdí delší dráhou a rychle se sjíždí. Podrobně v článku {lnk(A_PREZUT, 'kdy přezout na letní pneumatiky')}.</p>")
        faq = [("Kolik stojí letní pneumatiky?", f"Letní pneumatiky srovnáváme od {R.kc(lo)} za kus, polovina nabídek je do {R.kc(med)}. Cena hodně závisí na rozměru — "
                                                  f"vyberte svůj rozměr a uvidíte ceny ze {shops}."),
               ("Jaká je minimální hloubka dezénu letních pneumatik?", "Zákon vyžaduje aspoň 1,6 mm. Na mokru ale pneumatika výrazně ztrácí přilnavost už pod 3 mm, proto je rozumné ji měnit dřív."),
               ("Jsou levné letní pneumatiky bezpečné?", f"Rozhoduje štítek EU, ne cena: hledejte přilnavost za mokra A nebo B. Levné pneumatiky s horší přilnavostí brzdí na mokru delší dráhou. "
                                                         f"Přehled nejlevnějších nabídek najdete na stránce {lnk('/levne-pneumatiky/letni/', 'levné letní pneumatiky')}."),
               ("Kdy přezout na letní pneumatiky?", "Až se denní teploty drží nad 7 °C, v Česku obvykle během dubna. Zimní pneumatiky jsou povinné do 31. března jen při zimních podmínkách na silnici.")]
        return "Letní pneumatiky", "Letní pneu", "Letní pneumatiky (letní pneu) mají tvrdší směs a méně lamel. Nad 7 °C brzdí na suchu i mokru kratší dráhou než zimní a pomaleji se sjíždějí.", guide, faq
    if slug == "zimni-pneumatiky":
        pm = [p for p in items if p["vehicle"] in PASSENGER]
        pct = round(100 * sum(1 for p in pm if p["pmsf"]) / len(pm)) if pm else 0
        guide = (f"<h2>Zimní pneumatiky a zákon</h2><p>Od 1. listopadu do 31. března musí mít osobní auto zimní pneumatiky, když je na silnici souvislá vrstva sněhu, led nebo námraza, "
                 f"nebo se to dá podle počasí čekat. Kde stojí značka <b>Zimní výbava</b>, platí povinnost bez ohledu na počasí. Minimální hloubka dezénu je 4 mm. "
                 f"Všechno podstatné shrnuje článek {lnk('/povinnost-zimnich-pneumatik/', 'povinnost zimních pneumatik')}.</p>"
                 f"<h2>M+S, nebo vločka 3PMSF?</h2><p>Značka M+S (bláto a sníh) je jen prohlášení výrobce. Symbol hory s vločkou (3PMSF) znamená, že pneumatika prošla "
                 f"normovaným testem na sněhu. Český zákon uznává i M+S, ale v Německu platí od října 2024 jako zimní jen pneumatiky s vločkou — kdo jezdí do zahraničí, kupuje vločku. "
                 f"Z {nf(len(pm))} zimních pneumatik pro osobní auta, SUV a dodávky, které srovnáváme, ji má {pct} %.</p>"
                 f"<h2>Jak vybrat zimní pneumatiky</h2><p>Na štítku EU sledujte přilnavost za mokra — v české zimě je víc mokra a břečky než sněhu. "
                 f"{tier_sentence(items, 'zimní pneumatika')} Na hory a do vyšších poloh se vyplatí pneumatika z testů s dobrým hodnocením na sněhu ({E(top_b)}).</p>")
        faq = [("Od kdy jsou zimní pneumatiky povinné?", "Od 1. listopadu do 31. března, pokud je na silnici sníh, led nebo námraza nebo to lze podle počasí předpokládat. Na úsecích se značkou Zimní výbava platí povinnost vždy."),
               ("Kolik stojí zimní pneumatiky?", f"Zimní pneumatiky srovnáváme od {R.kc(lo)} za kus, polovina nabídek je do {R.kc(med)}. Sada čtyř pneumatik v běžném rozměru 205/55 R16 vyjde od zhruba čtyř tisíc korun."),
               ("Jaká musí být hloubka dezénu zimních pneumatik?", "Aspoň 4 mm u aut do 3,5 tuny a 6 mm u těžších. Pod touto hranicí už pneumatika jako zimní neplatí, i když má značku M+S nebo vločku."),
               ("Kde koupit zimní pneumatiky nejlevněji?", f"Stejnou pneumatiku porovnáváme ve {shops} a nejlevnější obchod je vždy nahoře. Nejlevnější nabídky vůbec najdete na stránce {lnk('/levne-pneumatiky/zimni/', 'levné zimní pneumatiky')}.")]
        return "Zimní pneumatiky", "Zimní pneu", "Zimní pneumatiky (zimní pneu, zimní gumy) jsou v Česku povinné od 1. listopadu do 31. března při zimních podmínkách. Měkká směs a hustě lamelovaný dezén drží na sněhu, ledu i studeném mokru.", guide, faq
    if slug == "celorocni-pneumatiky":
        pct = round(100 * sum(1 for p in items if p["pmsf"]) / len(items)) if items else 0
        guide = (f"<h2>Pro koho jsou celoroční pneumatiky</h2><p>Celoroční (univerzální) pneumatiky se hodí do města a nížin, pro auta, která najedou do zhruba 15 000 km ročně "
                 f"a v zimě nemusí na hory. Ušetříte jedno přezouvání a místo na uskladnění. Kdo jezdí hodně nebo často do kopců, je lépe vybaven dvěma sadami. "
                 f"Srovnání výhod a nevýhod najdete v článku {lnk(A_CELOROC, 'vyplatí se celoroční pneumatiky')}.</p>"
                 f"<h2>Zimní povinnost a vločka</h2><p>Celoroční pneumatika splní zimní povinnost, pokud má značení M+S a dezén aspoň 4 mm; vločku 3PMSF má {pct} % celoročních pneumatik, které srovnáváme, "
                 f"a ty jsou nejbezpečnější volbou (v Německu se bez ní jako zimní neuznávají).</p>"
                 f"<p>{tier_sentence(items, 'celoroční pneumatika')} Nejlepší výsledky v testech celoročních pneumatik mívají vzorky prémiových značek ({E(top_b)}).</p>")
        faq = [("Vyplatí se celoroční pneumatiky?", "Ano, pokud jezdíte hlavně po městě a v nížinách a najedete do 15 000 km ročně. V létě brzdí o něco delší dráhou než letní, v hlubokém sněhu jsou slabší než zimní."),
               ("Splňují celoroční pneumatiky zimní povinnost?", "Ano, když mají značení M+S (lépe i vločku 3PMSF) a hloubku dezénu aspoň 4 mm."),
               ("Kolik stojí celoroční pneumatiky?", f"Celoroční pneumatiky srovnáváme od {R.kc(lo)} za kus, polovina nabídek je do {R.kc(med)}."),
               ("Jak dlouho vydrží celoroční pneumatiky?", "Protože se jezdí celý rok, sjíždějí se rychleji — obvykle 3–4 roky nebo 40 až 50 tisíc kilometrů. Pod 4 mm dezénu už neplatí jako zimní.")]
        return "Celoroční pneumatiky", "Celoroční pneu", "Celoroční pneumatiky (celoroční pneu) se nemusí přezouvat: se značením M+S a dezénem nad 4 mm splní i zimní povinnost. Hodí se hlavně do města a nížin.", guide, faq
    if slug == "osobni-pneumatiky":
        guide = (f"<h2>Jak najít správné pneumatiky na osobní auto</h2><p>Rozměr je v technickém průkazu, na štítku ve dveřích řidiče a na boku současných pneumatik. "
                 f"Nejjednodušší je vybrat {lnk('/pneumatiky-podle-auta/', 'pneumatiky podle auta')} nebo projít {lnk('/najdi-pneumatiky/', 'průvodce ve 3 krocích')}.</p>"
                 f"<p>{tier_sentence(items, 'pneumatika na osobní auto')}</p>")
        faq = [("Jak zjistím rozměr pneumatik?", f"V technickém průkazu (řádek pneumatiky), na štítku ve dveřích řidiče nebo na boku pneumatiky, např. 205/55 R16 91V. Návod: {lnk('/rozmery-pneumatik/', 'rozměry pneumatik')}."),
               ("Kolik stojí pneumatiky na osobní auto?", f"Od {R.kc(lo)} za kus, polovina nabídek je do {R.kc(med)}. Rozhoduje rozměr a třída značky."),
               ("Můžu dát na auto jiný rozměr pneumatik?", "Jen rozměr uvedený v technickém průkazu nebo v COC listu vozu. Jiný rozměr vyžaduje schválení.")]
        return "Pneumatiky na osobní auta", "Pneumatiky na osobní auta", "Pneumatiky pro osobní auta v rozměrech od 13 do 22 palců: letní, zimní i celoroční. Nevíte rozměr? Najděte ho podle auta.", guide, faq
    if slug == "pneumatiky-suv":
        guide = ("<h2>Silniční, A/T, nebo M/T?</h2><p>Většina SUV jezdí po asfaltu, a proto potřebuje silniční pneumatiky (H/T) — jsou tišší a brzdí lépe. "
                 "Univerzální A/T (All Terrain) zvládnou i šotolinu a polní cesty, M/T (Mud Terrain) jsou do bláta a terénu, na silnici jsou hlučné. "
                 f"Terénní vzorky najdete na stránce {lnk('/offroad-pneumatiky/', 'off-road pneumatiky')}.</p>"
                 "<p>SUV jsou těžší, proto hlídejte index nosnosti a často je potřeba provedení XL (zesílené).</p>")
        faq = [("Potřebuje SUV speciální pneumatiky?", "SUV potřebuje rozměr a index nosnosti podle technického průkazu. Pro jízdu po silnici stačí běžné silniční vzorky, na terén A/T nebo M/T."),
               ("Kolik stojí pneumatiky na SUV?", f"Od {R.kc(lo)} za kus, polovina nabídek je do {R.kc(med)}. Velké rozměry 19–21 palců jsou výrazně dražší.")]
        return "Pneumatiky na SUV a 4x4", "Pneumatiky na SUV a 4x4", "Pneumatiky pro SUV mají vyšší nosnost a často zesílenou konstrukci (XL). Silniční vzorky jsou tišší, terénní A/T a M/T mají hrubší dezén.", guide, faq
    if slug == "pneumatiky-na-dodavky":
        guide = ("<h2>Co znamená C a dvojitý index</h2><p>Pneumatiky pro dodávky se značí písmenem C (Commercial) za průměrem ráfku, například 195/75 R16C 107/105R. "
                 "První číslo je nosnost při montáži jednotlivě, druhé při dvojmontáži na zadní nápravě. Na dodávku nedávejte pneumatiky pro osobní auta — mají nižší nosnost.</p>")
        faq = [("Můžu dát na dodávku pneumatiky pro osobní auta?", "Ne, pokud technický průkaz předepisuje pneumatiky C. Mají zesílenou konstrukci a vyšší nosnost."),
               ("Kolik stojí pneumatiky na dodávku?", f"Od {R.kc(lo)} za kus, polovina nabídek je do {R.kc(med)}.")]
        return "Pneumatiky na dodávky", "Pneumatiky na dodávky", "Pneumatiky pro dodávky (C) mají zesílenou konstrukci a dvojitý index nosnosti, například 107/105R, pro jednotlivou montáž a dvojmontáž.", guide, faq
    if slug == "pneumatiky-na-motorku":
        guide = ("<h2>Jak vybrat pneumatiky na motorku</h2><p>Přední a zadní pneumatika mají jiný rozměr a často i jiný vzorek — rozměr najdete v technickém průkazu nebo na boku pneumatiky. "
                 "Silniční (sport, touring), enduro a cross pneumatiky se liší směsí i dezénem. Moto pneumatiky stárnou rychleji než autopneumatiky, proto hlídejte rok výroby (DOT).</p>")
        faq = [("Kolik stojí pneumatiky na motorku?", f"Moto pneu srovnáváme od {R.kc(lo)} za kus. Cena závisí hlavně na rozměru a typu (silniční, enduro, cross)."),
               ("Musí být na motorce stejné pneumatiky vpředu i vzadu?", "Výrobci doporučují stejnou značku a řadu na obou kolech, protože jsou navržené jako pár.")]
        return "Pneumatiky na motorku", "Moto pneu", "Moto pneu pro silniční motorky, skútry, enduro i cross. Přední a zadní pneumatika se liší rozměrem i vzorkem.", guide, faq
    guide = "<h2>Nákladní pneumatiky podle nápravy</h2><p>Řídicí (vodicí) pneumatiky jsou pro přední nápravu, záběrové (trakční) pro hnací nápravu a návěsové pro přívěsy a návěsy.</p>"
    faq = [("Kolik stojí nákladní pneumatiky?", f"Nákladní pneu srovnáváme od {R.kc(lo)} za kus.")]
    return "Nákladní pneumatiky", "Nákladní pneu", "Nákladní pneumatiky (nákladní pneu) pro ráfky 17,5″, 19,5″ a 22,5″ se dělí podle nápravy: řídicí, záběrová a návěsová.", guide, faq


def write_hub_json(out, S, key, items, brand_name, order="pop"):
    key_f = (lambda p: p["price"]) if order == "price" else (lambda p: (-len(p["offers"]), p["price"]))
    top = sorted(items, key=key_f)[:HUB_JSON_ITEMS]
    bidx = brand_index(top)
    out.write(f"data/h/{key}.json", json.dumps({"brands": {i: brand_name[b] for b, i in bidx.items()},
                                                 "items": [S.item_json(p, bidx[p["bslug"]]) for p in top]}, ensure_ascii=False, separators=(",", ":")))
    return len(items) > HUB_JSON_ITEMS        # True: JSON is only a sample, the page needs hub mode


def hub_page(S, slug, items, dim_pages, dim_season, season, vehicle, brand_name, brand_dim, brand_season, rim_season):
    path = f"/{slug}/"
    n_shops = len({o["shop"] for p in items for o in p["offers"]})
    h1, kw, lead, guide, qa = hub_content(slug, items, n_shops)
    ss = T.SEASON_SLUG.get(season, "")
    dim_opts = [(d, dim_lbl(d), n, f"/pneumatiky/{d}/{ss}/" if season and (d, season) in dim_season else f"/pneumatiky/{d}/")
                for d, n in Counter(p["dim"] for p in items).most_common(150) if d in dim_pages]
    bcount = Counter(p["bslug"] for p in items)
    brand_opts = [(b, brand_name[b], n, f"/brandstore/{b}/{ss}/" if season and (b, season) in brand_season else f"/brandstore/{b}/") for b, n in bcount.most_common()]
    first = not getattr(S, "pg", None) or S.pg[1] == 1
    extra = []
    if season:
        extra.append((f"/levne-pneumatiky/{ss}/", f"Levné {T.SEASON_CZ[season]} pneumatiky"))
    extra += {"winter": [("/test-zimnich-pneumatik-2026/", "Test zimních pneumatik 2026"), ("/snehove-retezy/", "Sněhové řetězy"), ("/3pmsf-m-s-znaceni/", "M+S a vločka 3PMSF")],
              "all": [("/test-celorocnich-pneumatik-2026/", "Test celoročních pneumatik 2026")],
              "summer": [("/test-letnich-pneumatik-2026/", "Test letních pneumatik 2026")]}.get(season, [])
    extra += [("/testy-pneumatik/", "Testy pneumatik 2026"), ("/eu-stitek-pneumatik/", "Štítek EU"), ("/hloubka-dezenu/", "Hloubka dezénu"), ("/tlak-v-pneumatikach/", "Tlak v pneumatikách"), ("/pneumatiky-podle-auta/", "Pneumatiky podle auta"), ("/najdi-pneumatiky/", "Průvodce výběrem"), ("/run-flat-pneumatiky/", "Run-flat"),
              ("/pneumatiky-pro-elektromobily/", "Pro elektromobily"), ("/vyprodej-pneumatik/", "Výprodej")]
    faq_html, faq_ld = S.faq(qa)
    body = (S.listing(items, f"/data/h/{slug}.json", dims=dim_opts, brands=brand_opts, lazy=True, show_vehicle=vehicle is None,
                      hub={"season": season} if season else {"vehicle": vehicle})
            + (quick_links(S, items, season, dim_season, brand_season, rim_season, extra) + f'<div class="prose hubtext">{guide}</div>' + faq_html if first else ""))
    art = f"/img/ui/sezona-{R.SEASON_IMG[season]}.webp" if season else f"/img/ic/{R.VEHICLE_IMG[vehicle]}.webp"
    lo = low(items)
    intro = f"{lead} Srovnáváme {nf(len(items))} pneumatik od {len(bcount)} značek ze {R.shops_gen(n_shops)}, nejlevněji od {R.kc(lo)}."
    gen = {"letni-pneumatiky": "letních pneumatik", "zimni-pneumatiky": "zimních pneumatik", "celorocni-pneumatiky": "celoročních pneumatik"}.get(slug)
    title = f"{kw} – srovnání cen {gen} od {R.kc(lo)} | auty.cz" if gen else f"{kw} – srovnání cen od {R.kc(lo)} | auty.cz"
    desc = f"{kw}: {nf(len(items))} pneumatik od {len(bcount)} značek ze {R.shops_gen(n_shops)}, ceny od {R.kc(lo)}. Vyberte rozměr a nejlevnější obchod."
    return S.layout(path, title, desc, body, [("auty.cz", "/"), (h1, path)], band=S.band(h1, intro, art), jsonld=[faq_ld] if faq_ld and first else None)


# ----------------------------------------------------------------------- cheap tyres: "levné / nejlevnější pneu" (~17k searches a month)

def cheap_table(items, season):
    """Cheapest tyre and cheapest from an established brand in the 20 most common passenger sizes."""
    dims = [d for d, _ in Counter(p["dim"] for p in items).most_common(20)]
    by = defaultdict(list)
    for p in items:
        by[p["dim"]].append(p)
    rows = ""
    for d in sorted(dims, key=lambda d: (float(by[d][0]["r"]), int(by[d][0]["w"]), int(by[d][0]["p"]))):
        ps = by[d]
        a = min(ps, key=lambda p: p["price"])
        decent = [p for p in ps if R.tier(p["bslug"]) in ("m", "p")]
        b = min(decent, key=lambda p: p["price"]) if decent else None
        ss = f"{T.SEASON_SLUG[season]}/" if season else ""
        cell = lambda p: (f'<a href="/produkt/{p["slug"]}/">{R.kc(p["price"])}</a> <small>{E(p["brand"])}</small>' if p.get("page") else f'{R.kc(p["price"])} <small>{E(p["brand"])}</small>')
        rows += (f'<tr><td><a href="/pneumatiky/{d}/{ss}">{dim_lbl(d)}</a></td><td>{cell(a)}</td><td>{cell(b) if b else "–"}</td></tr>')
    return ('<div class="cheapt"><table><thead><tr><th>Rozměr</th><th>Úplně nejlevnější</th><th>Nejlevnější od zavedené značky</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')


def cheap_page(S, season, items, all_items, dim_pages, brand_name):
    ss = T.SEASON_SLUG.get(season, "")
    path = "/levne-pneumatiky/" + (f"{ss}/" if season else "")
    sname = T.SEASON_CZ.get(season, "")
    h1 = f"Levné {sname} pneumatiky" if season else "Levné pneumatiky"
    lo = min(p["price"] for p in items)
    cheapest = min(items, key=lambda p: p["price"])
    n_shops = len({o["shop"] for p in items for o in p["offers"]})
    multi = [p for p in all_items if len(p["offers"]) >= 3]
    sav = median([R.Site.saving(p) for p in multi]) if multi else 0
    tabs = '<ul class="pills" style="margin:0 0 18px">' + "".join(
        f'<li><a href="/levne-pneumatiky/{(T.SEASON_SLUG[s] + "/") if s else ""}"{" class=on" if s == season else ""}>{R.SEASON_ICON[s] + " " if s else ""}{T.SEASON_CZ[s] if s else "Všechny"}</a></li>'
        for s in (None, "summer", "winter", "all")) + "</ul>"
    first = not getattr(S, "pg", None) or S.pg[1] == 1
    table = (f'<h2 style="margin-top:0">Nejlevnější {sname + " " if season else ""}pneumatiky v nejčastějších rozměrech</h2>'
             '<p class="muted">Úplně nejlevnější kus a nejlevnější pneumatika střední nebo prémiové třídy. Ceny za kus s DPH, jen čerstvé zboží (bez výprodeje starého DOT).</p>'
             + cheap_table(items, season)) if first else ""
    dl, tp = tier_prices(items)
    names = {"b": "levné značky", "m": "střední třída", "p": "prémiové značky"}
    tier_txt = (f"V nejčastějším rozměru {dl} stojí polovina {sname + 'ch ' if season else ''}pneumatik do " + ", ".join(f"{R.kc(v)} ({names[t]})" for t, v in tp.items()) + ". ") if len(tp) >= 2 else ""
    guide = (f"<h2>Jak koupit pneumatiky co nejlevněji</h2><ol><li><b>Porovnejte stejnou pneumatiku ve více obchodech.</b> U pneumatik, které prodávají aspoň tři obchody, "
             f"je rozdíl mezi nejlevnějším a nejdražším obchodem v polovině případů {round(sav)} % a víc.</li>"
             "<li><b>Kupte mimo sezónu.</b> Zimní pneumatiky jsou nejlevnější na jaře a v létě, letní na podzim. Před prvním sněhem ceny rostou a dochází zásoby.</li>"
             f"<li><b>Zvažte střední třídu místo nejlevnější značky.</b> {tier_txt}Střední třída (Barum, Kumho, Nexen, Kleber…) bývá jen o pár stovek dražší a v testech výrazně lepší.</li>"
             f"<li><b>Počítejte celou cenu.</b> Ceny jsou za kus včetně DPH. Přičtěte dopravu (u čtyř kusů ji obchody často dávají zdarma) a přezutí.</li>"
             f"<li><b>Výprodej starších kusů.</b> Pneumatika z loňska až předloňska je pořád nová, pokud byla skladovaná v suchu a temnu. Najdete je ve {lnk('/vyprodej-pneumatik/', 'výprodeji')}; "
             f"jak číst rok výroby, vysvětluje článek {lnk('/stari-pneumatik/', 'stáří pneumatik (DOT)')}.</li></ol>"
             "<h2>Kdy se levné pneumatiky nevyplatí</h2><p>Nejlevnější pneumatiky z Asie (Goldline, Aplus, Royal Black, Tracmax…) mají často horší přilnavost za mokra — na štítku EU třídu C až E. "
             "Pro auto, které jezdí po městě pár tisíc kilometrů ročně, to může stačit. Pro dálnice, hory a rodinné auto doporučujeme aspoň střední třídu s přilnavostí za mokra A nebo B.</p>")
    qa = [(f"Kde koupit {sname + ' ' if season else ''}pneumatiky nejlevněji?", f"Stejnou pneumatiku porovnáváme ve {R.shops_gen(n_shops)} a nejlevnější obchod je vždy nahoře. "
                                                                                   "Vyberte rozměr a seřaďte nabídky podle ceny."),
          (f"Kolik stojí nejlevnější {sname + ' ' if season else ''}pneumatiky?", f"Nejlevnější {sname + ' ' if season else ''}pneumatika pro osobní auto teď stojí {R.kc(lo)} za kus "
                                                                                  f"({E(S.name(cheapest))}). V běžných rozměrech 15–17 palců začínají ceny kolem tisíce korun."),
          ("Jsou levné pneumatiky bezpečné?", "Každá pneumatika prodávaná v EU musí splnit homologaci. Liší se ale brzdnou dráhou na mokru a životností — sledujte štítek EU (přilnavost za mokra A–E)."),
          ("Vyplatí se koupit levné pneumatiky?", "Na málo jezdící auto ve městě ano. Kdo najezdí víc než 10 000 km ročně nebo jezdí po dálnici, ušetří na delší životnosti a bezpečnosti střední třídy.")]
    faq_html, faq_ld = S.faq(qa)
    bcount = Counter(p["bslug"] for p in items)
    brand_opts = [(b, brand_name[b], n, f"/brandstore/{b}/") for b, n in bcount.most_common()]
    dim_opts = [(d, dim_lbl(d), n, f"/pneumatiky/{d}/{ss}/" if season else f"/pneumatiky/{d}/") for d, n in Counter(p["dim"] for p in items).most_common(150) if d in dim_pages]
    key = "levne-pneumatiky" + (f"-{ss}" if season else "")
    body = (tabs + table + S.listing(items, f"/data/h/{key}.json", order="price", dims=dim_opts, brands=brand_opts, lazy=True, show_vehicle=True,
                                     hub={"season": season} if season else None)
            + (f'<div class="prose hubtext">{guide}</div>{faq_html}' if first else ""))
    intro = (f"Nejlevnější {sname + ' ' if season else ''}pneumatiky ze {R.shops_gen(n_shops)} na jednom místě, seřazené od nejnižší ceny. "
             f"Levné pneu pro osobní auta, SUV i dodávky od {R.kc(lo)} za kus — a pro každý rozměr srovnání, kde je stejná pneumatika nejlevnější.")
    title = (f"Levné {sname} pneu – nejlevnější {sname} pneumatiky od {R.kc(lo)} | auty.cz" if season else f"Nejlevnější pneumatiky – levné pneu od {R.kc(lo)} | auty.cz")
    desc = f"{h1}: nejlevnější nabídky ze {R.shops_gen(n_shops)}, od {R.kc(lo)} za kus. Tabulka nejlevnějších pneumatik v nejčastějších rozměrech a rady, jak ušetřit."
    crumbs = [("auty.cz", "/"), ("Levné pneumatiky", "/levne-pneumatiky/")] + ([(sname.capitalize(), path)] if season else [])
    return S.layout(path, title, desc, body, crumbs, band=S.band(h1, intro, "/img/ic/cena.webp"), jsonld=[faq_ld] if faq_ld and first else None)


# ----------------------------------------------------------------------- combos: brand × season, rim × season, special categories

def brand_season_page(S, b, season, items, brand_name, model_url, dim_pages):
    brand = brand_name[b]
    ss, sname = T.SEASON_SLUG[season], T.SEASON_CZ[season]
    path = f"/brandstore/{b}/{ss}/"
    h1 = f"{sname.capitalize()} pneumatiky {brand}"
    models = Counter()
    first_p = {}
    for p in items:
        models[p["mkey"]] += 1
        first_p.setdefault(p["mkey"], p)
    lo = low(items)
    first = not getattr(S, "pg", None) or S.pg[1] == 1
    pills = ("<h2 style=\"margin-top:0\">Vzorky</h2><ul class=\"pills\">" + "".join(
        f'<li><a href="{model_url(first_p[k])}">{E(first_p[k]["model"])} <small>{n}</small></a></li>' for k, n in models.most_common(40)) + "</ul>") if first else ""
    info = BRAND_INFO.get(b)
    prof = (f'{E(brand)} je {({"p": "prémiová značka", "m": "značka střední třídy", "b": "levná značka"})[R.tier(b)]} ({info[0]}{", " + info[1] if info[1] else ""}). {info[2]} ') if info else ""
    dim_opts = [(d, dim_lbl(d), n, f"/brandstore/{b}/{d}/" if (b, d) in S.brand_dim else f"/pneumatiky/{d}/")
                for d, n in Counter(p["dim"] for p in items).most_common(120) if d in dim_pages]
    sib = "".join(f'<li><a href="/brandstore/{b}/{T.SEASON_SLUG[s]}/">{T.SEASON_CZ[s].capitalize()} {E(brand)}</a></li>' for s in ("summer", "winter", "all") if s != season and (b, s) in S.brand_season)
    body = (pills + S.listing(items, f"/data/h/bs-{b}-{ss}.json", show_brand=False, dims=dim_opts, fixed_season=season)
            + (f'<ul class="pills" style="margin-top:18px">{sib}<li><a href="/brandstore/{b}/">Všechny pneumatiky {E(brand)}</a></li>'
               f'<li><a href="/{ss}-pneumatiky/">Všechny {sname} pneumatiky</a></li></ul>' if first else ""))
    intro = (f"{prof}Srovnáváme {len(items)} {sname}ch pneumatik {E(brand)} v {len(models)} {R.plural(len(models), 'vzorku', 'vzorcích', 'vzorcích')}, nejlevněji od {R.kc(lo)} za kus.")
    title = f"{sname.capitalize()} pneumatiky {brand} – ceny od {R.kc(lo)} | auty.cz"
    desc = f"{h1}: {len(items)} pneumatik v {len(models)} vzorcích ({', '.join(E(first_p[k]['model']) for k, _ in models.most_common(3))}), srovnání cen od {R.kc(lo)}."
    return S.layout(path, title, desc, body, [("auty.cz", "/"), ("Značky", "/brandstore/"), (brand, f"/brandstore/{b}/"), (sname.capitalize(), path)],
                    band=S.band(h1, intro, f"/img/ui/sezona-{R.SEASON_IMG[season]}.webp"))


def rim_season_page(S, season, r, items, brand_name, dim_season):
    ss, sname = T.SEASON_SLUG[season], T.SEASON_CZ[season]
    path = f"/{ss}-pneumatiky/r{r}/"
    h1 = f"{sname.capitalize()} pneumatiky R{r}"
    lo = low(items)
    dims = Counter(p["dim"] for p in items)
    first = not getattr(S, "pg", None) or S.pg[1] == 1
    pills = ("<h2 style=\"margin-top:0\">Rozměry R" + r + "</h2><ul class=\"pills\">" + "".join([
        f'<li><a href="/pneumatiky/{d}/{ss + "/" if (d, season) in dim_season else ""}">{dim_lbl(d)} <small>{n}</small></a></li>' for d, n in dims.most_common(60) if d in S.dim_set][:40]) + "</ul>") if first else ""
    bcount = Counter(p["bslug"] for p in items)
    brand_opts = [(b, brand_name[b], n, f"/brandstore/{b}/") for b, n in bcount.most_common()]
    dim_opts = [(d, dim_lbl(d), n, f"/pneumatiky/{d}/{ss}/" if (d, season) in dim_season else f"/pneumatiky/{d}/") for d, n in dims.most_common(100) if d in S.dim_set][:80]
    key = f"r-{ss}-{r}"
    hubmode = len(items) > HUB_JSON_ITEMS
    body = pills + S.listing(items, f"/data/h/{key}.json", dims=dim_opts, brands=brand_opts, lazy=hubmode, fixed_season=season, hub={"season": season} if hubmode else None)
    intro = (f"{sname.capitalize()} pneumatiky na {r}palcové ráfky ({sname} pneu {r}\") pro osobní auta, SUV a dodávky: {nf(len(items))} pneumatik v {len(dims)} rozměrech, "
             f"nejlevněji od {R.kc(lo)}. Nejčastější rozměr je {dim_lbl(dims.most_common(1)[0][0])}.")
    title = f"{sname.capitalize()} pneumatiky R{r} – {sname} pneu {r}\" od {R.kc(lo)} | auty.cz"
    desc = f"{h1}: {nf(len(items))} pneumatik v {len(dims)} rozměrech, srovnání cen v českých e-shopech od {R.kc(lo)}."
    return S.layout(path, title, desc, body, [("auty.cz", "/"), (f"{sname.capitalize()} pneumatiky", f"/{ss}-pneumatiky/"), (f"R{r}", path)],
                    band=S.band(h1, intro, f"/img/ui/sezona-{R.SEASON_IMG[season]}.webp"))


SPECIAL = {  # slug: (h1, title, lead, guide html, faq)
    "run-flat-pneumatiky": ("Run-flat pneumatiky", "Run-flat pneumatiky (RFT, SSR, ZP) – srovnání cen",
                            "Run-flat pneumatiky mají zesílenou bočnici: po defektu s nimi dojedete zhruba 80 km rychlostí do 80 km/h. Značí se RFT, SSR, ZP, ROF nebo DSST.",
                            f"<h2>Na co si dát pozor</h2><p>Run-flat pneumatiky smí jezdit jen na autě se systémem hlídání tlaku (TPMS), jinak defekt nepoznáte. "
                            f"Mají tvrdší jízdu a jsou dražší. Pokud je auto má z výroby (často BMW, Mini, Mercedes), nemá obvykle rezervu — kombinovat run-flat a běžné pneumatiky se nedoporučuje. "
                            f"Víc v článku {lnk(A_RUNFLAT, 'run-flat pneumatiky: můžete s píchnutým kolem dojet domů?')}.</p>",
                            [("Jak poznám run-flat pneumatiku?", "Podle značení na boku: RFT (Bridgestone, Pirelli), SSR (Continental), ZP (Michelin), ROF (Goodyear, Dunlop), DSST (Dunlop)."),
                             ("Můžu dát místo run-flat běžné pneumatiky?", "Technicky ano, ale auto pak potřebuje rezervu nebo opravnou sadu. Výrobce to obvykle nedoporučuje kvůli naladění podvozku.")]),
    "pneumatiky-pro-elektromobily": ("Pneumatiky pro elektromobily", "Pneumatiky pro elektromobily (EV) – srovnání cen",
                                     "Pneumatiky pro elektromobily mají nízký valivý odpor (víc dojezdu), tišší dezén a vyšší nosnost kvůli těžké baterii. Značí se EV, EVc, Elect nebo HL.",
                                     "<h2>Musí mít elektromobil speciální pneumatiky?</h2><p>Nemusí — stačí rozměr a index nosnosti podle technického průkazu. Elektromobil je ale těžší a má okamžitý točivý moment, "
                                     "takže běžné pneumatiky se na něm sjíždějí rychleji. Pneumatiky pro EV mají směs na delší životnost, nízký valivý odpor (na štítku EU třída A nebo B) a pěnu proti hluku.</p>",
                                     [("Co znamená HL na pneumatice?", "HL (High Load) je vyšší nosnost při stejném rozměru — hodí se pro těžké elektromobily a SUV."),
                                      ("Prodlouží pneumatiky pro EV dojezd?", "Nízký valivý odpor může dojezd prodloužit o několik procent. Sledujte třídu valivého odporu na štítku EU.")]),
    "offroad-pneumatiky": ("Off-road pneumatiky", "Off-road pneumatiky (A/T, M/T) na 4x4 – srovnání cen",
                           "Terénní pneumatiky pro SUV a 4x4: univerzální A/T (All Terrain) na silnici i šotolinu a M/T (Mud Terrain) do bláta a těžkého terénu.",
                           "<h2>A/T, nebo M/T?</h2><p>A/T pneumatiky jsou kompromis: na silnici přijatelně tiché, v lehkém terénu a na šotolině jistější než silniční. "
                           "M/T mají velké bloky dezénu a samočisticí drážky do bláta, na asfaltu jsou hlučné a v zimě slabší. Kdo jezdí hlavně po silnici, sáhne po A/T se symbolem vločky 3PMSF.</p>",
                           [("Jsou off-road pneumatiky vhodné na zimu?", "A/T pneumatiky se symbolem vločky 3PMSF ano, M/T obvykle ne — v zimě na silnici brzdí hůř."),
                            ("Co znamená A/T a M/T?", "A/T = All Terrain (univerzální terén), M/T = Mud Terrain (bláto a těžký terén).")]),
    "vyprodej-pneumatik": ("Výprodej pneumatik", "Výprodej pneumatik – starší DOT levněji",
                           "Pneumatiky z loňských a starších zásob za výprodejové ceny. U každé vidíte rok výroby (DOT) — nová, nejetá pneumatika do 3–5 let od výroby je v pořádku.",
                           f"<h2>Je pneumatika ze skladu horší?</h2><p>Pneumatika stárne pomalu, pokud leží v suchu, temnu a chladu. Výrobci považují nejetou pneumatiku do pěti let od výroby za novou. "
                           f"Rok výroby poznáte podle čtyřčíslí DOT na boku (2324 = 23. týden roku 2024) — {lnk('/stari-pneumatik/', 'jak číst DOT')}.</p>",
                           [("Vyplatí se koupit pneumatiku se starším DOT?", "Ano, pokud je do tří až pěti let od výroby a byla správně skladovaná. Sleva bývá i několik set korun na kus."),
                            ("Může obchod prodávat starší pneumatiky jako nové?", "Ano, nejetá pneumatika je nové zboží. Rok výroby by vám obchod měl na požádání sdělit.")]),
}
for _ss, _s, _gen, _when in (("zimni", "Zimní", "zimních", "Zimní pneumatiky se nejvíc prodávají v říjnu a listopadu; nejlevněji je seženete na jaře nebo v létě, kdy obchody doprodávají zásoby."),
                             ("letni", "Letní", "letních", "Letní pneumatiky jsou nejlevnější na podzim a v zimě, kdy obchody doprodávají sezónu — s nákupem do jara se často vyplatí počkat."),
                             ("celorocni", "Celoroční", "celoročních", "Celoroční pneumatiky prodávají obchody po celý rok, výprodeje starších ročníků ale bývají hlavně na konci sezóny.")):
    SPECIAL[f"vyprodej-pneumatik/{_ss}"] = (
        f"{_s} pneumatiky ve výprodeji", f"{_s} pneu výprodej – starší DOT levněji",
        f"{_s} pneumatiky z loňských a starších zásob za výprodejové ceny. U každé vidíte rok výroby (DOT) — nejetá pneumatika do 3–5 let od výroby je v pořádku.",
        f"<h2>Kdy kupovat {_gen} pneumatiky nejlevněji</h2><p>{_when} Rok výroby poznáte podle čtyřčíslí DOT na boku (2324 = 23. týden roku 2024) — "
        f"{lnk('/stari-pneumatik/', 'jak číst DOT')}.</p>",
        [(f"Vyplatí se {_s.lower()} pneumatika se starším DOT?",
          "Ano, pokud je do tří až pěti let od výroby a byla správně skladovaná. Sleva bývá i několik set korun na kus."),
         ("Jak poznám rok výroby pneumatiky?", "Podle posledních čtyř číslic kódu DOT na boku: první dvě jsou týden, druhé dvě rok výroby.")])



def special_items(products, slug):
    pas = [p for p in products if p["vehicle"] in PASSENGER]
    if slug == "run-flat-pneumatiky":
        return [p for p in pas if p["rf"]]
    if slug == "pneumatiky-pro-elektromobily":
        return [p for p in pas if p["ev"]]
    if slug == "offroad-pneumatiky":
        return [p for p in pas if re.search(r"\b(A/?T\d?|M/?T\d?|R/T|AT3W?|All[ -]?Terrain|Mud|KO2|KM3|Trail|Terra)\b", p["model"], re.I)]
    if slug.startswith("vyprodej-pneumatik"):
        season = {"zimni": "winter", "letni": "summer", "celorocni": "all"}.get(slug.partition("/")[2])
        return [p for p in products if p["offers"][0].get("dot") and p["offers"][0]["dot"] <= TODAY.year - 2 and (not season or p["season"] == season)]
    return []


def special_page(S, slug, items, brand_name, dim_pages):
    h1, title_kw, lead, guide, qa = SPECIAL[slug]
    path = f"/{slug}/"
    lo = min(p["price"] for p in items) if slug.startswith("vyprodej-pneumatik") else low(items)
    first = not getattr(S, "pg", None) or S.pg[1] == 1
    bcount = Counter(p["bslug"] for p in items)
    brand_opts = [(b, brand_name[b], n, f"/brandstore/{b}/") for b, n in bcount.most_common()]
    dim_opts = [(d, dim_lbl(d), n, f"/pneumatiky/{d}/") for d, n in Counter(p["dim"] for p in items).most_common(120) if d in dim_pages]
    faq_html, faq_ld = S.faq(qa)
    order = "price" if slug.startswith("vyprodej-pneumatik") else "pop"
    hubmode = len(items) > HUB_JSON_ITEMS
    seas = ""
    if slug.startswith("vyprodej-pneumatik"):
        seas = '<ul class="pills" style="margin:0 0 12px">' + "".join(
            f'<li><a href="/{u}/"{" class=on" if u == slug else ""}>{t}</a></li>' for u, t in (("vyprodej-pneumatik", "Vše"), ("vyprodej-pneumatik/zimni", "Zimní"),
                                                                                             ("vyprodej-pneumatik/letni", "Letní"), ("vyprodej-pneumatik/celorocni", "Celoroční"))
            if u == "vyprodej-pneumatik" or u in getattr(S, "specials", ())) + "</ul>"
    body = seas + S.listing(items, f"/data/h/{slug.replace('/', '-')}.json", order=order, dims=dim_opts, brands=brand_opts, lazy=hubmode, show_vehicle=True) + (
        f'<div class="prose hubtext">{guide}</div>{faq_html}' if first else "")
    intro = f"{lead} Srovnáváme {nf(len(items))} pneumatik od {len(bcount)} značek, nejlevněji od {R.kc(lo)}."
    return S.layout(path, f"{title_kw} od {R.kc(lo)} | auty.cz", f"{h1}: {nf(len(items))} pneumatik od {len(bcount)} značek, srovnání cen od {R.kc(lo)}. {lead[:80]}",
                    body, [("auty.cz", "/"), (h1, path)], band=S.band(h1, intro, "/img/ic/pneu.webp"), jsonld=[faq_ld] if faq_ld and first else None)


def home_page(S, products, dim_pages, by_brand, brand_name, articles):
    body = home_body(S, products, dim_pages, by_brand, brand_name, articles)
    return S.layout("/", "Srovnání cen pneumatik – letní, zimní i celoroční | auty.cz",
                    f"Srovnávač cen pneumatik: {nf(S.stats['products'])} pneumatik od {S.stats['brands']} značek ze {R.shops_gen(S.stats['shops'])}. Hledejte podle rozměru a vyberte nejlevnější obchod.",
                    body, jsonld=[{"@context": "https://schema.org", "@type": "WebSite", "name": "auty.cz", "alternateName": "auty.cz – srovnání cen pneumatik", "url": BASE + "/", "inLanguage": "cs-CZ",
                             "publisher": {"@id": BASE + "/#org"}},
                            {"@context": "https://schema.org", "@type": "Organization", "@id": BASE + "/#org", "name": "auty.cz", "url": BASE + "/",
                             "logo": BASE + "/img/ui/logo-192.webp", "description": "Nezávislý srovnávač cen pneumatik pro český trh.",
                             "founder": {"@type": "Person", "@id": "https://huy.cz/#kenny", "name": "Ha Duy Huy", "alternateName": "Kenny", "url": "https://huy.cz/", "jobTitle": "SEO konzultant"}}], full=True, preload_img="/img/ui/hero-640.webp")


CAR_NOTE = ("Rozměry jsou tovární podle výrobce pro běžné verze. Vaše auto může mít jiná kola — vždy si rozměr ověřte "
            "v technickém průkazu (osvědčení o registraci, část II, řádek pneumatiky), na štítku ve dveřích řidiče nebo na boku pneumatik, které máte teď.")


def load_cars(by_dim):
    raw = json.load(open(APP / "data/cars.json"))["cars"]
    cars, seen = [], set()
    for make, model, gen, years, sizes in raw:
        name = " ".join(x for x in (make, model, gen) if x)
        slug = T.slugify(name)
        if slug in seen:
            slug = T.slugify(f"{name} {years[:4]}")
        seen.add(slug)
        zs = []
        for z in sizes:
            rear = z.startswith("R:")
            m = re.match(r"(\d{3})/(\d{2}) R(\d{2}(?:\.5)?)(C?)$", z[2:] if rear else z)
            if not m:
                raise SystemExit(f"cars.json: bad size {z!r} ({name})")
            w, pr, r, c = m.groups()
            d = T.dim_slug(w, pr, r)
            ps = [p for p in by_dim.get(d, []) if bool(p["c"]) == bool(c)] or by_dim.get(d, [])
            fresh = lambda p: min((o["price"] for o in p["offers"] if not (o.get("dot") and o["dot"] < TODAY.year - 1)), default=0)
            low = {se: min((fresh(p) for p in ps if p["season"] == se and fresh(p)), default=0) for se in ("summer", "winter", "all")}
            zs.append({"label": f"{w}/{pr} R{r}{c}", "dim": d, "r": r, "rear": rear, "n": len(ps), "low": low})
        cars.append({"make": make, "model": model, "gen": gen, "years": years, "name": name, "slug": slug,
                     "url": f"/pneumatiky-podle-auta/{slug}/", "sizes": zs})
    return cars


def car_links(cs, limit=12):
    return ", ".join(f'<a href="{c["url"]}">{E(c["name"])}</a>' for c in cs[:limit])


def car_page(S, c, by_make, by_dim, dim_pages):
    rows = ""
    for z in c["sizes"]:
        link = f'/pneumatiky/{z["dim"]}/' if z["dim"] in dim_pages else ""
        cells = "".join(f'<td>{("od " + R.kc(z["low"][se])) if z["low"][se] else "–"}</td>' for se in ("summer", "winter", "all"))
        lbl = f'<b>{z["label"]}</b>' + (' <small class="muted">zadní náprava</small>' if z["rear"] else "")
        btn = f'<a class="btn btn--o" href="{link}">Porovnat ceny</a>' if link else '<span class="muted">zatím bez nabídek</span>'
        rows += f'<tr><th>{lbl}<small class="muted">{z["r"]}″ kola</small></th>{cells}<td>{btn}</td></tr>'
    table = (f'<div class="ctable"><table class="params"><thead><tr><th>Rozměr</th><th>Letní</th><th>Zimní</th><th>Celoroční</th><th></th></tr></thead>'
             f'<tbody>{rows}</tbody></table></div>')
    main = next((z for z in c["sizes"] if z["n"] and not z["rear"]), None)
    top = ""
    if main:
        ps = [p for p in by_dim.get(main["dim"], []) if p["page"] and p["vehicle"] in ("car", "suv", "van")]
        ps = sorted(ps, key=lambda p: (-len(p["offers"]), p["price"]))[:8]
        if ps:
            top = f'<h2>Nejsrovnávanější pneumatiky {main["label"]}</h2><ul class="pgrid-c">{"".join(S.prow(p) for p in ps)}</ul>'
    sizes_txt = ", ".join(z["label"] + (" (zadní)" if z["rear"] else "") for z in c["sizes"])
    base = c["sizes"][0]
    cheap = min((v for z in c["sizes"] if not z["rear"] for v in z["low"].values() if v), default=0)
    qa = [(f"Jaké pneumatiky má {c['name']}?", f"{c['name']} ({c['years']}) jezdí z výroby na rozměrech {sizes_txt}. Nejmenší kola mají rozměr {base['label']}. Který rozměr máte vy, zjistíte v technickém průkazu nebo na boku pneumatik."),
          (f"Kolik stojí sada pneumatik na {c['name']}?", (f"Nejlevnější pneumatika v továrních rozměrech stojí teď od {R.kc(cheap)} za kus, sada čtyř tedy od {R.kc(cheap * 4)} bez montáže. " if cheap else "")
           + "Cena hodně záleží na značce a rozměru — větší kola jsou dražší."),
          ("Můžu dát jiný rozměr, než je v technickém průkazu?", "Jen rozměr zapsaný v technickém průkazu (případně v dokladu o homologaci kol). Jiný rozměr musí mít podobný vnější průměr a musí ho schválit výrobce nebo zkušebna, jinak neprojdete STK a pojišťovna může krátit plnění."),
          ("Letní, zimní, nebo celoroční?", "Od 1. listopadu do 31. března musíte mít zimní pneumatiky (M+S, ideálně se symbolem vločky 3PMSF), pokud je na silnici sníh nebo led. Celoroční se vyplatí hlavně ve městě a při nájezdu do ~15 000 km ročně.")]
    wl = getattr(S, "car_wheels", {}).get(f'{c["make"]}|{c["model"]}|{c["gen"]}')
    wheels_html = ""
    if wl:
        pcd, cb = wl.split("|")
        ps = pcd.replace(".", "-")
        pl = pcd.replace("x", "×").replace(".", ",")
        wp = getattr(S, "wpages", set())
        rims = sorted({int(float(z["r"])) for z in c["sizes"]})
        links = [(f"/alu-kola/r{r}/{ps}/", f"Alu kola R{r} {pl}") for r in rims if f"/alu-kola/r{r}/{ps}/" in wp]
        if f"/alu-kola/{ps}/" in wp:
            links.append((f"/alu-kola/{ps}/", f"Všechna alu kola {pl}"))
        mp = f"/alu-kola-na-auto/{T.slugify(c['make'])}/"
        if mp in wp:
            links.append((mp, f"Alu kola na {c['make']}"))
        links.append(("/roztec-kol/", "Tabulka roztečí"))
        wheels_html = (f'<h2>Alu kola na {E(c["name"])}</h2><p>Kola pro {E(c["name"])} mají z výroby rozteč šroubů <b>{pl}</b> a středový otvor <b>{cb.replace(".", ",")} mm</b>, '
                       f'průměr ráfku R{rims[0]}' + (f"–R{rims[-1]}" if len(rims) > 1 else "") + '. Zális ET a šířku ráfku ověřte v technickém průkazu nebo na původních kolech.</p>'
                       + (f'<ul class="pills">{"".join(f"<li><a href={chr(34)}{u}{chr(34)}>{t}</a></li>" for u, t in links)}</ul>' if links else ""))
        qa.append((f"Jakou rozteč mají kola na {c['name']}?", f"{c['name']} má z výroby rozteč {pl} a středový otvor {cb.replace('.', ',')} mm. Ověřte si ji v technickém průkazu nebo na vnitřní straně původního kola."))
    faq_html, faq_ld = S.faq(qa)
    sib = [x for x in by_make[c["make"]] if x is not c]
    same = [x for x in sib if x["model"] == c["model"]]
    other = ""
    if same:
        other += f'<h2>Jiné generace {E(c["make"])} {E(c["model"])}</h2><ul class="pills">' + "".join(f'<li><a href="{x["url"]}">{E(x["name"])} <small>{x["years"]}</small></a></li>' for x in same) + "</ul>"
    rest = [x for x in sib if x["model"] != c["model"]]
    if rest:
        other += f'<h2>Další modely {E(c["make"])}</h2><ul class="pills">' + "".join(f'<li><a href="{x["url"]}">{E(x["model"])} {E(x["gen"])} <small>{x["years"]}</small></a></li>' for x in rest) + "</ul>"
    h1 = f"Pneumatiky na {c['name']}"
    body = (f'<h1>{E(h1)} <small class="muted" style="font-size:.5em">({c["years"]})</small></h1>'
            f'<p class="lead">Tovární rozměry pneumatik pro {E(c["name"])} a aktuální ceny v českých e-shopech. Vyberte rozměr, který máte v technickém průkazu, a porovnejte ceny.</p>'
            f'{table}<p class="note">⚠️ {CAR_NOTE}</p>{top}{wheels_html}{faq_html}{other}'
            f'<p style="margin-top:20px"><a class="lnk" href="/pneumatiky-podle-auta/">← Všechna auta</a></p>')
    title = f"Pneumatiky na {c['name']} ({c['years']}) – rozměry a ceny | auty.cz"
    desc = f"Jaké pneumatiky na {c['name']}? Tovární rozměry {', '.join(z['label'] for z in c['sizes'][:4])} a ceny letních, zimních i celoročních pneumatik od {R.kc(cheap)}." if cheap else \
        f"Tovární rozměry pneumatik pro {c['name']} ({c['years']}) a srovnání cen v českých e-shopech."
    crumbs = [("auty.cz", "/"), ("Podle auta", "/pneumatiky-podle-auta/"), (c["make"], f"/pneumatiky-podle-auta/{T.slugify(c['make'])}/"), (c["name"], c["url"])]
    return S.layout(c["url"], title, desc, body, crumbs, jsonld=[faq_ld])


def make_page(S, mk, cs):
    items = "".join(f'<li><a class="carlink" href="{c["url"]}"><b>{E(c["model"])} {E(c["gen"])}</b><small>{c["years"]} · {", ".join(z["label"] for z in c["sizes"] if not z["rear"])[:60]}</small></a></li>' for c in cs)
    body = (f'<h1>Pneumatiky na {E(mk)}</h1><p class="lead">Vyberte model a generaci — ukážeme tovární rozměry pneumatik a ceny v českých e-shopech.</p>'
            f'<ul class="cars">{items}</ul><p class="note">⚠️ {CAR_NOTE}</p><p style="margin-top:20px"><a class="lnk" href="/pneumatiky-podle-auta/">← Všechny značky aut</a></p>')
    return S.layout(f"/pneumatiky-podle-auta/{T.slugify(mk)}/", f"Pneumatiky na {mk} – rozměry podle modelu | auty.cz",
                    f"Jaké pneumatiky na {mk}? Tovární rozměry pro {', '.join(c['model'] for c in cs[:5])} a další modely, s cenami v českých e-shopech.",
                    body, [("auty.cz", "/"), ("Podle auta", "/pneumatiky-podle-auta/"), (mk, f"/pneumatiky-podle-auta/{T.slugify(mk)}/")])


def cars_index(S, by_make):
    makes = sorted(by_make, key=lambda m: (-len(by_make[m]), m))
    groups = "".join(f'<section class="make"><h2><a href="/pneumatiky-podle-auta/{T.slugify(m)}/">{E(m)}</a></h2><ul class="pills">'
                     + "".join(f'<li><a href="{c["url"]}" data-n="{E(c["name"])}">{E(c["model"])} {E(c["gen"])} <small>{c["years"]}</small></a></li>' for c in by_make[m])
                     + "</ul></section>" for m in makes)
    body = (f'<h1>Pneumatiky podle auta</h1><p class="lead">Nevíte, jaký rozměr pneumatik potřebujete? Najděte své auto a ukážeme tovární rozměry s aktuálními cenami.</p>'
            f'<input class="carsearch" type="search" placeholder="Napište auto, např. Octavia" aria-label="Hledat auto">'
            f'<div class="carlist">{groups}</div><p class="note">⚠️ {CAR_NOTE}</p>'
            f'<h2>Kde najdu rozměr pneumatik?</h2><ol class="howto"><li><b>Na boku pneumatiky</b> — např. <code>205/55 R16 91V</code>: šířka 205 mm, profil 55 %, ráfek 16 palců, index nosnosti 91, rychlosti V.</li>'
            f'<li><b>V technickém průkazu</b> — osvědčení o registraci, část II, řádek s pneumatikami a ráfky.</li><li><b>Na štítku ve dveřích řidiče</b> nebo na víčku nádrže (bývá tam i tlak).</li></ol>')
    return S.layout("/pneumatiky-podle-auta/", "Pneumatiky podle auta – zjistěte rozměr pro svůj vůz | auty.cz",
                    "Jaké pneumatiky na moje auto? Tovární rozměry pro Škoda, VW, Hyundai, Toyota, Dacia a další značky, s cenami v českých e-shopech.",
                    body, [("auty.cz", "/"), ("Podle auta", "/pneumatiky-podle-auta/")])


def wizard_page(S, by_make, dim_pages):
    opts = "".join(f'<optgroup label="{E(m)}">' + "".join(
        f'<option value="{c["slug"]}" data-s="{"|".join(z["dim"] + ":" + z["label"] for z in c["sizes"] if not z["rear"] and z["dim"] in dim_pages)}">{E(c["model"])} {E(c["gen"])} ({c["years"]})</option>'
        for c in cs) + "</optgroup>" for m, cs in sorted(by_make.items()))
    seas = [("letni", "letni", "Letní", "Od dubna do října. Nejkratší brzdná dráha v teple."), ("zimni", "zimni", "Zimní", "Od listopadu do března. Povinné na sněhu a ledu."),
            ("celorocni", "celorocni", "Celoroční", "Bez přezouvání. Hlavně do města a do 15 000 km ročně.")]
    tiers = [("b", "Co nejlevněji", "Levné značky. Na běžné ježdění po městě stačí."), ("m", "Střední třída", "Zavedené značky za rozumnou cenu. Nejčastější volba."),
             ("p", "Prémiové", "Michelin, Continental, Bridgestone… Nejlepší v testech."), ("", "Je mi to jedno", "Ukážeme všechno od nejsrovnávanějších.")]
    body = f"""<h1>Najděte své pneumatiky ve 3 krocích</h1><p class="lead">Nevyznáte se v pneumatikách? Nevadí. Odpovězte na tři otázky a ukážeme vám vhodné pneumatiky seřazené podle ceny.</p>
<form class="wiz" id="wiz" onsubmit="return false">
<fieldset class="wiz__s"><legend><span>1</span> Jaký rozměr potřebujete?</legend>
<div class="wiz__tabs"><label><input type="radio" name="how" value="size" checked> Znám rozměr</label><label><input type="radio" name="how" value="car"> Vyberu auto</label></div>
<div class="wiz__size"><div class="picker__f"><div><label for="ww">Šířka</label><select id="ww"><option>205</option></select></div><div><label for="wp">Profil</label><select id="wp"><option>55</option></select></div><div><label for="wr">Ráfek R</label><select id="wr"><option>16</option></select></div></div>
<p class="muted">Najdete ho na boku pneumatiky (např. <b>205/55 R16</b> 91V) nebo v technickém průkazu. <a href="/rozmery-pneumatik/">Jak zjistit rozměr</a></p></div>
<div class="wiz__car" hidden><label for="wc">Auto</label><select id="wc"><option value="">Vyberte auto…</option>{opts}</select><div id="wcs" class="wiz__opts"></div>
<p class="muted">Tovární rozměry — porovnejte s technickým průkazem. Auto chybí? Zadejte rozměr.</p></div></fieldset>
<fieldset class="wiz__s"><legend><span>2</span> Na jakou sezónu?</legend><div class="wiz__opts">{"".join(f'<label class="opt"><input type="radio" name="season" value="{v}"{" checked" if v == "zimni" else ""}><img src="/img/ic/{i}.webp" alt="" width="40" height="40"><b>{n}</b><small>{t}</small></label>' for v, i, n, t in seas)}</div></fieldset>
<fieldset class="wiz__s"><legend><span>3</span> Kolik chcete utratit?</legend><div class="wiz__opts">{"".join(f'<label class="opt"><input type="radio" name="tier" value="{v}"{" checked" if v == "m" else ""}><b>{n}</b><small>{t}</small></label>' for v, n, t in tiers)}</div></fieldset>
<button class="btn btn--o wiz__go" type="submit">Ukázat pneumatiky</button><p class="wiz__err" hidden>Tenhle rozměr zatím nemáme. Zkuste jiný.</p>
</form><noscript><p>Průvodce potřebuje JavaScript. <a href="/pneumatiky/">Vyberte rozměr ze seznamu</a>.</p></noscript>"""
    return S.layout("/najdi-pneumatiky/", "Najděte pneumatiky ve 3 krocích – průvodce pro začátečníky | auty.cz",
                    "Nevyznáte se v pneumatikách? Zadejte rozměr nebo auto, sezónu a rozpočet a ukážeme vhodné pneumatiky s nejlepšími cenami.",
                    body, [("auty.cz", "/"), ("Průvodce výběrem", "/najdi-pneumatiky/")])


def llms_txt(products, dim_pages, by_brand, brand_name, articles, shops, present):
    pop = sorted(dim_pages.items(), key=lambda kv: -len(kv[1]))[:40]
    bs = sorted(by_brand, key=lambda b: -len(by_brand[b]))[:30]
    shop_names = ", ".join(s["name"] for s in shops if s["id"] in present)
    L = [f"# auty.cz", "",
         f"> Nezávislý srovnávač cen pneumatik pro Českou republiku. Porovnává {nf(len(products))} pneumatik (letní, zimní, celoroční; osobní, SUV, dodávky, moto) "
         f"z {len(present)} českých e-shopů: {shop_names}. Ceny jsou za kus včetně DPH, aktualizují se každý den (naposledy {TODAY.isoformat()}).", "",
         "Každá stránka produktu ukazuje ceny stejné pneumatiky ve všech obchodech seřazené od nejlevnější, parametry (rozměr, index nosnosti a rychlosti, XL, run-flat, homologace) "
         "a štítek EU, pokud je známý. Odkazy do obchodů jsou affiliate; pořadí určuje jen cena.", "",
         "## Hlavní kategorie",
         f"- [Letní pneumatiky]({BASE}/letni-pneumatiky/)", f"- [Zimní pneumatiky]({BASE}/zimni-pneumatiky/)", f"- [Celoroční pneumatiky]({BASE}/celorocni-pneumatiky/)",
         f"- [Pneumatiky na SUV a 4x4]({BASE}/pneumatiky-suv/)", f"- [Pneumatiky na dodávky]({BASE}/pneumatiky-na-dodavky/)", f"- [Pneumatiky na motorku]({BASE}/pneumatiky-na-motorku/)",
         f"- [Všechny rozměry]({BASE}/pneumatiky/)", f"- [Všechny značky]({BASE}/brandstore/)", "",
         "## Nejhledanější rozměry"]
    L += [f"- [Pneumatiky {T.dim_label(ps[0]['w'], ps[0]['p'], ps[0]['r'])}]({BASE}/pneumatiky/{d}/): {len(ps)} pneumatik, od {R.kc(min(p['price'] for p in ps))}" for d, ps in pop]
    L += ["", "## Značky"] + [f"- [{brand_name[b]}]({BASE}/brandstore/{b}/): {len(by_brand[b])} pneumatik" for b in bs]
    L += ["", "## Rady a průvodci"] + [f"- [{a['title']}]({BASE}/{a['slug']}/)" for a in articles]
    L += ["", "## O webu", f"- [Jak srovnáváme ceny]({BASE}/o-webu/)", f"- [Mapa webu]({BASE}/sitemap_index.xml)", ""]
    return "\n".join(L)


def nf(n):
    return f"{n:,}".replace(",", "\u00a0")


def home_body(S, products, dim_pages, by_brand, brand_name, articles):
    pop = sorted(dim_pages.items(), key=lambda kv: -len(kv[1]))
    seas = Counter(p["season"] for p in products)
    veh = Counter(p["vehicle"] for p in products)
    minp = {s: min(p["price"] for p in products if p["season"] == s) for s in seas}
    top_dim = pop[0][0] if pop else ""
    top_lbl = T.dim_label(pop[0][1][0]["w"], pop[0][1][0]["p"], pop[0][1][0]["r"]) if pop else ""
    car_min = min((o["price"] for p in dim_pages.get(top_dim, []) if p["vehicle"] == "car" for o in p["offers"] if not o.get("dot")), default=0)
    # biggest savings: real comparisons only (3+ shops, no clearance DOT in the cheapest offer, sane spread)
    sav = [p for p in products if p["page"] and len(p["offers"]) >= 3 and not p["offers"][0].get("dot") and p["vehicle"] in ("car", "suv", "van")]
    sav = [p for p in sav if 8 <= S.saving(p) <= 35 and p["price"] <= 5000 and float(p["r"]) <= 19]
    sav = sorted(sav, key=lambda p: (-S.saving(p), p["price"]))
    seen_m, picked = set(), []
    for p in sav:                      # one size per tread pattern, so the row is not 12× the same tyre
        if p["mkey"] not in seen_m:
            seen_m.add(p["mkey"])
            picked.append(p)
    sav = picked[:10]
    often = "".join(f'<a href="/pneumatiky/{d}/">{T.dim_label(ps[0]["w"], ps[0]["p"], ps[0]["r"])}</a>' for d, ps in pop[:7])
    shops = "".join(S.shop_logo(i, big=True) for i in S.stats["shop_ids"])
    cats = [("/letni-pneumatiky/", "letni", "Letní", seas["summer"], ""), ("/zimni-pneumatiky/", "zimni", "Zimní", seas["winter"], ""),
            ("/celorocni-pneumatiky/", "celorocni", "Celoroční", seas["all"], ""), ("/osobni-pneumatiky/", "osobni", "Osobní auta", veh["car"], " c-o"),
            ("/pneumatiky-suv/", "suv", "SUV a 4x4", veh["suv"], " c-o"), ("/pneumatiky-na-dodavky/", "dodavky", "Dodávky", veh["van"], " c-o"),
            ("/pneumatiky-na-motorku/", "moto", "Motocykly", veh["moto"], " c-o"), ("/nakladni-pneumatiky/", "nakladni", "Nákladní", veh["truck"], " c-o")]
    cat_html = "".join(f'<li><a class="cat{c}" href="{u}"><img src="/img/ic/{i}.webp" alt="" width="62" height="62" loading="lazy"><b>{n}</b><small>{nf(k)} pneumatik</small></a></li>' for u, i, n, k, c in cats if k)
    scards = "".join(
        f'<li><a class="scard" href="/{slug}/"><img src="/img/ui/sezona-{img}.webp" alt="" width="120" height="130" loading="lazy"><span><b>{n}</b>'
        f'<p>{nf(seas[s])} pneumatik</p>{R.spz(minp.get(s, 0))}</span></a></li>'
        for s, slug, img, n in (("summer", "letni-pneumatiky", "letni", "Letní"), ("winter", "zimni-pneumatiky", "zimni", "Zimní"), ("all", "celorocni-pneumatiky", "celorocni", "Celoroční")))
    dsz = getattr(S, "dim_season", set())
    size_season = ""
    for d, ps in pop[:14]:
        lbl = T.dim_label(ps[0]["w"], ps[0]["p"], ps[0]["r"])
        links = []
        for se, nm in (("summer", "letní"), ("winter", "zimní"), ("all", "celoroční")):
            sub = [p for p in ps if p["season"] == se]
            if (d, se) in dsz and sub:
                links.append(f'<a href="/pneumatiky/{d}/{T.SEASON_SLUG[se]}/" title="{nm.capitalize()} pneu {lbl} od {R.kc(low(sub))}">{nm} <small>od {R.kc(low(sub))}</small></a>')
        if links:
            size_season += f'<div><b><a href="/pneumatiky/{d}/">{lbl}</a></b>{"".join(links)}</div>'
    bs = sorted(by_brand, key=lambda b: -len(by_brand[b]))[:16]
    brands = "".join(f'<a class="brandtile" href="/brandstore/{b}/"><i style="background:{R.SHOP_COLORS[i % len(R.SHOP_COLORS)]}">{E(brand_name[b][:2].upper())}</i><span>{E(brand_name[b])}<small>{nf(len(by_brand[b]))} pneumatik</small></span></a>' for i, b in enumerate(bs))
    return f"""<section class="hero"><div class="wrap"><div>
<h1>Pneumatiky: <span class="mark">porovnejte ceny</span> a&nbsp;ušetřete na sadě</h1>
<p class="lead">Stejnou pneumatiku hledáme ve všech obchodech najednou. Zadejte rozměr z boku pneumatiky nebo z technického průkazu a uvidíte, kdo ji teď prodává nejlevněji.</p>
{S.size_picker(title="Jaký rozměr hledáte?")}
<div class="often">Často hledané: {often}</div>
<p class="bycar"><img src="/img/ic/podle-auta.webp" alt="" width="28" height="28"> Nevyznáte se? <a href="/najdi-pneumatiky/">Průvodce ve 3 krocích</a> · <a href="/pneumatiky-podle-auta/">pneumatiky podle auta</a></p>
<ul class="stats"><li><b>{nf(S.stats['products'])}</b>pneumatik</li><li><b>{S.stats['brands']}</b>značek</li><li><b>{S.stats['shops']}</b>{R.plural(S.stats['shops'], 'obchod', 'obchody', 'obchodů')}</li></ul></div>
<div class="hero__art"><img src="/img/ui/hero-640.webp" srcset="/img/ui/hero-640.webp 640w, /img/ui/hero-1000.webp 1000w" sizes="(max-width:980px) 90vw, 560px" alt="Auto se sadou pneumatik na střeše" width="640" height="456" fetchpriority="high"><a href="/pneumatiky/{top_dim}/" title="Nejlevnější {top_lbl} pro osobní auta">{R.spz(car_min, top_lbl + " od")}</a></div>
</div></section>
<section class="shops"><div class="wrap"><span class="l">Porovnáváme ceny z</span>{shops}</div></section>
<div class="wrap">
<h2>Co hledáte?</h2><ul class="cats">{cat_html}</ul>
<h2>Největší úspora</h2><p class="muted" style="margin-top:-.4em">Pneumatiky, u kterých je mezi obchody největší rozdíl v ceně za kus.</p><ul class="pgrid-c">{"".join(S.prow(p) for p in sav)}</ul>
<h2>Oblíbené rozměry</h2><ul class="pills">{"".join(f'<li><a href="/pneumatiky/{d}/">{T.dim_label(ps[0]["w"], ps[0]["p"], ps[0]["r"])} <small>{len(ps)}</small></a></li>' for d, ps in pop[:30])}</ul>
<p style="margin-top:14px"><a class="lnk" href="/pneumatiky/">Všechny rozměry →</a></p>
<h2>Pneumatiky podle rozměru a sezóny</h2><p class="muted" style="margin-top:-.4em">Nejčastější rozměry rovnou v letní, zimní a celoroční verzi.</p><div class="sizeseas">{size_season}</div>
<h2>Podle sezóny</h2><ul class="scards">{scards}</ul>
<h2>Značky</h2><div class="brands">{brands}</div><p style="margin-top:14px"><a class="lnk" href="/brandstore/">Všech {len(by_brand)} značek →</a></p>
<h2>Disky, testy a rady</h2><ul class="pills"><li><a href="/alu-kola/">Alu kola</a></li><li><a href="/plechove-disky/">Plechové disky</a></li><li><a href="/roztec-kol/">Rozteč kol podle auta</a></li><li><a href="/levne-pneumatiky/">Levné pneumatiky</a></li><li><a href="/vyprodej-pneumatik/">Výprodej pneumatik</a></li><li><a href="/testy-pneumatik/">Testy pneumatik 2026</a></li><li><a href="/test-zimnich-pneumatik-2026/">Test zimních pneumatik</a></li><li><a href="/povinnost-zimnich-pneumatik/">Kdy mít zimní pneu</a></li><li><a href="/snehove-retezy/">Sněhové řetězy</a></li><li><a href="/prezuti-pneu-cena/">Cena přezutí</a></li><li><a href="/hloubka-dezenu/">Hloubka dezénu</a></li><li><a href="/eu-stitek-pneumatik/">Štítek EU</a></li></ul>
<h2>Jak srovnáváme</h2><ol class="steps"><li><img src="/img/ic/pneu.webp" alt="" width="64" height="64" loading="lazy"><div><h3>Stahujeme ceny</h3><p class="muted">Každý den obnovíme ceny a jednou týdně nabídky z českých e-shopů s pneumatikami.</p></div></li>
<li><img src="/img/ic/cena.webp" alt="" width="64" height="64" loading="lazy"><div><h3>Ověříme, že jde o stejný kus</h3><p class="muted">Porovnáváme jen opravdu stejnou pneumatiku — stejný vzorek, rozměr i indexy, ne jen podobný název.</p></div></li>
<li><img src="/img/ic/obchod.webp" alt="" width="64" height="64" loading="lazy"><div><h3>Vy vyberete obchod</h3><p class="muted">Kliknutím přejdete přímo do e-shopu. Cena je stejná, jako kdybyste tam šli sami.</p></div></li></ol>
<h2>Rady k pneumatikám</h2><ul class="arts">{"".join(article_card(a) for a in articles[:3])}</ul></div>"""


def article_card(a):
    img = f'<img src="{a["image"]}" alt="{E(a["title"])}" loading="lazy" width="640" height="427">' if a.get("image") else ""
    return f'<li><a class="card" href="/{a["slug"]}/">{img}<h3>{E(a["title"])}</h3><p class="muted">{E(a["excerpt"][:160])}…</p></a></li>'


MODEL_REF = re.compile(r"\{\{m:([^|}]+)\|([^}]+)\}\}")
BRAND_ALIAS = {"nokian": ["nokian-tyres", "nokian"], "gt-radial": ["gt-radial", "gtradial"], "bfgoodrich": ["bfgoodrich", "bf-goodrich"]}


def resolve_models(S, html):
    """{{m:Brand|Model}} in article HTML -> link to our model page with the current price, or plain text when we don't sell it."""
    links = getattr(S, "model_links", {})

    def one(m):
        brand, model = m.group(1).strip(), m.group(2).strip()
        name = f"{brand} {model}"
        key = re.sub(r"[^a-z0-9]", "", T.fold(model))
        for b in BRAND_ALIAS.get(T.slugify(brand), [T.slugify(brand)]):
            hit = links.get(f"{b}:{key}")
            if not hit:
                cands = [(v[2], k) for k, v in links.items() if k.startswith(f"{b}:{key}") and len(k) - len(b) - 1 - len(key) <= 6]
                hit = links[max(cands)[1]] if cands else None
            if hit:
                return f'<a href="{hit[0]}">{E(name)}</a> <small class="muted">od {R.kc(hit[1])}</small>'
        return E(name)
    return MODEL_REF.sub(one, html)



TESTS = [  # (season label, test, size, winner {{m:}}, grade, article) — copied from the test articles (sources there)
    ("Zimní", "ADAC 2026", "185/65 R15", "{{m:Continental|WinterContact TS 870}}", "2,2", "/test-zimnich-pneumatik-2026/"),
    ("Zimní", "ADAC 2026", "225/50 R17", "{{m:Pirelli|Cinturato Winter 3}}", "2,2", "/test-zimnich-pneumatik-2026/"),
    ("Celoroční", "ADAC 2026", "185/65 R15", "{{m:Continental|AllSeasonContact 2}}", "2,3", "/test-celorocnich-pneumatik-2026/"),
    ("Celoroční", "Auto Bild 2026", "225/45 R17", "{{m:Goodyear|Vector All Season 4}}", "1. místo", "/test-celorocnich-pneumatik-2026/"),
    ("Letní", "ADAC 2026", "225/50 R17", "{{m:Continental|PremiumContact 7}}", "1,9", "/test-letnich-pneumatik-2026/"),
    ("Letní", "Auto Bild 2026", "245/45 R19", "{{m:Hankook|Ventus evo}}", "1,1", "/test-letnich-pneumatik-2026/"),
]


def tests_page(S, articles):
    rows = "".join(f'<tr><th>{m}</th><td data-l="Sezóna">{s}</td><td data-l="Test">{t_}</td><td data-l="Rozměr">{z}</td><td data-l="Známka">{g}</td><td><a href="{u}">celý test</a></td></tr>'
                   for s, t_, z, m, g, u in TESTS)
    table = ('<div class="stbl"><table class="params"><thead><tr><th>Vítěz a naše cena</th><th>Sezóna</th><th>Test</th><th>Rozměr</th><th>Známka</th><th></th></tr></thead>'
             f'<tbody>{rows}</tbody></table></div>')
    arts = {a["slug"]: a for a in articles}
    cards = "".join(article_card(arts[s]) for s in ("test-zimnich-pneumatik-2026", "test-celorocnich-pneumatik-2026", "test-letnich-pneumatik-2026") if s in arts)
    qa = [("Který test pneumatik je nejdůvěryhodnější?", "Nejvíc se cituje společný test ADAC, ÖAMTC a TCS: pneumatiky kupuje anonymně v obchodech, měří brzdění na suchu, mokru, sněhu i ledu a "
           "zahrnuje i opotřebení. Auto Bild testuje víc pneumatik, ale v prvním kole vyřazuje polovinu podle brzdné dráhy."),
          ("Jak se čte známka ADAC?", "Jako ve škole: 1 je nejlepší, 5 nejhorší. Výsledná známka je podle nejslabší disciplíny — pneumatika, která dobře brzdí na suchu, ale selže na mokru, nemůže dostat dobrou známku."),
          ("Platí výsledek testu pro všechny rozměry?", "Ne úplně. Test se dělá v jednom nebo dvou rozměrech; v jiném rozměru se pneumatika chová podobně, ale ne stejně. Vítězové testů bývají silní napříč rozměry."),
          ("Proč jsou ceny v testu jiné než u vás?", "Testy uvádějí německé ceny v eurech. U každé pneumatiky ukazujeme aktuální nejnižší cenu v českých e-shopech.")]
    faq_html, faq_ld = S.faq(qa)
    body = (f'<p class="lead">Výsledky nejdůležitějších testů pneumatik 2026 na jednom místě: ADAC (společně s ÖAMTC a TCS) a Auto Bild pro zimní, celoroční i letní pneumatiky. '
            f'U každého vítěze vidíte, za kolik ho teď seženete v českých e-shopech.</p><h2>Vítězové testů pneumatik 2026</h2>{resolve_models(S, table)}'
            f'<h2>Podrobné výsledky testů</h2><div class="arts">{cards}</div>'
            '<div class="prose hubtext"><h2>Jak testy pneumatik číst</h2><p>Nejdůležitější je brzdná dráha na mokru a na suchu — tam jsou rozdíly mezi dobrou a slabou pneumatikou v metrech. '
            'U zimních pneumatik sledujte i sníh a led, u celoročních kompromis mezi zimou a létem. Levné pneumatiky občas vyniknou na sněhu, ale na mokru brzdí o několik metrů déle. '
            'Hodnocení „dobrá“ (známka do 2,5) znamená, že pneumatika nemá slabé místo.</p>'
            '<p>Testy mají jednu nevýhodu: zachytí jen pár rozměrů a pneumatiky, které jsou v Německu běžné. Proto u dalších pneumatik pomůže '
            '<a href="/eu-stitek-pneumatik/">štítek EU</a> — třída přilnavosti na mokru se měří podle jednotné normy.</p></div>' + faq_html)
    return S.layout("/testy-pneumatik/", "Testy pneumatik 2026 – výsledky ADAC a Auto Bild: zimní, letní i celoroční | auty.cz",
                    "Testy pneumatik 2026 přehledně: vítězové ADAC a Auto Bild pro zimní, celoroční a letní pneumatiky, známky a aktuální ceny v českých e-shopech.",
                    body, [("auty.cz", "/"), ("Rady a články", "/blog/"), ("Testy pneumatik", "/testy-pneumatik/")],
                    band=S.band("Testy pneumatik 2026", "Vítězové testů ADAC a Auto Bild a jejich aktuální ceny.", "/img/ic/pneu.webp"),
                    jsonld=[faq_ld] if faq_ld else None)

def article_page(S, a, articles):
    d = a["date"][:10]
    y, m, dd = d.split("-")
    img = f'<p class="hero-img"><img src="{a["image"]}" alt="{E(a["title"])}" width="1024" height="683" fetchpriority="high"></p>' if a.get("image") else ""
    others = [x for x in articles if x["slug"] != a["slug"]][:3]
    body = (f'<div class="artpage"><article class="prose"><h1>{E(a["title"])}</h1><p class="muted">{int(dd)}. {int(m)}. {y}</p>{img}{resolve_models(S, a["html"])}</article>'
            f'{S.size_picker(title="Hledáte pneumatiky? Zadejte rozměr")}<h2>Další články</h2><ul class="arts">{"".join(article_card(x) for x in others)}</ul></div>')
    ld = {"@context": "https://schema.org", "@type": "Article", "headline": a["title"], "datePublished": a["date"], "dateModified": a["modified"],
          "author": {"@type": "Organization", "name": "Redakce auty.cz", "url": BASE + "/o-webu/"},
          "publisher": {"@type": "Organization", "name": "auty.cz", "@id": BASE + "/#org"}, "mainEntityOfPage": BASE + f"/{a['slug']}/", "inLanguage": "cs-CZ"}
    if a.get("image"):
        ld["image"] = BASE + a["image"]
    return S.layout(f"/{a['slug']}/", f"{a['title']} | auty.cz", a["excerpt"][:155], body, [("auty.cz", "/"), ("Rady", "/blog/"), (a["title"], f"/{a['slug']}/")], jsonld=[ld])


def blog_page(S, articles):
    return S.layout("/blog/", "Rady k pneumatikám – přezouvání, skladování, homologace | auty.cz", "Praktické rady k pneumatikám: kdy přezout, jak je skladovat, co znamená homologace a run-flat.",
                    f'<h1>Rady k pneumatikám</h1><ul class="arts">{"".join(article_card(a) for a in articles)}</ul>', [("auty.cz", "/"), ("Rady", "/blog/")])


def about_page(S, shops, present, offers):
    cnt = Counter(o["shop"] for o in offers)
    rows = "".join(f"<li><b>{E(s['name'])}</b> — {nf(cnt[s['id']])} nabídek</li>" for s in shops if s["id"] in present)
    body = f"""<div class="prose"><h1>Jak srovnáváme ceny pneumatik</h1>
<p>auty.cz je nezávislý srovnávač cen pneumatik pro český trh. Neprodáváme, jen ukazujeme, kde je stejná pneumatika nejlevnější.</p>
<h2>Odkud bereme ceny</h2><p>Každý den stáhneme produktové feedy těchto obchodů:</p><ul>{rows}</ul>
<p>Porovnáváme jen stejnou pneumatiku: musí sedět značka, vzorek, rozměr, index nosnosti a rychlosti i značení (XL, run-flat, homologace).</p>
<h2>Jak na webu vyděláváme</h2><p>Odkazy do obchodů jsou affiliate: když nakoupíte, obchod nám zaplatí malou provizi. Na ceně pro vás se nic nemění a pořadí obchodů určuje jen cena — nejlevnější je vždy nahoře.</p>
<h2>Kdo web provozuje</h2><p>auty.cz provozuje Ha Duy Huy, IČO 07876823, Praha — viz <a href="/kontakt/">kontakt a provozovatel</a>. Nejsme e-shop: nic neprodáváme a nevybíráme platby.</p>
<h2>Co ověřit před nákupem</h2><ul><li>Rozměr, index nosnosti a rychlosti podle technického průkazu.</li><li>Cenu dopravy a montáže v obchodě (my ukazujeme cenu za kus s DPH).</li><li>Rok výroby (DOT), pokud je pro vás důležitý.</li></ul></div>"""
    return S.layout("/o-webu/", "Jak srovnáváme ceny pneumatik | auty.cz", "Odkud bereme ceny, jak párujeme pneumatiky mezi obchody a jak web vydělává.", body, [("auty.cz", "/"), ("Jak srovnáváme", "/o-webu/")])



OPERATOR = {"name": "Ha Duy Huy", "street": "Bartáková 36", "city": "140 00 Praha", "ico": "07876823", "ds": "dxmsrdt", "email": "info@auty.cz"}


def contact_page(S):
    o = OPERATOR
    body = f"""<div class="prose"><h1>Kontakt a provozovatel webu</h1>
<p>Web auty.cz provozuje fyzická osoba podnikající podle živnostenského zákona. Údaje o provozovateli jsou veřejně dostupné v registru ARES.</p>
<table class="opr"><tbody>
<tr><th>Provozovatel</th><td>{E(o["name"])}</td></tr>
<tr><th>Sídlo</th><td>{E(o["street"])}, {E(o["city"])}</td></tr>
<tr><th>IČO</th><td>{o["ico"]} (<a href="https://ares.gov.cz/ekonomicke-subjekty?ico={o["ico"]}" rel="nofollow noopener" target="_blank">ověřit v ARES</a>)</td></tr>
<tr><th>DPH</th><td>nejsem plátce DPH</td></tr>
<tr><th>Datová schránka</th><td>{o["ds"]}</td></tr>
<tr><th>E-mail</th><td><a href="mailto:{o["email"]}">{o["email"]}</a></td></tr>
</tbody></table>
<h2>S čím se na nás můžete obrátit</h2>
<ul><li>Chybná cena nebo chybný údaj u pneumatiky či disku — napište nám odkaz na stránku a my nabídku zkontrolujeme.</li>
<li>Žádost o odstranění nebo opravu údajů na webu.</li>
<li>Spolupráce a nabídky obchodů — viz níže.</li></ul>
<h2>Spolupráce</h2>
<p>Chcete s námi spolupracovat — obchod s pneumatikami nebo disky, partnerský program, reklama či obsah? Napište na <a href="mailto:{o["email"]}">{o["email"]}</a> nebo se podívejte na <a href="https://huy.cz/" rel="nofollow noopener" target="_blank">huy.cz</a>, kde najdete nabídku spolupráce.</p>
<h2>Čím se auty.cz zabývá a čím ne</h2>
<p>auty.cz je nezávislý srovnávač cen pneumatik a kol. <b>Nic neprodáváme</b>, nevybíráme platby a neuzavíráme s vámi kupní smlouvu — nákup probíhá v e-shopu, na který vás odkážeme. Reklamace, vrácení zboží a dopravu proto řešíte s prodejcem. Jak srovnáváme a jak web vydělává, popisuje stránka <a href="/o-webu/">Jak srovnáváme</a>; zacházení s údaji najdete v <a href="/ochrana-osobnich-udaju/">zásadách ochrany osobních údajů</a>.</p></div>"""
    return S.layout("/kontakt/", "Kontakt a provozovatel | auty.cz", "Provozovatel srovnávače auty.cz: Ha Duy Huy, IČO 07876823, Praha. Kontakt, datová schránka a údaje o fungování webu.",
                    body, [("auty.cz", "/"), ("Kontakt a provozovatel", "/kontakt/")])

def privacy_page(S):
    body = """<div class="prose"><h1>Ochrana osobních údajů a cookies</h1>
<h2>Správce údajů</h2><p>Správcem je provozovatel webu <b>Ha Duy Huy</b>, Bartáková 36, 140 00 Praha, IČO 07876823, e-mail <a href="mailto:info@auty.cz">info@auty.cz</a>. Další údaje najdete na stránce <a href="/kontakt/">Kontakt a provozovatel</a>.</p>
<p>Web auty.cz nepoužívá registrace ani formuláře a nesbírá jména, e-maily ani jiné kontaktní údaje.</p>
<h2>Měření návštěvnosti</h2><p>Jen když v liště souhlasíte, načteme Google Analytics 4 (Google Ireland Ltd.). Měříme, které stránky a obchody vás zajímají; IP adresu Google anonymizuje. Bez souhlasu se žádný měřicí skript nenačte. Souhlas můžete kdykoli změnit odkazem „Nastavení cookies“ v patičce.</p>
<h2>Odkazy do obchodů</h2><p>Po kliknutí na „Do obchodu“ vás affiliate síť (CJ, AffiliatePort, eHub nebo Dognet) přesměruje do e-shopu a uloží cookie, podle které obchod pozná, že jste přišli od nás. Zpracování v obchodě se řídí jeho podmínkami.</p>
<h2>Technické údaje</h2><p>Web běží na síti Cloudflare, která kvůli bezpečnosti a ochraně před roboty zpracovává IP adresu a technické údaje o požadavku.</p>
<h2>Vaše práva</h2><p>Máte právo na informace, přístup, opravu a výmaz údajů a právo podat stížnost u Úřadu pro ochranu osobních údajů (uoou.cz).</p></div>"""
    return S.layout("/ochrana-osobnich-udaju/", "Ochrana osobních údajů a cookies | auty.cz", "Jak auty.cz pracuje s cookies a osobními údaji.", body, [("auty.cz", "/"), ("Ochrana osobních údajů", "/ochrana-osobnich-udaju/")])


if __name__ == "__main__":
    main()
