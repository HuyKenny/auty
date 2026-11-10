"""Parse tire attributes out of affiliate feed titles / categories / URLs."""
import re
import unicodedata

DIM = re.compile(r"(?<![\d.])(\d{3})\s*/\s*(\d{2,3})\s*(ZR|Z\s?R|R\s?F|R|-)?\s*(\d{2}(?:[.,]5)?)(C)?(?![\d.])", re.I)
LISI = re.compile(r"(?<![\w/])(\d{2,3}(?:/\d{2,3})?)\s?([A-HJ-NP-Z]|ZR)(?![\w+])")
SILI = re.compile(r"(?<![\w/])([A-HJ-NP-Z])\s?(\d{2,3}(?:/\d{2,3})?)(?![\w/])")
EU_URL = re.compile(r"-rr-([a-g])-wg-([a-g])-nl-(\d)\((\d{2})\)", re.I)

BRANDS = {
    "bf goodrich": "BFGoodrich", "bfgoodrich": "BFGoodrich", "nokian tyres": "Nokian", "nokian": "Nokian",
    "general tire": "General Tire", "general": "General Tire", "gt radial": "GT Radial", "a-plus": "Aplus", "aplus": "Aplus",
    "goodyear": "Goodyear", "continental": "Continental", "michelin": "Michelin", "bridgestone": "Bridgestone", "pirelli": "Pirelli",
    "hankook": "Hankook", "dunlop": "Dunlop", "kumho": "Kumho", "yokohama": "Yokohama", "barum": "Barum", "firestone": "Firestone",
    "nexen": "Nexen", "maxxis": "Maxxis", "falken": "Falken", "toyo": "Toyo", "vredestein": "Vredestein", "uniroyal": "Uniroyal",
    "semperit": "Semperit", "matador": "Matador", "debica": "Dębica", "dębica": "Dębica", "fulda": "Fulda", "sava": "Sava",
    "kleber": "Kleber", "kormoran": "Kormoran", "laufenn": "Laufenn", "sailun": "Sailun", "nankang": "Nankang", "cooper": "Cooper",
    "gislaved": "Gislaved", "avon": "Avon", "viking": "Viking", "marshal": "Marshal", "giti": "Giti", "triangle": "Triangle",
    "gripmax": "Gripmax", "prinx": "Prinx", "linglong": "Linglong", "imperial": "Imperial", "tracmax": "Tracmax", "rotalla": "Rotalla",
    "riken": "Riken", "tigar": "Tigar", "taurus": "Taurus", "strial": "Strial", "orium": "Orium", "kenda": "Kenda", "lassa": "Lassa",
    "petlas": "Petlas", "starmaxx": "Starmaxx", "nordexx": "Nordexx", "goodride": "Goodride", "westlake": "Westlake",
    "landsail": "Landsail", "roadstone": "Roadstone", "evergreen": "Evergreen", "fortuna": "Fortuna", "berlin tires": "Berlin Tires",
    "tomket": "Tomket", "mitas": "Mitas", "metzeler": "Metzeler", "heidenau": "Heidenau", "continental moto": "Continental",
    "minerva": "Minerva", "hifly": "HiFly", "hi fly": "HiFly", "arivo": "Arivo", "davanti": "Davanti", "kingstar": "Kingstar",
    "zeetex": "Zeetex", "bfg": "BFGoodrich", "toyo tires": "Toyo", "momo": "Momo", "radar": "Radar", "pace": "Pace", "delinte": "Delinte",
    "mazzini": "Mazzini", "superia": "Superia", "aeolus": "Aeolus", "double coin": "Double Coin", "ceat": "CEAT", "apollo": "Apollo",
    "cst": "CST", "unigrip": "Unigrip", "wanli": "Wanli", "sunny": "Sunny", "milestone": "Milestone", "doublestar": "Doublestar",
}

FLAG_TOKENS = {"TL", "TT", "XL", "RF", "REINF", "M+S", "MS", "3PMSF", "FR", "MFS", "FSL", "RFT", "SSR", "ROF", "ZP", "RUNFLAT",
               "DOT", "EXTRA", "LOAD", "MO", "MO1", "MOE", "AO", "AO1", "N0", "N1", "N2", "N3", "N4", "N5", "VOL", "T0", "T1",
               "J", "LR", "RO1", "K1", "K2", "*", "BLK", "PR", "4PR", "6PR", "8PR", "10PR", "C", "SEAL", "SEALINSIDE", "CS", "NCS",
               "ACOUSTIC", "SILENT", "ELECT", "HL", "+", "PNCS", "SCT", "EV", "ENLITEN", "OWL", "RWL", "OWL/RWL", "WSW", "B", "BSW",
               "FP", "C1", "C2", "C3", "EVR", "TL/TT", "MFS)", "(MFS", "SL", "RPB", "FRV", "VSB", "OWL/BSW", "SBL", "MFS/FSL"}
