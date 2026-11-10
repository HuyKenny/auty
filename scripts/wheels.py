"""Alloy and steel wheels: feed parsing and listing pages.

Two feeds carry wheels (checked 2026-11-08): DobrePneu ("Disky > Alu kola" / "Disky > Plechová kola") and Alukola-obchod
(an eHub shop that sells only wheels). Shops overlap little, so the value is a filterable catalogue: every card links
straight to the shop and there are no per-wheel pages (they would eat the 95k-file budget).
Pages: /alu-kola/, /alu-kola/r17/, /alu-kola/5x112/, /alu-kola/r17/5x112/, /alu-kola/<brand>/, /plechove-disky/ (+ /r16/).
"""
import gzip
import json
import re
from collections import Counter, defaultdict
from html import escape as E
from pathlib import Path

import render as R
import tires as T

RAW = Path(__file__).resolve().parent.parent / "data/raw"
COLOR_WORDS = re.compile(r"\b(black|silver|dark|bronze|shine|titan|titanium|graphite|gun ?metal|gunmetal|grey|gray|white|gold|anthracite|hyper|polished|"
                         r"matt?|glossy|gloss|front|lip|face|machined|black ?polished|černá|stříbrná|antracit|lesk|mat)\b", re.I)
ACCESSORY = re.compile(r"\b(šroub|matice|krouž|kroužky|čidl|ventil|poklic|kryt|centrovac|podložk|rozšiřovac|klíč|sada|tpms)", re.I)
PCD_OK = {98, 100, 105, 108, 110, 112, 114.3, 115, 118, 120, 127, 130, 139.7, 150, 160, 165.1, 170, 180, 205}


def num(s):
    return float(s.replace(",", ".").replace("?", ".5").replace(" ", "")) if s else None


def fmt(x):
    return (f"{x:g}").replace(".", ",") if x is not None else ""


def parse(rec):
    t = re.sub(r"\s+", " ", rec.get("t") or "").strip()
    cat = rec.get("cat") or ""
    if ACCESSORY.search(t):
        return None
    steel = "Plechov" in cat or t.lower().startswith("plechov")
    if not steel and "Alu kola" not in cat:
        return None
    w = r = pcd = et = cb = None
    holes = None
    m = re.search(r"(\d{1,2}(?:[.,?]\d)?)\s*\??J?\s*[x×]\s*(\d{1,2}(?:[.,]\d)?)\b", t)       # 8x20 / 18x8 / 6?J x 16
    if m:
        a, b = num(m.group(1)), num(m.group(2))
        w, r = (b, a) if (13 <= a <= 24 and a == int(a) and b <= 13) and not (13 <= b <= 24 and b == int(b) and a <= 13) else (a, b)
        if not (13 <= r <= 24 and 3.5 <= w <= 13):
            w, r = (a, b) if 13 <= b <= 24 else (b, a)
    mp = re.search(r"\b([3-8])\s*[x×]\s*(\d{2,3}(?:[.,]\d)?)\b", t[m.end():] if m else t)
    if mp:
        holes, pcd = int(mp.group(1)), num(mp.group(2))
    me = re.search(r"\bET\s*(-?\d{1,3}(?:[.,]\d)?)", t, re.I)
    if me:
        et = num(me.group(1))
        mc = re.search(r"ET\s*-?\d{1,3}(?:,\d)?\s*,\s*(\d{2,3}(?:[.,]\d)?)\s*$", t, re.I)
        cb = num(mc.group(1)) if mc else None
    if m and (pcd is None or et is None):                                        # "Glasgow 8.5 20 112 35 70,1" / "7J x 17 108 46"
        tail = re.findall(r"-?\d{1,3}(?:[.,]\d)?", t[m.end():])
        vals = [num(x) for x in tail]
        if pcd is None and vals and vals[0] in PCD_OK:
            pcd = vals.pop(0)
        if et is None and vals and -60 <= vals[0] <= 80:
            et = vals.pop(0)
        if cb is None and vals and 50 <= vals[0] <= 135:
            cb = vals.pop(0)
    if not m:
        m2 = re.search(r"\b(\d{1,2}(?:[.,]\d)?)\s+(1[3-9]|2[0-4])\s+(\d{3}(?:[.,]\d)?)\s+(-?\d{1,2})(?:\s+(\d{2,3}(?:[.,]\d)?))?\s*$", t)
        if not m2:
            return None
        w, r, pcd, et, cb = num(m2.group(1)), num(m2.group(2)), num(m2.group(3)), num(m2.group(4)), num(m2.group(5)) if m2.group(5) else None
    if not (r and 13 <= r <= 24 and w and 3.5 <= w <= 13):
        return None
    if pcd is not None and pcd not in PCD_OK:
        pcd = None
    brand = (rec.get("b") or t.split()[0]).strip()
    if steel and brand.lower().startswith("plechov"):
        brand = t.split()[2] if len(t.split()) > 2 else "?"
    name = t[:m.start()].strip(" ,") if m else t
    if steel:
        name = re.sub(r"(?i)^plechový disk\s*", "", name)
    model = re.sub(r"(?i)^" + re.escape(brand) + r"\s*", "", name).strip() or name
    design = COLOR_WORDS.sub(" ", model)
    design = re.sub(r"\s+", " ", design).strip() or model
    price = round(float(rec["p"]))
    if price < 400:
        return None
    return {"id": rec["id"], "brand": brand.title() if brand.isupper() and len(brand) > 4 else brand, "model": model, "design": design, "steel": steel,
            "w": w, "r": int(r), "holes": holes, "pcd": pcd, "et": et, "cb": cb, "ean": T.norm_ean(rec.get("ean")),
            "img": rec.get("img") or "", "price": price, "go": rec["go"], "url": rec["url"]}


