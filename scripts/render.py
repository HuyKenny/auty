import re
"""HTML templates (plain f-strings, no dependencies). Design „Na silnici" — see static/assets/site.css."""
import html
import json
from collections import Counter

import tires as T

E = html.escape
SPECULATION = ('<script type="speculationrules">{"prerender":[{"where":{"and":[{"href_matches":"/*"},{"not":{"href_matches":"/data/*"}},'
               '{"not":{"href_matches":"/*\\\\?*"}},{"not":{"selector_matches":"[rel~=nofollow],[target=_blank],[download]"}}]},"eagerness":"moderate"}]}</script>')
SHOP_COLORS = ["#0B57B7", "#F7931E", "#1E7A4C", "#7A4BD1", "#D7262E", "#0E7490", "#B45309", "#334155", "#BE123C"]
SEASON_IMG = {"summer": "letni", "winter": "zimni", "all": "celorocni"}
SEASON_ICON = {s: f'<img src="/img/ic/{n}.webp" alt="" width="18" height="18" loading="lazy">' for s, n in SEASON_IMG.items()}
VEHICLE_IMG = {"car": "osobni", "suv": "suv", "van": "dodavky", "moto": "moto", "truck": "nakladni"}


def kc(n):
    return f"{int(round(n)):,}".replace(",", " ") + " Kč"


def plural_shops(n):
    return f"{n} obchod" if n == 1 else f"{n} obchody" if n < 5 else f"{n} obchodů"


def shops_gen(n):
    """Genitive after „ze/z“: z 1 obchodu, ze 2 obchodů, ze 5 obchodů."""
    return f"{n} obchodu" if n == 1 else f"{n} obchodů"


def plural(n, one, few, many):
    return one if n == 1 else few if 1 < n < 5 else many



def img_url(u, size="card"):
    """Smaller variants from the shops' own image CDNs (no resizing on our side — that would cost Worker CPU).
    market.tasy.cz serves 700 KB PNGs; Kentico's ?maxsidesize cuts them ~10x. jfnet has fixed NxN renditions.
    http:// -> https:// (browsers upgrade mixed-content images anyway; dobrepneu answers http with a 302)."""
    if not u:
        return u
    if u.startswith("http://"):
        u = "https://" + u[7:]
    if "market.tasy.cz/getmedia/" in u and "?" not in u:
        return u + ("?maxsidesize=240" if size == "card" else "?maxsidesize=480")
    if "jfnet.de/profile/" in u and size == "card":
        return u.replace("/500x500-cc-ffffff.", "/300x300-cc-ffffff.")
    return u

def spz(price, prefix="od", xl=False):
    pre = f"<small>{prefix}</small>" if prefix else ""
    return f'<span class="spz{" spz--xl" if xl else ""}"><i>CZ</i><b>{pre}{kc(price)}</b></span>'


SEARCH_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>'
LOGO = '<img src="/img/ui/logo-96.webp" alt="" width="46" height="46"><b>Auty<i>.cz</i></b>'
PER_PAGE = 24

# Brand classes for beginners (by brand slug). Premium = the big test winners, mid = established brands of the big groups / Asian majors.
TIER_P = {"michelin", "continental", "bridgestone", "goodyear", "pirelli", "dunlop", "nokian", "nokian-tyres", "vredestein"}
TIER_M = {"hankook", "kumho", "nexen", "toyo", "yokohama", "falken", "kleber", "bfgoodrich", "uniroyal", "barum", "semperit", "fulda", "firestone",
          "sava", "debica", "matador", "gt-radial", "laufenn", "general", "general-tire", "cooper", "maxxis", "giti", "apollo", "esa-tecar", "metzeler", "mitas", "heidenau"}
TIER_CZ = {"p": "prémiová", "m": "střední třída", "b": "levná"}


def tier(bslug):
    return "p" if bslug in TIER_P else "m" if bslug in TIER_M else "b"