SEASON_WORDS = re.compile(r"\b(osobn[ií]|zimn[ií]|letn[ií]|celoro[čc]n[ií]|celosez[oó]nn[ií]|pneu(matika|matiky)?|off-?road|4x4 (zimn[ií]|letn[ií]))\b", re.I)

WINTER_MODEL = re.compile(r"winter|snow|ice|alpin|blizzak|nordic|polar|frigo|sottozero|wintrac|ultra ?grip|hakka|eurowinter|winguard|"
                          r"wintercraft|i\*?cept|icept|arctic|zimn|w ?\d{3}\b|\bts ?8[5-7]\d|\bkw\d|\bws\d|\bwp\d|\bis ?\d|snowtrac|"
                          r"\bsw\d|\bcw-|winter ?hawk|polaris|frigus|eskimo|wr ?[a-z]?\d|sn3|nord|glacier", re.I)
ALLSEASON_MODEL = re.compile(r"all ?season|4 ?seasons?|crossclimate|cross climate|quatrac|vector 4|celoro|celosez|multi ?season|"
                             r"weather ?(react|proof|control)|seasonproof|all ?weather|kinergy 4s|allseasoncontact|turanza all|"
                             r"\b4s\b|go ?all|all ?climate|quadraxer|4 ?ever|multiway|duraturn all|\bas ?\d?\b|a/s", re.I)