def pcd_slug(x):
    return f"{x['holes']}x{fmt(x['pcd']).replace(',', '-')}" if x["holes"] and x["pcd"] else None


def pcd_label(x):
    return f"{x['holes']}×{fmt(x['pcd'])}" if x["holes"] and x["pcd"] else (f"{fmt(x['pcd'])} mm" if x["pcd"] else "")


def load(shops):
    """-> list of wheels, each {..., offers:[{shop, price, go}]}, merged across shops by EAN."""
    items, by_ean = [], {}
    for s in shops:
        if s.get("kind") not in ("wheels", "both"):
            continue
        p = RAW / f"{s['id']}.jsonl.gz"
        if not p.exists():
            continue
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for line in f:
                x = parse(json.loads(line))
                if not x:
                    continue
                off = {"shop": s["id"], "price": x["price"], "go": x["go"]}
                if x["ean"] and x["ean"] in by_ean:
                    y = by_ean[x["ean"]]
                    if all(o["shop"] != s["id"] for o in y["offers"]):
                        y["offers"].append(off)
                    continue
                x["offers"] = [off]
                items.append(x)
                if x["ean"]:
                    by_ean[x["ean"]] = x
    for x in items:
        x["offers"].sort(key=lambda o: o["price"])
        x["price"] = x["offers"][0]["price"]
        x["bslug"] = T.slugify(x["brand"])
        x["ps"] = pcd_slug(x)
    return items


def plan(items):
    """Listing pages: path -> (kind, key, items). Combos only where there is enough choice."""
    alu = [x for x in items if not x["steel"]]
    steel = [x for x in items if x["steel"]]
    pages = {"/alu-kola/": ("hub", None, alu)}
    if len(steel) >= 12:
        pages["/plechove-disky/"] = ("steel", None, steel)
    by_r, by_p, by_rp, by_b, by_sr = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for x in alu:
        by_r[x["r"]].append(x)
        by_b[x["bslug"]].append(x)
        if x["ps"]:
            by_p[x["ps"]].append(x)
            by_rp[(x["r"], x["ps"])].append(x)
    for x in steel:
        by_sr[x["r"]].append(x)
    for r, v in by_r.items():
        if len(v) >= 12:
            pages[f"/alu-kola/r{r}/"] = ("rim", r, v)
    for ps, v in by_p.items():
        if len(v) >= 12:
            pages[f"/alu-kola/{ps}/"] = ("pcd", ps, v)
    for (r, ps), v in by_rp.items():
        if len(v) >= 12 and f"/alu-kola/r{r}/" in pages and f"/alu-kola/{ps}/" in pages:
            pages[f"/alu-kola/r{r}/{ps}/"] = ("rimpcd", (r, ps), v)
    for b, v in by_b.items():
        if len(v) >= 12 and not re.fullmatch(r"r\d{2}|\d+x[\d-]+", b):
            pages[f"/alu-kola/{b}/"] = ("brand", b, v)
    for r, v in by_sr.items():
        if len(v) >= 12:
            pages[f"/plechove-disky/r{r}/"] = ("steelrim", r, v)
    return pages


