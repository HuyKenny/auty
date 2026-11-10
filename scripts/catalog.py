"""Merge offers from all shops into products (EAN first, then identical tire attributes), keep slugs stable between runs."""
import gzip
import hashlib
import json
import re
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import tires as T

APP = Path(__file__).resolve().parent.parent
RAW = APP / "data/raw"
STATE = APP / "data/state"

# image quality preference (first non-empty wins)
IMG_PREF = ["bestdrive", "nejvyhodnejsipneu", "dobrepneu", "pneuboss", "nejlevnejsipneu"]


def load_offers(shops):
    out = []
    for s in shops:
        p = RAW / f"{s['id']}.jsonl.gz"
        if not p.exists():
            continue
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                a = T.parse(rec)
                if not a or a["vehicle"] == "other" or not a["brand"]:
                    continue
                a.update(shop=s["id"], price=round(float(rec["p"])), go=rec["go"], url=rec["url"], img=rec.get("img") or "",
                         ean=T.norm_ean(rec.get("ean")), title=rec["t"], from_cat=bool(rec.get("cat")))
                out.append(a)
    return out


def attr_key(a):
    return "|".join([T.model_key(a["brand"], a["model"]), a["w"], a["p"], a["r"], a["li"], a["si"],
                     "xl" if a["xl"] else "", "rf" if a["rf"] else "", ",".join(a["hom"])])


class DSU:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def mixed_case_score(s):
    return sum(1 for c in s if c.islower()) - (5 if s.isupper() else 0)


def cluster(offers):
    dsu = DSU()
    for i, a in enumerate(offers):
        node = f"o{i}"
        if a["ean"]:
            dsu.union(node, "e" + a["ean"])
        dsu.union(node, "k" + attr_key(a))
    groups = defaultdict(list)
    for i, a in enumerate(offers):
        groups[dsu.find(f"o{i}")].append(a)
    products = []
    for offs in groups.values():
        eans = sorted({a["ean"] for a in offs if a["ean"]})
        pid = ("e" + eans[0]) if eans else ("k" + hashlib.md5(attr_key(offs[0]).encode()).hexdigest()[:12])
        best = {}
        for a in offs:
            if a["shop"] not in best or a["price"] < best[a["shop"]]["price"]:
                best[a["shop"]] = a
        shop_offers = sorted(best.values(), key=lambda a: a["price"])
        # attributes: majority / most trustworthy
        cat_season = [a["season"] for a in offs if a["from_cat"]]
        season = max(set(cat_season), key=cat_season.count) if cat_season else max(set(a["season"] for a in offs), key=[a["season"] for a in offs].count)
        veh = [a["vehicle"] for a in offs if a["from_cat"]] or [a["vehicle"] for a in offs]
        vehicle = max(set(veh), key=veh.count)
        model = max((a["model"] for a in offs), key=mixed_case_score)
        ref = offs[0]
        img = ""
        for shop in IMG_PREF + [a["shop"] for a in offs]:
            img = next((a["img"] for a in offs if a["shop"] == shop and a["img"]), "")
            if img:
                break
        eu = next((a["eu"] for a in offs if a["eu"]), None)
        products.append({
            "id": pid, "eans": eans, "brand": ref["brand"], "model": model, "mkey": T.model_key(ref["brand"], model),
            "w": ref["w"], "p": ref["p"], "r": ref["r"], "li": ref["li"], "si": ref["si"], "season": season, "vehicle": vehicle,
            "xl": any(a["xl"] for a in offs), "rf": any(a["rf"] for a in offs), "hom": sorted({h for a in offs for h in a["hom"]}),
            "ev": any(a["ev"] for a in offs), "pmsf": any(a["pmsf"] for a in offs), "ms": any(a["ms"] for a in offs),
            "c": any(a["c"] for a in offs), "eu": eu, "img": img,
            "offers": [{"shop": a["shop"], "price": a["price"], "go": a["go"], "dot": a.get("dot")} for a in shop_offers],
            "price": shop_offers[0]["price"],
        })
    return products


# ---------------------------------------------------------------- slugs

def product_slug(p):
    parts = [p["brand"], p["model"], p["w"], p["p"], "r" + p["r"].replace(".", "-"), f"{p['li']}{p['si']}".lower()]
    if p["xl"]:
        parts.append("xl")
    if p["rf"]:
        parts.append("runflat")
    parts += [h if h != "★" else "bmw" for h in p["hom"]]
    if p["c"]:
        parts.append("c")
    return T.slugify(" ".join(x for x in parts if x))[:110].strip("-")


OLD_DIM = re.compile(r"-(\d{3})-(\d{2})-z?r-?(\d{2}(?:-5)?)c?-(\d{2,3})-?([a-z]{1,2})(?:-|$)")


def parse_old(slug, brand_slugs):
    m = OLD_DIM.search(slug)
    if not m:
        return None
    head = slug[:m.start()]
    brand = next((b for b in brand_slugs if head == b or head.startswith(b + "-")), None)
    if not brand:
        return None
    model = re.sub(r"[^a-z0-9]", "", head[len(brand):])
    return {"brand": brand, "w": m.group(1), "p": m.group(2), "r": m.group(3).replace("-", "."), "li": m.group(4),
            "si": m.group(5).upper(), "model": model, "xl": "-xl" in slug[m.end() - 1:] or slug.endswith("-xl")}


def assign_slugs(products, state, old_slugs, log):
    """Keep existing slugs; give old auty.cz /produkt/ slugs to matching products; new slugs for the rest."""
    slugs = state.setdefault("slugs", {})
    used = set(slugs.values())
    brand_slugs = sorted({T.slugify(p["brand"]) for p in products} | {"nokian-tyres", "general-tire", "bf-goodrich", "a-plus"},
                         key=len, reverse=True)
    alias = {"nokian-tyres": "nokian", "bf-goodrich": "bfgoodrich", "a-plus": "aplus"}
    index = defaultdict(list)
    for p in products:
        if p["id"] not in slugs:
            index[(T.slugify(p["brand"]), p["w"], p["p"], p["r"], p["li"], p["si"])].append(p)
    matched = 0
    for old in old_slugs:
        if old in used:
            continue
        o = parse_old(old, brand_slugs)
        if not o:
            continue
        cands = [p for p in index.get((alias.get(o["brand"], o["brand"]), o["w"], o["p"], o["r"], o["li"], o["si"]), []) if p["id"] not in slugs]
        best, score = None, 0.0
        for p in cands:
            mk = p["mkey"].split(":", 1)[1]
            s = SequenceMatcher(None, o["model"], mk).ratio()
            if o["model"] and (o["model"] in mk or mk in o["model"]):
                s = max(s, 0.9)
            if o["xl"] == p["xl"]:
                s += 0.05
            if s > score:
                best, score = p, s
        if best and score >= 0.72:
            slugs[best["id"]] = old
            used.add(old)
            matched += 1
    new = 0
    for p in products:
        if p["id"] in slugs:
            continue
        base = product_slug(p)
        s, i = base, 2
        while s in used:
            s, i = f"{base}-{i}", i + 1
        slugs[p["id"]] = s
        used.add(s)
        new += 1
    log(f"slugs: {matched} old auty.cz URLs matched, {new} new")
    for p in products:
        p["slug"] = slugs[p["id"]]


def load_state():
    p = STATE / "state.json.gz"
    if p.exists():
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    STATE.mkdir(parents=True, exist_ok=True)
    with gzip.open(STATE / "state.json.gz", "wt", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