def fold(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", fold(s).replace("*", " star ").replace("+", " plus "))
    return s.strip("-")


def norm_ean(e):
    e = re.sub(r"\D", "", e or "").lstrip("0")
    return e if 8 <= len(e) <= 14 else ""


def brand_of(raw, title):
    for cand in (raw, title):
        f = fold(cand).strip()
        if not f:
            continue
        for k in sorted(BRANDS, key=len, reverse=True):
            if f == k or f.startswith(k + " "):
                return BRANDS[k]
    raw = (raw or "").strip()
    if not raw:
        return ""
    return raw if not raw.isupper() or len(raw) <= 3 else raw.title()


KEEP_UPPER = {"SUV", "AT", "MT", "HT", "HP", "UHP", "EV", "4X4", "RS", "GT", "GTS", "AS", "A/T", "M/T", "H/T", "R/T", "LT", "CS", "SL", "XT", "TS", "KO2", "AWD"}
CAMEL = re.compile(r"(?<=[a-z])(contact|climate|grip|proof|craft|guard|sport|force|trac|drive|power|terrain|winter|season|max|life|blue|green|tour)", re.I)


def nice_word(w):
    if not w.isupper() or len(w) <= 2:
        return w
    if w in KEEP_UPPER or re.search(r"\d", w):
        return w
    return CAMEL.sub(lambda m: m.group(1).capitalize(), w.capitalize())


def dim_slug(w, p, r):
    return f"{w}-{p}-r{r.replace('.', '-')}"


def dim_label(w, p, r):
    return f"{w}/{p} R{r}"


def parse(rec):
    """rec: slim offer from fetch.py -> dict of attributes, or None when it is not a recognisable tire."""
    title = re.sub(r"\s+", " ", (rec.get("t") or "").replace("_", " ")).strip()
    dot = None
    md = re.search(r"\bDOT\s?(\d{2})(\d{2})?\b|\((20[12]\d)\)", title)
    if md:
        dot = int(md.group(3)) if md.group(3) else 2000 + int(md.group(2) or md.group(1)) if (md.group(2) or md.group(1)) else None
        if md.group(1) and md.group(2):          # DOT week+year, e.g. DOT 4821
            dot = 2000 + int(md.group(2))
        title = (title[:md.start()] + title[md.end():]).strip()
    cat = rec.get("cat") or ""
    url = rec.get("url") or ""
    if re.search(r"\b(du[šs]e|inner tube|schlauch|vložka|ochranná páska)\b", title, re.I):   # inner tubes / rim tape are not tyres
        return None
    m = DIM.search(title)
    if not m:
        m = DIM.search(rec.get("d") or "")
        if not m:
            return None
    w, p, construction, rim, csuffix = m.group(1), m.group(2), (m.group(3) or "").upper().replace(" ", ""), m.group(4).replace(",", "."), m.group(5)
    if not (125 <= int(w) <= 445 and 25 <= int(p) <= 100):
        return None
    after = title[m.end():]
    li = si = ""
    mm = LISI.search(after) or LISI.search(title[:m.start()])
    if mm:
        li, si = mm.group(1), mm.group(2).upper()
    else:
        mm = SILI.search(after)
        if mm:
            si, li = mm.group(1).upper(), mm.group(2)
    if si == "ZR":
        si = "W"
    if construction == "ZR" and not si:
        si = "W"
    brand = brand_of(rec.get("b"), title)
    up = " " + title.upper() + " "
    flags = set(t for t in re.split(r"[\s,()]+", up) if t)
    xl = bool(re.search(r"\b(XL|REINF|EXTRA LOAD|RF)\b", up)) and not re.search(r"\bRFT\b", up) or " XL " in up
    runflat = bool(re.search(r"\b(RFT|SSR|ROF|ZP|RUNFLAT|RUN FLAT|ZPS|HRS|DSST|EMT|RSC|MOE)\b", up))
    homolog = sorted(t for t in flags & {"MO", "MO1", "MOE", "AO", "AO1", "N0", "N1", "N2", "N3", "N4", "N5", "VOL", "T0", "J", "LR", "RO1"})
    if re.search(r"\s\*(\s|$)|\(\*\)|★", title):
        homolog.append("★")
    ev = bool(re.search(r"\b(ENLITEN|EV|EVC|ELECT|E-PRIMACY|HL)\b", up))
    pmsf = "3PMSF" in up or "3PMSF" in fold(url).upper() or "SNOWFLAKE" in up
    ms = "M+S" in up or " MS " in up or "M S" in up

    # model = the title without brand, size, indices and flags
    rest = title[:m.start()] + " " + title[m.end():]
    if brand:
        aliases = sorted({k for k, v in BRANDS.items() if v == brand} | {brand, (rec.get("b") or "").strip()}, key=len, reverse=True)
        for k in aliases:
            if k:
                rest = re.sub(r"(?i)(?<!\w)" + re.escape(k) + r"(?!\w)", " ", rest)
    if li and si:
        rest = re.sub(r"(?i)(?<![\w/])" + re.escape(li) + r"\s?" + re.escape(si) + r"(?!\w)", " ", rest, count=1)
        rest = re.sub(r"(?i)(?<![\w/])" + re.escape(si) + r"\s?" + re.escape(li) + r"(?!\w)", " ", rest, count=1)
    rest = re.sub(r"(?i)\([^()]*pneu[^()]*\)", " ", rest)      # "(Osobní letní pneu)" category notes in titles
    rest = SEASON_WORDS.sub(" ", rest)
    words = [x for x in re.split(r"\s+", rest) if x]
    model_words = []
    for x in words:
        xu = x.upper().strip(",.()")
        if not xu or xu in FLAG_TOKENS or "*" in xu or re.fullmatch(r"\d+PR|\(\d{2}\)|DOT\d+|\d{2,3}/\d{2,3}[A-Z]?|\d{2,3}[JKLMNPQRSTUHVWYZ]", xu) or xu in ("-", "|", "/"):
            continue
        model_words.append(nice_word(x.strip(",")))
    model = " ".join(model_words)
    # marketing / rim-protection labels are flags, not tread patterns ("WinterContact TS 870 P EVc s ramenem ráfku" = "WinterContact TS 870 P")
    model = re.sub(r"(?i)\[ev\+?\]|\(ev\)|\bs (ramenem|ochrannou) ráfku(leiste)?\b|\bsuitable\b|\bEVc\b", " ", model)
    model = re.sub(r"\s+", " ", model).strip(" -")[:60]
    if not model:
        model = "?"

    text = fold(f"{cat} {title} {url}")
    season = ""
    params = rec.get("par") or {}
    sez = fold(params.get("sezóna", ""))
    for src in (fold(cat), sez):
        if re.search(r"celoro|celosez|all ?season", src):
            season = "all"
        elif "zimn" in src or "winter" in src:
            season = "winter"
        elif "letn" in src or "summer" in src:
            season = "summer"
        if season:
            break
    if not season:
        mf = fold(model + " " + title)
        if ALLSEASON_MODEL.search(mf):
            season = "all"
        elif WINTER_MODEL.search(mf) or "zimn" in text:
            season = "winter"
        elif "celoro" in text:
            season = "all"
        elif pmsf:
            season = "winter"
        else:
            season = "summer"

    vehicle = "car"
    c = fold(cat).replace("osobni a 4x4", "osobni").replace("auto-moto", "auto")   # Heureka root "Auto-moto" is not moto
    if re.search(r"moto|skutr|scooter", c):
        vehicle = "moto"
    elif re.search(r"nakladn|truck|tir\b|kamion", c) or rim.endswith(".5"):
        vehicle = "truck"
    elif re.search(r"agro|zemedel|traktor|prumysl|atv|quad|vozik|privesn", c):
        vehicle = "other"
    elif re.search(r"dodav|van|lehk", c) or csuffix or "/" in li:
        vehicle = "van"
    elif re.search(r"suv|4x4|off-?road|terenn", c) or re.search(r"\b(SUV|4X4|AT\d?|MT\d?|A/T|M/T|KO2|ALL[ -]TERRAIN|MUD)\b", up) or \
            re.search(r"grabber|wrangler|dueler|latitude|scorpion|crosscontact|cross contact|geolandar|terrain|road ?venture|baja", fold(title)):
        vehicle = "suv"
    elif int(w) % 10 == 0 and not csuffix:
        vehicle = "moto"
    if re.search(r"disk|kola|alu|plech|ret[ez]|řetěz", c) and not re.search(r"pneu", c):
        return None

    eu = None
    me = EU_URL.search(url)
    if me:
        eu = {"rr": me.group(1).upper(), "wg": me.group(2).upper(), "nc": int(me.group(3)), "db": int(me.group(4))}

    return {"w": w, "p": p, "r": rim, "li": li, "si": si, "brand": brand, "model": model, "season": season,
            "vehicle": vehicle, "xl": bool(xl), "rf": runflat, "hom": homolog, "ev": ev, "pmsf": pmsf, "ms": ms,
            "c": bool(csuffix), "eu": eu, "dot": dot if dot and 2015 <= dot <= 2030 else None}


def model_key(brand, model):
    return slugify(brand) + ":" + re.sub(r"[^a-z0-9]", "", fold(model))


# load index -> kg, speed index -> km/h
LI_KG = {50: 190, 51: 195, 52: 200, 53: 206, 54: 212, 55: 218, 56: 224, 57: 230, 58: 236, 59: 243, 60: 250, 61: 257, 62: 265,
         63: 272, 64: 280, 65: 290, 66: 300, 67: 307, 68: 315, 69: 325, 70: 335, 71: 345, 72: 355, 73: 365, 74: 375, 75: 387,
         76: 400, 77: 412, 78: 425, 79: 437, 80: 450, 81: 462, 82: 475, 83: 487, 84: 500, 85: 515, 86: 530, 87: 545, 88: 560,
         89: 580, 90: 600, 91: 615, 92: 630, 93: 650, 94: 670, 95: 690, 96: 710, 97: 730, 98: 750, 99: 775, 100: 800, 101: 825,
         102: 850, 103: 875, 104: 900, 105: 925, 106: 950, 107: 975, 108: 1000, 109: 1030, 110: 1060, 111: 1090, 112: 1120,
         113: 1150, 114: 1180, 115: 1215, 116: 1250, 117: 1285, 118: 1320, 119: 1360, 120: 1400, 121: 1450, 122: 1500,
         123: 1550, 124: 1600, 125: 1650, 126: 1700}
SI_KMH = {"J": 100, "K": 110, "L": 120, "M": 130, "N": 140, "P": 150, "Q": 160, "R": 170, "S": 180, "T": 190, "U": 200,
          "H": 210, "V": 240, "W": 270, "Y": 300, "Z": 240}
SI_ORDER = "JKLMNPQRSTUHVWYZ"
HOMOLOG = {"MO": "Mercedes-Benz", "MO1": "Mercedes-AMG", "MOE": "Mercedes-Benz (run-flat)", "AO": "Audi", "AO1": "Audi RS",
           "N0": "Porsche", "N1": "Porsche", "N2": "Porsche", "N3": "Porsche", "N4": "Porsche", "N5": "Porsche", "★": "BMW / MINI",
           "VOL": "Volvo", "T0": "Tesla", "J": "Jaguar", "LR": "Land Rover", "RO1": "Audi quattro"}
SEASON_CZ = {"summer": "letní", "winter": "zimní", "all": "celoroční"}
SEASON_GEN = {"summer": "letních", "winter": "zimních", "all": "celoročních"}
SEASON_SLUG = {"summer": "letni", "winter": "zimni", "all": "celorocni"}
VEHICLE_CZ = {"car": "osobní", "suv": "SUV a 4x4", "van": "dodávky", "moto": "motocyklové", "truck": "nákladní", "other": "ostatní"}