def card(S, x):
    o = x["offers"][0]
    n = len(x["offers"])
    spec = ", ".join(s for s in (f"{fmt(x['w'])}J×{x['r']}", pcd_label(x), f"ET {fmt(x['et'])}" if x["et"] is not None else "",
                                 f"střed {fmt(x['cb'])} mm" if x["cb"] else "") if s)
    img = E(R.img_url(x["img"]) or "/img/ic/disk.webp")
    return (f'<li class="pc wc"><span class="pc__img"><img src="{img}" alt="" loading="lazy" width="180" height="150"></span><div class="pc__b">'
            f'<h3>{E(x["brand"])} {E(x["model"])} {x["r"]}"</h3><p class="wspec">{spec}</p>'
            f'<div>{R.spz(x["price"], "od" if n > 1 else "")}</div><p class="pc__n">{S.shop_logo(o["shop"])}{" v " + str(n) + " obchodech" if n > 1 else ""}</p>'
            f'<a class="btn" href="{E(o["go"])}" {S.go_attrs(o["shop"])}>Do obchodu</a></div></li>')




def car_models(cars_raw, car_wheels):
    """[(make, model, gen, years, name, slug, ps, pcd_label, cb, rims)] for cars with known bolt pattern."""
    out, seen = [], set()
    for make, model, gen, years, sizes in cars_raw:
        name = " ".join(x for x in (make, model, gen) if x)
        slug = T.slugify(name)
        if slug in seen:
            slug = T.slugify(f"{name} {years[:4]}")
        seen.add(slug)
        wl = car_wheels.get(f"{make}|{model}|{gen}")
        if not wl:
            continue
        pcd, cb = wl.split("|")
        rims = sorted({int(float(re.search(r"R(\d{2}(?:\.5)?)", z).group(1))) for z in sizes if re.search(r"R(\d{2})", z)})
        out.append({"make": make, "model": model, "gen": gen, "years": years, "name": name, "url": f"/pneumatiky-podle-auta/{slug}/",
                    "ps": pcd.replace(".", "-"), "pl": pcd.replace("x", "×").replace(".", ","), "cb": float(cb), "rims": rims})
    return out


def car_plan(wheels, models):
    """/alu-kola-na-auto/<make>/ for makes with enough fitting alloy wheels."""
    alu = [x for x in wheels if not x["steel"] and x["ps"]]
    by_make = defaultdict(list)
    for m in models:
        by_make[m["make"]].append(m)
    pages = {}
    for make, ms in by_make.items():
        fit = [x for x in alu if any(x["ps"] == m["ps"] and x["r"] in m["rims"] and (x["cb"] is None or x["cb"] >= m["cb"] - 0.05) for m in ms)]
        if len(fit) >= 12:
            pages[f"/alu-kola-na-auto/{T.slugify(make)}/"] = ("car", (make, ms), fit)
    return pages


def car_table(ms, wpages):
    rows = ""
    for m in ms:
        wl = f'/alu-kola/{m["ps"]}/'
        rr = f'R{m["rims"][0]}' + (f'–R{m["rims"][-1]}' if len(m["rims"]) > 1 else "")
        pcd = f'<a href="{wl}">{m["pl"]}</a>' if wl in wpages else m["pl"]
        rows += (f'<tr><th><a href="{m["url"]}">{E(m["name"])}</a> <small class="muted">{m["years"]}</small></th><td data-l="Rozteč">{pcd}</td>'
                 f'<td data-l="Střed">{fmt(m["cb"])} mm</td><td data-l="Kola">{rr}</td></tr>')
    return ('<div class="stbl"><table class="params"><thead><tr><th>Model</th><th>Rozteč</th><th>Středový otvor</th><th>Průměr kol</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')