def pager_html(path, cur, pages):
    """1 2 3 … pagination with real links (page 1 = the listing URL, then /strana-N/)."""
    if pages <= 1:
        return ""
    url = lambda k: path if k == 1 else f"{path}strana-{k}/"
    show = sorted({1, pages, cur - 2, cur - 1, cur, cur + 1, cur + 2} & set(range(1, pages + 1)))
    out, last = [], 0
    for k in show:
        if k - last > 1:
            out.append('<span class="gap">…</span>')
        out.append(f'<a aria-current="page" class="on">{k}</a>' if k == cur else f'<a href="{url(k)}">{k}</a>')
        last = k
    prev = f'<a class="pp" href="{url(cur - 1)}" rel="prev" aria-label="Předchozí strana">‹</a>' if cur > 1 else ""
    nxt = f'<a class="pp" href="{url(cur + 1)}" rel="next" aria-label="Další strana">›</a>' if cur < pages else ""
    return f'<nav class="pager" id="pager" aria-label="Stránkování">{prev}{"".join(out)}{nxt}</nav>'


CAT_NAV = [("/letni-pneumatiky/", "letni", "Letní"), ("/zimni-pneumatiky/", "zimni", "Zimní"), ("/celorocni-pneumatiky/", "celorocni", "Celoroční"),
           ("/osobni-pneumatiky/", "osobni", "Osobní"), ("/pneumatiky-suv/", "suv", "SUV a 4x4"), ("/pneumatiky-na-dodavky/", "dodavky", "Dodávky"),
           ("/pneumatiky-na-motorku/", "moto", "Moto"), ("/pneumatiky-podle-auta/", "podle-auta", "Podle auta"), ("/levne-pneumatiky/", "cena", "Levné"),
           ("/vyprodej-pneumatik/", "obchod", "Výprodej"), ("/alu-kola/", "disk", "Alu kola"), ("/brandstore/", "pneu", "Značky")]