def json_key(path):
    return path.strip("/").replace("/", "_")


def json_data(S, items):
    """Compact rows for the client-side facets: [title, spec, img, price, offers, go, shop, w, r, pcd slug, et, cb]."""
    items = sorted(items, key=lambda x: x["price"])
    shops = {o["shop"] for x in items for o in x["offers"][:1]}
    rows = []
    for x in items:
        o = x["offers"][0]
        spec = ", ".join(v for v in (f"{fmt(x['w'])}J×{x['r']}", pcd_label(x), f"ET {fmt(x['et'])}" if x["et"] is not None else "",
                                     f"střed {fmt(x['cb'])} mm" if x["cb"] else "") if v)
        rows.append([f'{x["brand"]} {x["model"]} {x["r"]}"', spec, R.img_url(x["img"]) or "", x["price"], len(x["offers"]), o["go"], o["shop"],
                     x["w"], x["r"], x["ps"] or "", x["et"], x["cb"]])
    return json.dumps({"shops": {sh: S.shop_logo(sh) for sh in shops}, "items": rows}, ensure_ascii=False, separators=(",", ":"))


def facets(items, alu):
    """Client-side facets (need the listing JSON): width, offset range, centre bore, price, sort."""
    ws = Counter(x["w"] for x in items if x["w"])
    cbs = sorted({x["cb"] for x in items if x["cb"]})
    h = ""
    if len(ws) > 1:
        h += ('<fieldset><legend>Šířka ráfku</legend><div class="frims">' + "".join(
            f'<label><input type="checkbox" name="w" value="{fmt(w)}"> {fmt(w)}J<span class="count">{n}</span></label>'
            for w, n in sorted(ws.items()) if n >= 2) + "</div></fieldset>")
    if any(x["et"] is not None for x in items):
        h += ('<fieldset><legend>Zális ET</legend><div class="fprice"><input type="number" name="etmin" step="1" placeholder="od" aria-label="ET od">'
              '<span>–</span><input type="number" name="etmax" step="1" placeholder="do" aria-label="ET do"><span>mm</span></div></fieldset>')
    std = [c for c in (54.1, 56.1, 56.6, 57.1, 58.1, 60.1, 63.3, 63.4, 64.1, 65.1, 66.1, 66.5, 66.6, 67.1, 70.1, 71.6, 72.6, 74.1) if cbs and cbs[0] <= c <= cbs[-1]]
    if len(cbs) > 2 and std:
        h += ('<fieldset><legend>Středový otvor</legend><select name="cb" aria-label="Středový otvor aspoň"><option value="">jakýkoli</option>'
              + "".join(f'<option value="{c}">aspoň {fmt(c)} mm (náboj {fmt(c)})</option>' for c in std) + "</select>"
              '<p class="fhint">Otvor kola musí být stejný nebo větší než náboj auta; rozdíl dorovnají centrovací kroužky.</p></fieldset>')
    h += ('<fieldset><legend>Cena za kus</legend><div class="fprice"><input type="number" name="pmin" min="0" step="100" placeholder="od" aria-label="Cena od (Kč)">'
          '<span>–</span><input type="number" name="pmax" min="0" step="100" placeholder="do" aria-label="Cena do (Kč)"><span>Kč</span></div></fieldset>')
    return h


def wpills(title, opts, cur, search=None):
    """Sidebar filter group: real links (crawlable), the current page is ticked."""
    if len(opts) < 2:
        return ""
    srch = f'<input class="fsearch wsearch" type="search" placeholder="{search}" aria-label="{search}">' if search and len(opts) > 12 else ""
    return (f'<fieldset><legend>{title}</legend>{srch}<div class="flinks">' + "".join(
        f'<a href="{u}"{" class=on" if u == cur else ""}><i></i>{t}<span class="count">{n}</span></a>' for u, t, n in opts) + "</div></fieldset>")


HUB_GUIDE = """<h2>Jak vybrat alu kola</h2>
<p>Alu kolo musí na auto sedět ve čtyřech parametrech. Najdete je v technickém průkazu, v COC listu nebo na vnitřní straně původního kola.</p>
<ul><li><b>Průměr a šířka</b> (např. 7J×17): průměr v palcích musí odpovídat pneumatikám (R17), šířka v palcích je rozměr ráfku.</li>
<li><b>Rozteč šroubů</b> (např. 5×112): počet šroubů × průměr kružnice, na které leží, v milimetrech. Musí sedět přesně.</li>
<li><b>Zális ET</b> (např. ET 45): o kolik milimetrů je dosedací plocha posunutá od středu ráfku. Menší ET vysune kolo ven z blatníku, větší ho zasune dovnitř — odchylka do pár milimetrů bývá v pořádku, víc už se musí schvalovat.</li>
<li><b>Středový otvor</b> (např. 57,1 mm): musí být stejný jako náboj auta, nebo větší a dorovnaný centrovacími kroužky.</li></ul>
<p>Disky, které nejsou uvedené v technickém průkazu, potřebují homologaci (TÜV/KBA nebo ECE) a případně zápis na STK. Kola od výrobce vozu nebo s homologací pro váš typ auta lze montovat bez zápisu.</p>
<h2>Alu kola, nebo plechové disky?</h2>
<p>Alu kola jsou lehčí, lépe chladí brzdy a vypadají lépe; v zimě je ale musíte víc hlídat proti soli. <a href="/plechove-disky/">Plechové disky</a> jsou levné a odolné — proto je řada řidičů používá na zimní sadu.</p>"""