class Site:
    def __init__(self, base, env, ga, css, js, shops, updated, stats):
        self.base, self.env, self.ga, self.css, self.js = base, env, ga, css, js
        self.shops = shops  # id -> {"name","color"}
        self.updated, self.stats = updated, stats

    def url(self, path):
        return self.base + path

    def band(self, title, text, img=None):
        art = f'<img class="band__art" src="{img}" alt="" width="150" height="150">' if img else ""
        return f'<section class="band"><div class="wrap"><div><h1>{title}</h1><p>{text}</p></div>{art}</div></section>'

    def layout(self, path, title, desc, body, crumbs=None, noindex=False, jsonld=None, preload_img=None, band=None, full=False):
        robots = "noindex,follow" if (noindex or self.env != "production") else "index,follow,max-image-preview:large"
        pg = getattr(self, "pg", None)
        if pg and pg[1] > 1:
            path, n = f"{pg[0]}strana-{pg[1]}/", pg[1]
            title = title.replace(" | auty.cz", f" – strana {n} | auty.cz") if " | auty.cz" in title else f"{title} – strana {n}"
            desc = f"Strana {n}: {desc}"
            jsonld = [x for x in (jsonld or []) if x and x.get("@type") != "FAQPage"]
            body = re.sub(r'<section class="faq">.*?</section>', "", body, flags=re.S)
            body = re.sub(r"</h1>", f' <small class="pgn">– strana {n}</small></h1>', body, count=1)
            if band:
                band = re.sub(r"</h1>", f' <small class="pgn">– strana {n}</small></h1>', band, count=1)
        jsonld = list(jsonld or [])
        if getattr(self, "pending_ld", None):
            jsonld.append(self.pending_ld)
            self.pending_ld = None
        ld = "".join(f'<script type="application/ld+json">{json.dumps(x, ensure_ascii=False, separators=(",", ":"))}</script>' for x in jsonld)
        cr = ""
        if crumbs:
            ld += '<script type="application/ld+json">' + json.dumps({
                "@context": "https://schema.org", "@type": "BreadcrumbList",
                "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": n, "item": self.url(u)} for i, (n, u) in enumerate(crumbs)]},
                ensure_ascii=False, separators=(",", ":")) + "</script>"
            cr = '<nav class="crumbs" aria-label="Drobečková navigace">' + " › ".join(
                f'<a href="{u}">{E(n)}</a>' if i < len(crumbs) - 1 else E(n) for i, (n, u) in enumerate(crumbs)) + "</nav>"
        pre = f'<link rel="preload" as="image" href="{E(preload_img)}" fetchpriority="high">' if preload_img else ""
        ga = f'<meta name="ga4" content="{self.ga}">' if self.ga and self.env == "production" else ""
        if full:
            content = body
        elif band:
            content = band.replace('<div class="wrap"><div>', f'<div class="wrap"><div>{cr}', 1) + f'<div class="wrap">{body}</div>'
        else:
            content = f'<div class="wrap page">{cr}{body}</div>'
        nav = "".join(f'<li><a href="{u}"><img src="/img/ic/{i}.webp" alt="" width="34" height="34">{n}</a></li>' for u, i, n in CAT_NAV)
        shops_n = self.stats["shops"]
        return f"""<!doctype html><html lang="cs"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(title)}</title><meta name="description" content="{E(desc)}"><link rel="canonical" href="{self.url(path)}"><meta name="robots" content="{robots}">
<meta property="og:title" content="{E(title)}"><meta property="og:description" content="{E(desc)}"><meta property="og:url" content="{self.url(path)}"><meta property="og:type" content="website"><meta property="og:locale" content="cs_CZ"><meta property="og:image" content="{self.base}/img/ui/og.png">
<meta name="theme-color" content="#0B57B7"><link rel="icon" href="/favicon.ico" sizes="48x48"><link rel="icon" href="/favicon-192.png" type="image/png" sizes="192x192"><link rel="apple-touch-icon" href="/favicon-180.png">
<link rel="preload" href="/fonts/figtree-1.woff2" as="font" type="font/woff2" crossorigin><link rel="preload" href="/fonts/barlow-c-700.woff2" as="font" type="font/woff2" crossorigin><link rel="stylesheet" href="/assets/{self.css}">{pre}{SPECULATION}{ga}{ld}</head><body>
<div class="usp"><ul><li>Ceny pneumatik {"z" if shops_n == 1 else "ze"} {shops_gen(shops_n)} na jednom místě</li><li>Vždy stejná pneumatika, jen jiná cena</li><li>Kupujete přímo v obchodě, za stejnou cenu</li><li>Bez registrace a bez reklam</li></ul></div>
<header class="top"><div class="wrap"><a class="logo" href="/" aria-label="auty.cz – úvod">{LOGO}</a>
<form class="search" role="search" onsubmit="return false"><label class="sr" for="q">Hledat rozměr, značku nebo vzorek</label>{SEARCH_ICON}<input id="q" type="search" placeholder="Rozměr, značka nebo vzorek" autocomplete="off"><button type="submit" id="qgo">Hledat</button><div class="sugg" id="sugg"></div></form>
<nav class="hl" aria-label="Rychlé odkazy"><a href="/pneumatiky/"><img src="/img/ic/disk.webp" alt="" width="26" height="26">Rozměry</a><a href="/o-webu/"><img src="/img/ic/obchod.webp" alt="" width="26" height="26">Obchody</a></nav></div></header>
<nav class="cn" aria-label="Kategorie"><ul>{nav}</ul></nav>
<main>{content}</main>
<footer><div class="wrap cols"><div><a class="logo" href="/">{LOGO}</a><p>Srovnání cen pneumatik v českých e-shopech. Ceny aktualizujeme každý den. Za nákup přes odkazy můžeme dostat provizi — cenu pro vás nemění.</p></div>
<div><h3>Sezóny</h3><ul><li><a href="/letni-pneumatiky/">Letní pneumatiky</a></li><li><a href="/zimni-pneumatiky/">Zimní pneumatiky</a></li><li><a href="/celorocni-pneumatiky/">Celoroční pneumatiky</a></li><li><a href="/levne-pneumatiky/">Levné pneumatiky</a></li><li><a href="/vyprodej-pneumatik/">Výprodej pneumatik</a></li><li><a href="/run-flat-pneumatiky/">Run-flat pneumatiky</a></li><li><a href="/pneumatiky-pro-elektromobily/">Pro elektromobily</a></li><li><a href="/pneumatiky/">Všechny rozměry</a></li><li><a href="/pneumatiky-podle-auta/">Pneumatiky podle auta</a></li></ul></div>
<div><h3>Průvodce</h3><ul><li><a href="/rozmery-pneumatik/">Jak zjistit rozměr</a></li><li><a href="/znaceni-pneumatik/">Značení pneumatik</a></li><li><a href="/rychlostni-index-pneumatik/">Rychlostní index</a></li><li><a href="/index-nosnosti-pneumatik/">Index nosnosti</a></li><li><a href="/povinnost-zimnich-pneumatik/">Povinnost zimních pneumatik</a></li><li><a href="/stari-pneumatik/">Stáří pneumatik (DOT)</a></li><li><a href="/testy-pneumatik/">Testy pneumatik 2026</a></li><li><a href="/eu-stitek-pneumatik/">Štítek EU</a></li></ul></div>
<div><h3>Vozidla</h3><ul><li><a href="/pneumatiky-suv/">SUV a 4x4</a></li><li><a href="/pneumatiky-na-dodavky/">Dodávky</a></li><li><a href="/pneumatiky-na-motorku/">Motocykly</a></li><li><a href="/nakladni-pneumatiky/">Nákladní</a></li><li><a href="/alu-kola/">Alu kola</a></li><li><a href="/plechove-disky/">Plechové disky</a></li><li><a href="/roztec-kol/">Rozteč kol podle auta</a></li></ul></div>
<div><h3>Web</h3><ul><li><a href="/o-webu/">Jak srovnáváme</a></li><li><a href="/blog/">Rady a články</a></li><li><a href="/brandstore/">Značky</a></li><li><a href="/kontakt/">Kontakt a provozovatel</a></li><li><a href="/ochrana-osobnich-udaju/">Ochrana osobních údajů</a></li><li><a href="#" id="consent-open">Nastavení cookies</a></li></ul></div></div>
<div class="wrap legal"><p>Provozovatel: Ha Duy Huy, Bartáková 36, 140 00 Praha · IČO 07876823 · nejsem plátce DPH · <a href="/kontakt/">kontakt</a></p></div></footer>
<div class="consent" id="consent" role="dialog" aria-label="Cookies"><p>Smíme měřit návštěvnost (Google Analytics)? Bez souhlasu nic neměříme.</p>
<div class="row"><button class="btn" data-consent="yes">Souhlasím</button><button class="btn btn--g" data-consent="no">Nesouhlasím</button><a href="/ochrana-osobnich-udaju/" class="lnk">Více</a></div></div>
<script src="/assets/{self.js}" defer></script></body></html>"""

    # ------------------------------------------------------------ pieces

    def name(self, p):
        x = f"{p['brand']} {p['model']} {T.dim_label(p['w'], p['p'], p['r'])}{'C' if p['c'] else ''} {p['li']}{p['si']}"
        if p["xl"]:
            x += " XL"
        if p["rf"]:
            x += " RunFlat"
        if p["hom"]:
            x += " " + " ".join(p["hom"])
        return x.replace(" ?", "").strip()

    def eu_mini(self, eu):
        if not eu:
            return ""
        return (f'<span class="eu" title="Štítek EU: valivý odpor {eu["rr"]}, přilnavost za mokra {eu["wg"]}, hluk {eu["db"]} dB">'
                f'<span class="eu-{eu["rr"]}">{eu["rr"]}</span><span class="eu-{eu["wg"]}">{eu["wg"]}</span><span>{eu["db"]} dB</span></span>')

    def chips(self, p):
        tip = {"summer": "Letní pneumatika: nejlepší nad 7 °C.", "winter": "Zimní pneumatika: splňuje povinnost od 1. 11. do 31. 3.",
               "all": "Celoroční pneumatika: nemusíte přezouvat; zimní povinnost splňuje s označením M+S."}[p["season"]]
        c = f'<span class="chip s-{p["season"]}" data-tip="{tip}">{SEASON_ICON[p["season"]].replace("18", "14")} {T.SEASON_CZ[p["season"]]}</span>'
        if p["li"]:
            li0 = p["li"].split("/")[0]
            kg = T.LI_KG.get(int(li0)) if li0.isdigit() else None
            t = (f"Index nosnosti {p['li']}" + (f" = {kg} kg na pneumatiku" if kg else "") +
                 (f", rychlostní index {p['si']} = do {T.SI_KMH[p['si']]} km/h" if p["si"] in T.SI_KMH else "") + ". V technickém průkazu nesmí být vyšší.")
            c += f'<span class="chip" data-tip="{t}">{p["li"]}{p["si"]}</span>'
        if p["xl"]:
            c += '<span class="chip" data-tip="XL = zesílená pneumatika s vyšší nosností. Nutná jen když ji uvádí technický průkaz.">XL</span>'
        if p["rf"]:
            c += '<span class="chip" data-tip="Run-flat: s defektem dojedete omezenou rychlostí do servisu. Jen pro auta s kontrolou tlaku.">run-flat</span>'
        if p["vehicle"] != "car":
            c += f'<span class="chip">{ {"suv": "SUV", "van": "dodávka", "moto": "moto", "truck": "nákladní"}[p["vehicle"]] }</span>'
        return c + self.eu_mini(p["eu"])

    def shop_logo(self, sid, big=False):
        m = self.shops.get(sid, {"name": sid, "color": "#999"})
        cls = "slogo slogo--b" if big else "slogo"
        if m.get("logo"):
            bg = f' style="background:{m["logo_bg"]}"' if m.get("logo_bg") else ""
            return f'<span class="{cls}"{bg} title="{E(m["name"])}"><img src="{m["logo"]}" alt="{E(m["name"])}" loading="lazy" decoding="async"></span>'
        two = re.sub(r"(?<=[a-zá-ž])(?=[A-Z])", "<br>", E(m["name"]), count=1)   # "NejlevnějšíPneu.cz" -> two lines
        return f'<span class="{cls} slogo--t" style="color:{m["color"]}" title="{E(m["name"])}">{two}</span>'

    def go_attrs(self, shop):
        return f'rel="sponsored nofollow noopener" target="_blank" data-go="{E(shop)}"'

    @staticmethod
    def saving(p):
        if len(p["offers"]) < 2:
            return 0
        lo, hi = p["offers"][0]["price"], p["offers"][-1]["price"]
        return round((hi - lo) / hi * 100) if hi else 0

    def prow(self, p):
        """Product card (the name is kept for build.py)."""
        img = E(img_url(p["img"]) or "/assets/tire.svg")
        n = len(p["offers"])
        sv = self.saving(p)
        badge = f'<span class="pc__save" title="Rozdíl mezi nejlevnějším a nejdražším obchodem">−{sv} %</span>' if sv >= 5 else ""
        if p.get("page"):
            href = f'/produkt/{p["slug"]}/'
            title = f'<a href="{href}">{E(self.name(p))}</a>'
            cta = f'<a class="btn btn--o" href="{href}">Porovnat ceny</a>'
            pic = f'<a class="pc__img" href="{href}" tabindex="-1"><img src="{img}" alt="" loading="lazy" width="180" height="150"></a>'
        else:
            o = p["offers"][0]
            title = E(self.name(p))
            cta = f'<a class="btn" href="{E(o["go"])}" {self.go_attrs(o["shop"])}>Do obchodu</a>'
            pic = f'<span class="pc__img"><img src="{img}" alt="" loading="lazy" width="180" height="150"></span>'
        where = f"v {n} obchodech" if n > 1 else "v 1 obchodě"
        return (f'<li class="pc">{pic}{badge}<div class="pc__b"><h3>{title}</h3><div class="chips">{self.chips(p)}</div>'
                f'<div>{spz(p["price"], "od" if n > 1 else "")}</div><p class="pc__n">{where}</p>{cta}</div></li>')

    def item_json(self, p, brand_idx):
        eu = f'{p["eu"]["rr"]}{p["eu"]["wg"]}{p["eu"]["db"]}' if p["eu"] else ""
        o = p["offers"][0]
        return [self.name(p), f'/produkt/{p["slug"]}/' if p.get("page") else "", img_url(p["img"]), p["price"], len(p["offers"]),
                ["summer", "winter", "all"].index(p["season"]), brand_idx, p["li"], p["si"], 1 if p["xl"] else 0, 1 if p["rf"] else 0,
                p["vehicle"], eu, self.saving(p), p["dim"]] + (["", ""] if p.get("page") else [o["go"], o["shop"]]) + \
               [1 if p.get("pmsf") else 0, 1 if p.get("ev") else 0]

    @staticmethod
    def link_filter(title, key, opts, search=None):
        """Filter whose options are real links (crawlable, work without JS); the script turns a click into a toggle.
        opts: [(value, label, count, href)]"""
        if not opts:
            return ""
        srch = f'<input class="fsearch" type="search" placeholder="{search}" aria-label="{search}">' if search and len(opts) > 12 else ""
        links = "".join(f'<a href="{href}" data-f="{key}" data-v="{E(str(v))}" data-n="{E(lbl)}"><i></i>{E(lbl)}<span class="count">{n}</span></a>' for v, lbl, n, href in opts)
        return f'<fieldset><legend>{title}</legend>{srch}<div class="flinks">{links}</div></fieldset>'

    def filters(self, items, show_vehicle=True, show_brand=True, dims=None, brands=None, fixed_season=None):
        seasons = {s: sum(1 for p in items if p["season"] == s) for s in ("summer", "winter", "all")}
        veh = {v: sum(1 for p in items if p["vehicle"] == v) for v in ("car", "suv", "van", "moto", "truck")}
        h = '<form class="card filters" id="filters">' + (f'<input type="hidden" name="season" value="{fixed_season}">' if fixed_season else "")
        h += self.link_filter("Rozměr", "dim", dims, "Hledat rozměr, např. 205/55")
        h += self.link_filter("Značka", "brand", brands, "Hledat značku")
        if sum(1 for n in seasons.values() if n) > 1:
            h += '<fieldset><legend>Sezóna</legend>' + "".join(
                f'<label><input type="checkbox" name="season" value="{s}"> {SEASON_ICON[s]} {T.SEASON_CZ[s]}<span class="count" data-c="season:{s}">{n}</span></label>'
                for s, n in seasons.items() if n) + "</fieldset>"
        rims = Counter(p["r"] for p in items if re.fullmatch(r"\d+(\.5)?", str(p["r"])))
        if len(rims) > 1:
            h += ('<fieldset><legend>Ráfek</legend><div class="frims">' + "".join(
                f'<label><input type="checkbox" name="rim" value="{r}"> R{r}<span class="count" data-c="rim:{r}">{n}</span></label>'
                for r, n in sorted(rims.items(), key=lambda x: float(x[0])) if n >= 2) + "</div></fieldset>")
        h += ('<fieldset><legend>Cena za kus</legend><div class="fprice"><input type="number" name="pmin" min="0" step="100" placeholder="od" aria-label="Cena od (Kč)">'
              '<span>–</span><input type="number" name="pmax" min="0" step="100" placeholder="do" aria-label="Cena do (Kč)"><span>Kč</span></div></fieldset>')
        if show_brand or brands:
            h += ('<fieldset><legend>Třída</legend>' + "".join(f'<label><input type="checkbox" name="tier" value="{k}"> {n}</label>'
                  for k, n in (("p", "Prémiové značky"), ("m", "Střední třída"), ("b", "Levné značky"))) + "</fieldset>")
        h += '<fieldset><legend>Vlastnosti</legend><label><input type="checkbox" name="multi" value="1"> Víc obchodů (srovnání)</label>'
        h += '<label><input type="checkbox" name="xl" value="1"> XL (zesílená)</label><label><input type="checkbox" name="rf" value="1"> Run-flat</label>'
        if any(p.get("pmsf") for p in items) and (seasons["winter"] or seasons["all"]):
            h += '<label><input type="checkbox" name="pmsf" value="1"> Vločka 3PMSF</label>'
        if sum(1 for p in items if p.get("ev")) >= 3:
            h += '<label><input type="checkbox" name="ev" value="1"> Pro elektromobily</label>'
        h += '</fieldset>'
        if sum(1 for p in items if p["eu"]) >= max(10, len(items) // 4):     # shops rarely send the EU label (~1 % of tyres, 11/2026)
            sel = lambda name, label, opts: (f'<label class="fsel"><span>{label}</span><select name="{name}"><option value="">jakákoli</option>'
                                             + "".join(f'<option value="{v}">{t}</option>' for v, t in opts) + "</select></label>")
            h += ('<fieldset><legend>Štítek EU</legend>' + sel("wg", "Přilnavost za mokra", [("A", "A"), ("B", "B a lepší"), ("C", "C a lepší")])
                  + sel("rr", "Valivý odpor (spotřeba)", [("A", "A"), ("B", "B a lepší"), ("C", "C a lepší")])
                  + sel("db", "Hluk nejvýš", [("68", "68 dB"), ("70", "70 dB"), ("72", "72 dB")]) + "</fieldset>")
        lis = sorted({int(p["li"]) for p in items if str(p.get("li") or "").isdigit()})
        if len(lis) > 3:
            step = [x for x in (75, 80, 84, 88, 91, 94, 97, 100, 103, 106, 110, 115, 120) if lis[0] < x <= lis[-1]]
            if step:
                h += ('<fieldset><legend>Index nosnosti aspoň</legend><select name="li" aria-label="Index nosnosti aspoň"><option value="">jakýkoli</option>'
                      + "".join(f'<option value="{x}">{x} — {T.LI_KG[x]} kg</option>' if T.LI_KG.get(x) else f'<option value="{x}">{x}</option>' for x in step)
                      + "</select></fieldset>")
        if show_vehicle and sum(1 for n in veh.values() if n) > 1:
            h += '<fieldset><legend>Vozidlo</legend>' + "".join(
                f'<label><input type="checkbox" name="vehicle" value="{v}"> <img src="/img/ic/{VEHICLE_IMG[v]}.webp" alt="" width="20" height="20"> {T.VEHICLE_CZ[v]}<span class="count" data-c="vehicle:{v}">{n}</span></label>'
                for v, n in veh.items() if n) + "</fieldset>"
        h += ('<fieldset><legend>Rychlost aspoň</legend><select name="si"><option value="">jakákoli</option>'
              + "".join(f'<option value="{s}">{s} — {T.SI_KMH[s]} km/h</option>' for s in "QRSTHVWY") + "</select></fieldset>")
        if show_brand and not brands:
            h += '<fieldset><legend>Značka</legend><div id="fbrands"><span class="muted">Načítám…</span></div></fieldset>'
        return (h + '<button class="btn btn--g freset" type="button" hidden>Zrušit filtry</button>'
                '<div class="fbar"><button class="btn fclose" type="button">Zobrazit výsledky</button></div></form>')

    def listing(self, items, json_url, ssr=24, lazy=False, hub=None, order="pop", **kw):
        items = sorted(items, key=(lambda p: p["price"]) if order == "price" else (lambda p: (-len(p["offers"]), p["price"])))
        path, cur, pages = getattr(self, "pg", None) or ("", 1, 1)
        chunk = items[(cur - 1) * PER_PAGE:cur * PER_PAGE]
        rows = "".join(self.prow(p) for p in chunk)
        top = [p for p in chunk if p.get("page")]
        self.pending_ld = {"@context": "https://schema.org", "@type": "ItemList", "numberOfItems": len(items),
                           "itemListElement": [{"@type": "ListItem", "position": i + 1, "url": self.url(f'/produkt/{p["slug"]}/'), "name": self.name(p)}
                                               for i, p in enumerate(top, (cur - 1) * PER_PAGE)]} if top else None
        more = pager_html(path, cur, pages) if path else ""
        hub_attr = f" data-hub='{json.dumps(hub)}'" if hub else ""
        note = '<p class="muted" id="hubnote" hidden style="width:100%;margin:0;font-size:.88rem">Ukazujeme nejsrovnávanější pneumatiky. Vyberte rozměr nebo značku a uvidíte všechny.</p>' if hub else ""
        return (f'<div class="layout">{self.filters(items, **kw)}<div><div class="listhead"><p class="muted" style="margin:0"><b id="count">{len(items)}</b> pneumatik</p>'
                f'<button class="btn btn--g ftoggle" type="button">Filtry</button><label>Řadit <select id="sort"><option value="pop">nejvíc obchodů</option>'
                f'<option value="price"{" selected" if order == "price" else ""}>nejlevnější</option><option value="pricedesc">nejdražší</option><option value="save">největší úspora</option></select></label>'
                f'{note}</div><ul class="pgrid-c" id="plist" data-page="{cur}" data-json="{json_url}"{" data-lazy=1" if lazy else ""}{hub_attr}>{rows}</ul>{more}</div></div>')

    def size_picker(self, w="205", p="55", r="16", title="Najděte pneumatiky podle rozměru"):
        seas = "".join(f'<label><input type="radio" name="season" value="{v}"{" checked" if not v else ""}>{f"<img src=/img/ic/{v}.webp alt width=22 height=22>" if v else ""}{n}</label>'
                       for v, n in (("", "všechny"), ("letni", "letní"), ("zimni", "zimní"), ("celorocni", "celoroční")))
        return f"""<form class="picker" id="picker" data-w="{w}" data-p="{p}" data-r="{r}"><h2>{title}</h2>
<div class="picker__f"><div><label for="pw">Šířka</label><select id="pw"><option>{w}</option></select></div><div><label for="pp">Profil</label><select id="pp"><option>{p}</option></select></div>
<div><label for="pr">Ráfek R</label><select id="pr"><option>{r}</option></select></div><button class="btn btn--o" type="submit">Ukázat pneumatiky</button></div>
<div class="seasons">{seas}</div></form>"""

    def faq(self, qa):
        if not qa:
            return "", None
        h = '<section class="faq"><h2>Časté otázky</h2>' + "".join(f"<details><summary>{E(q)}</summary><p>{a}</p></details>" for q, a in qa) + "</section>"
        ld = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in qa]}
        return h, ld


SIDEWALL = ""  # replaced by the Canva hero illustration (static/img/ui/hero-*.webp)