def page(S, path, kind, key, items, pages, shops_n):
    items = sorted(items, key=lambda x: x["price"])
    alu = kind not in ("steel", "steelrim")
    path_cur, cur, n_pages = getattr(S, "pg", None) or (path, 1, 1)
    chunk = items[(cur - 1) * R.PER_PAGE:cur * R.PER_PAGE]
    lo = items[0]["price"]
    r = key if kind in ("rim", "steelrim") else key[0] if kind == "rimpcd" else None
    ps = key if kind == "pcd" else key[1] if kind == "rimpcd" else None
    x0 = next((x for x in items if x["ps"] == ps), None) if ps else None
    pl = pcd_label(x0) if x0 else ""
    brand = items[0]["brand"] if kind == "brand" else ""
    base = "/alu-kola/" if alu else "/plechove-disky/"
    noun = "Alu kola" if alu else "Plechové disky"
    if kind == "hub":
        h1 = "Alu kola"
        lead = ("Alu kola (alu disky, hliníkové disky, litá kola) na osobní auta, SUV i dodávky. Vyberte průměr a rozteč šroubů podle technického průkazu, "
                "nebo rovnou disky na auto podle značky, a porovnejte ceny.")
    elif kind == "steel":
        h1 = "Plechové disky"
        lead = "Plechové disky jsou levné a odolné — ideální na zimní sadu. Vyberte průměr, rozteč a zális podle technického průkazu."
    elif kind in ("rim", "steelrim"):
        h1 = f"{noun} R{r}"
        # "alu disky 17" / "plechové disky 16" are the common searches
        lead = f"{noun} {r}\" (průměr {r} palců) pro pneumatiky R{r}. Vyberte rozteč šroubů a zális ET, které má vaše auto v technickém průkazu."
    elif kind == "pcd":
        h1 = f"Alu kola {pl}"
        lead = f"Alu kola s roztečí {pl}: {x0['holes']} šroubů na kružnici o průměru {fmt(x0['pcd'])} mm. Zkontrolujte ještě zális ET a středový otvor."
    elif kind == "rimpcd":
        h1 = f"Alu kola {pl} R{r}"
        lead = f"Alu kola {r}\" s roztečí {pl}. Před nákupem ověřte zális ET a středový otvor podle technického průkazu nebo původních kol."
    elif kind == "car":
        make, ms = key
        pls = [pl for pl, _ in Counter(m["pl"] for m in ms).most_common()]
        h1 = f"Alu kola na auta {E(make)}"
        lead = (f"Alu kola, která sedí na vozy {E(make)}: rozteč {', '.join(pls[:3])}, středový otvor stejný nebo větší než náboj a průměr, "
                f"který má model z výroby. Zális ET a šířku ráfku si ověřte v technickém průkazu.")
    else:
        h1 = f"Alu kola {E(brand)}"
        lead = f"Alu kola značky {E(brand)} v rozměrech R{min(x['r'] for x in items)}–R{max(x['r'] for x in items)}."
    rims = Counter(x["r"] for x in items)
    pcds = Counter(x["ps"] for x in items if x["ps"])
    first = cur == 1
    filt = wpills("Typ", [("/alu-kola/", "Alu kola", S.wheel_n.get("alu", 0)), ("/plechove-disky/", "Plechové disky", S.wheel_n.get("steel", 0))],
                  base)
    if True:
        if kind in ("hub", "pcd", "brand", "steel"):
            filt += wpills("Průměr", [(f"/alu-kola/r{rr}/{ps + '/' if ps and f'/alu-kola/r{rr}/{ps}/' in pages else ''}" if alu else f"/plechove-disky/r{rr}/", f"R{rr}", n)
                                      for rr, n in sorted(rims.items()) if (f"/alu-kola/r{rr}/" if alu else f"/plechove-disky/r{rr}/") in pages], path)
        if alu and kind in ("hub", "rim", "brand"):
            filt += wpills("Rozteč", [(f"/alu-kola/r{r}/{p}/" if r and f"/alu-kola/r{r}/{p}/" in pages else f"/alu-kola/{p}/",
                                       pcd_label(next(x for x in items if x["ps"] == p)), n) for p, n in pcds.most_common(16) if f"/alu-kola/{p}/" in pages], path)
        if kind in ("hub", "pcd", "rim"):
            cars = [(u, E(v[1][0]), len(v[2])) for u, v in pages.items() if v[0] == "car"] if isinstance(pages, dict) else []
            filt += wpills("Podle auta", sorted(cars, key=lambda c: -c[2]) + [("/roztec-kol/", "Tabulka roztečí", "")], path)
        if kind == "car":
            filt += wpills("Rozteč", [(f"/alu-kola/{p}/", pcd_label(next(x for x in items if x["ps"] == p)), n) for p, n in pcds.most_common(8) if f"/alu-kola/{p}/" in pages], path)
            filt += wpills("Model", [(m["url"], E(f'{m["model"]} {m["gen"]}'.strip()), m["pl"]) for m in key[1]], path)
        if kind == "hub":
            brands = Counter(x["bslug"] for x in items)
            filt += wpills("Značka", [(f"/alu-kola/{b}/", E(next(x["brand"] for x in items if x["bslug"] == b)), n) for b, n in brands.most_common(60) if f"/alu-kola/{b}/" in pages], path, "Hledat značku")
        if kind == "rimpcd":
            filt += (f'<fieldset><legend>Rozšířit výběr</legend><div class="flinks"><a href="/alu-kola/r{r}/"><i></i>Všechna R{r}</a>'
                     f'<a href="/alu-kola/{ps}/"><i></i>Všechna {pl}</a></div></fieldset>')
    grid = (f'<ul class="pgrid-c" id="wlist" data-json="/data/w/{json_key(path)}.json" data-page="{cur}">' + "".join(card(S, x) for x in chunk) + "</ul>"
            + (R.pager_html(path_cur, cur, n_pages) if n_pages > 1 else ""))
    head = (f'<div class="listhead"><p class="muted" style="margin:0"><b id="wcount">{len(items)}</b> {"kol" if alu else "disků"} · ceny za kus s DPH</p>'
            f'<label>Řadit <select id="wsort"><option value="price">od nejlevnějšího</option><option value="pricedesc">od nejdražšího</option>'
            f'<option value="et">podle zálisu ET</option><option value="w">podle šířky</option></select></label></div>')
    qa = [(f"Kolik stojí {h1.lower() if kind != 'brand' else h1}?", f"Nejlevnější {noun.lower()} {('R' + str(r) + ' ') if r else ''}teď stojí {R.kc(lo)} za kus, "
                                                                    f"polovina nabídek je do {R.kc(items[len(items) // 2]['price'])}."),
          ("Jak zjistím, jaká kola sedí na moje auto?", "Rozměr ráfku, rozteč, zális ET a středový otvor najdete v technickém průkazu nebo COC listu, případně na vnitřní straně původního kola. "
                                                        "U rozměrů mimo technický průkaz je potřeba homologace kola.")]
    if kind == "car":
        make, ms = key
        qa = [(f"Jakou rozteč kol mají auta {make}?", " ".join(f"{m['name']}: {m['pl']}, středový otvor {fmt(m['cb'])} mm." for m in ms[:8])
               + " Údaje jsou pro tovární kola; vždy je ověřte v technickém průkazu."),
              (f"Kolik stojí alu kola na {make}?", f"Nejlevnější alu kolo, které rozměrově sedí na některý model {make}, teď stojí {R.kc(lo)} za kus, "
                                                  f"polovina nabídek je do {R.kc(items[len(items) // 2]['price'])}. Sada čtyř kol tedy vyjde od {R.kc(lo * 4)}.")] + qa[1:]
    if kind == "pcd" or kind == "rimpcd":
        qa.append((f"Která auta mají rozteč {pl}?", (f"{pl} je typická rozteč " + {"5x112": "koncernu Volkswagen (VW, Škoda, Audi, Seat, Cupra) a novějších Mercedesů a BMW",
                                                                                "5x100": "menších vozů koncernu Volkswagen (Fabia, Scala, Kamiq, Polo, Ibiza) a Subaru",
                                                                                "5x114-3": "japonských a korejských aut (Toyota, Hyundai, Kia, Mazda, Honda, Nissan) a Renaultu",
                                                                                "4x100": "starších malých aut (Opel, Renault, Honda, Toyota, Hyundai, Kia)",
                                                                                "5x108": "Fordu, Volva, Peugeotu, Citroënu a Jaguaru",
                                                                                "5x120": "BMW a dodávek VW Transporter",
                                                                                "4x108": "starších Peugeotů, Citroënů a Fordů",
                                                                                "5x110": "Opelu, Alfy Romeo, Fiatu a Jeepu"}.get(ps, "více značek") + ". Vždy ale ověřte konkrétní model v technickém průkazu.")))
    faq_html, faq_ld = S.faq(qa)
    pre = (f'<h2>Rozteč a středový otvor podle modelu</h2>{car_table(key[1], pages)}<p class="note">⚠️ Údaje jsou pro tovární kola a mohou se lišit podle motorizace a roku výroby. Před nákupem je ověřte v technickém průkazu.</p>'
           if kind == "car" and first else "")
    body = pre + (f'<div class="layout"><aside class="card wside">{filt}<details class="wmore"><summary>Další filtry: šířka, ET, středový otvor, cena</summary><form id="wff" class="wff">{facets(items, alu)}'
            f'<button class="btn btn--g wreset" type="button" hidden>Zrušit filtry</button></form></details></aside><div>{head}{grid}</div></div>' + (f'<div class="prose hubtext">{HUB_GUIDE}</div>' if first and kind in ("hub", "steel", "rim", "pcd") else "") + (faq_html if first else ""))
    title = {"hub": f"Alu kola a hliníkové disky na auto od {R.kc(lo)} | auty.cz", "steel": f"Plechové disky na auto od {R.kc(lo)} | auty.cz"}.get(kind) or f"{h1} – ceny od {R.kc(lo)} | auty.cz"
    desc = f"{h1}: {len(items)} {'kol' if alu else 'disků'} z {'e-shopů' if shops_n > 1 else 'e-shopu'}, seřazeno podle ceny od {R.kc(lo)}. Filtr podle průměru, rozteče a značky."
    crumbs = [("auty.cz", "/"), (noun, base)] + ([(h1.replace(noun + " ", ""), path)] if path != base else [])
    if kind == "car":
        crumbs = [("auty.cz", "/"), ("Rozteč kol", "/roztec-kol/"), (key[0], path)]
        title = f"Alu kola {key[0]} – disky podle modelu, rozteč {', '.join(sorted({m['pl'] for m in key[1]}))[:40]} | auty.cz"
    intro = f"{lead} Nabídka: {len(items)} {'kol' if alu else 'disků'}, nejlevněji od {R.kc(lo)}."
    return S.layout(path, title, desc, body, crumbs, band=S.band(h1, intro, "/img/ic/disk.webp"), jsonld=[faq_ld] if faq_ld and first else None,
                    noindex=len(items) < 12)


def pcd_page(S, models, wpages):
    by_make = defaultdict(list)
    for m in models:
        by_make[m["make"]].append(m)
    blocks = ""
    for make in sorted(by_make, key=lambda k: (-len(by_make[k]), k)):
        mp = f"/alu-kola-na-auto/{T.slugify(make)}/"
        link = f' <a class="lnk" href="{mp}">Alu kola na {E(make)} →</a>' if mp in wpages else ""
        blocks += f'<h2 id="{T.slugify(make)}">{E(make)}{link}</h2>' + car_table(by_make[make], wpages)
    common = Counter(m["ps"] for m in models)
    pills = "".join(f'<li><a href="/alu-kola/{ps}/">{next(m["pl"] for m in models if m["ps"] == ps)} <small>{n} modelů</small></a></li>'
                    for ps, n in common.most_common(10) if f"/alu-kola/{ps}/" in wpages)
    toc = "".join(f'<li><a href="#{T.slugify(mk)}">{E(mk)}</a></li>' for mk in sorted(by_make, key=lambda k: (-len(by_make[k]), k)))
    qa = [("Co je rozteč kol?", "Rozteč (anglicky PCD) udává počet šroubů a průměr kružnice, na které leží, v milimetrech. Zápis 5×112 znamená pět šroubů na kružnici o průměru 112 mm. Kolo s jinou roztečí na auto nenasadíte."),
          ("Kde zjistím rozteč kol svého auta?", "V technickém průkazu (ORV), v COC listu, na vnitřní straně původního kola nebo v tabulce výše. Údaje v tabulce jsou pro tovární kola — u konkrétního vozu je vždy ověřte."),
          ("Jak změřit rozteč 5 šroubů?", "U pěti šroubů nejde měřit přímo protilehlé šrouby. Změřte vzdálenost středů dvou sousedních šroubů a vynásobte ji 1,701 — vyjde průměr kružnice. U 4 a 6 šroubů změříte přímo středy protilehlých šroubů."),
          ("Co je středový otvor a zális ET?", "Středový otvor musí být stejný jako náboj auta, nebo větší a dorovnaný centrovacím kroužkem. Zális ET říká, o kolik milimetrů je dosedací plocha posunutá od středu ráfku; větší odchylka od továrního ET mění rozchod kol a vyžaduje schválení."),
          ("Jaká auta mají rozteč 5×112?", "Rozteč 5×112 má většina vozů koncernu Volkswagen (Škoda Octavia, Superb, Kodiaq, VW Golf, Passat, Audi A3/A4, Seat Leon) a novější Mercedes-Benz a BMW.")]
    faq_html, faq_ld = S.faq(qa)
    body = (f'<p class="lead">Rozteč šroubů a středový otvor továrních kol u {len(models)} nejčastějších aut v Česku. Klikněte na rozteč a uvidíte alu kola, která na auto pasují, s cenami z e-shopů.</p>'
            f'<h2>Nejčastější rozteče</h2><ul class="pills">{pills}</ul><h2>Značky aut</h2><ul class="pills">{toc}</ul>{blocks}'
            f'<p class="note">⚠️ Údaje jsou pro tovární kola a mohou se lišit podle motorizace a roku výroby. Před nákupem je ověřte v technickém průkazu nebo u prodejce.</p>'
            f'<div class="prose hubtext">{HUB_GUIDE}</div>{faq_html}')
    return S.layout("/roztec-kol/", "Rozteč kol podle auta – tabulka roztečí a středových otvorů | auty.cz",
                    f"Rozteč kol (PCD) a středový otvor u {len(models)} aut: Škoda, VW, Hyundai, Kia, Toyota, Ford… Tabulka a alu kola, která na auto sedí.",
                    body, [("auty.cz", "/"), ("Alu kola", "/alu-kola/"), ("Rozteč kol", "/roztec-kol/")],
                    band=S.band("Rozteč kol podle auta", "Tabulka roztečí šroubů a středových otvorů pro nejčastější auta v Česku.", "/img/ic/disk.webp"),
                    jsonld=[faq_ld] if faq_ld else None)
