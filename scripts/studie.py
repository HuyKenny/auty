"""Data studies: /studie/<slug>/ pages with key numbers, an SVG chart, a table, methodology, FAQ, Article + Dataset JSON-LD and a CSV of the aggregates.

A study = content/studies/<slug>.json (texts with {placeholders}) + a derive function (scripts/studie_data.py) that turns the daily aggregates in
state["agg"] into numbers, table rows, chart rows and CSV rows. Only AGGREGATES are published (never shop-level prices or shop names).
A study is built when its "publish" date has come (STUDY_PREVIEW=1 builds it earlier, noindex) and when derive() has enough data.
Copy of apps/hrackarium/scripts/studie.py (keep the two in sync)."""
import csv
import html
import io
import json
import os
import re
from pathlib import Path

E = html.escape


def fmt(text, v):
    """{key} placeholders; an unknown key stays visible so a typo is noticed, not silently dropped."""
    return re.sub(r"\{(\w+)\}", lambda m: str(v.get(m.group(1), m.group(0))), text)


def bar_chart(rows, suffix="", aria="Graf"):
    """Horizontal bars, rows = [(label, value)]; pure SVG, no script."""
    if not rows:
        return ""
    top = max(v for _, v in rows) or 1
    h = 30 * len(rows) + 8
    bars = "".join(
        f'<text x="0" y="{22 + i * 30}" class="sc-l">{E(str(l))}</text>'
        f'<rect x="150" y="{8 + i * 30}" width="{max(2, round(v / top * 380))}" height="20" rx="4" class="sc-b"/>'
        f'<text x="{150 + max(2, round(v / top * 380)) + 8}" y="{23 + i * 30}" class="sc-v">{str(round(v, 1)).replace(".", ",")}{E(suffix)}</text>'
        for i, (l, v) in enumerate(rows))
    return f'<svg class="schart" viewBox="0 0 640 {h}" role="img" aria-label="{E(aria)}">{bars}</svg>'


def line_chart(series, suffix="", aria="Graf"):
    """series = {name: [(label, value), ...]}; one polyline per name, labels of the first series on the x axis."""
    names = [n for n, pts in series.items() if len(pts) >= 2]
    if not names:
        return ""
    allv = [v for n in names for _, v in series[n]]
    lo, hi = 0, max(allv) or 1
    W, H, pad = 640, 220, 34
    out, cls = [], ["sc-l1", "sc-l2", "sc-l3"]
    for k, n in enumerate(names):
        pts = series[n]
        xy = [(pad + i * (W - 2 * pad) / (len(pts) - 1), H - pad - (v - lo) / (hi - lo) * (H - 2 * pad)) for i, (_, v) in enumerate(pts)]
        out.append(f'<polyline class="sc-line {cls[k % 3]}" fill="none" points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in xy)}"/>')
        out.append(f'<text x="{xy[-1][0] - 4:.1f}" y="{xy[-1][1] - 8:.1f}" text-anchor="end" class="sc-v">{str(round(pts[-1][1], 1)).replace(".", ",")}{E(suffix)}</text>')
    first = series[names[0]]
    lab = "".join(f'<text x="{pad + i * (W - 2 * pad) / (len(first) - 1):.1f}" y="{H - 8}" text-anchor="middle" class="sc-l">{E(l)}</text>' for i, (l, _) in enumerate(first) if i % max(1, len(first) // 6) == 0 or i == len(first) - 1)
    legend = "".join(f'<text x="{pad + 150 * k}" y="14" class="sc-l {cls[k % 3]}">{E(n)}</text>' for k, n in enumerate(names))
    return f'<svg class="schart" viewBox="0 0 640 {H}" role="img" aria-label="{E(aria)}">{legend}{"".join(out)}{lab}</svg>'


def table(head, rows):
    th = "".join(f"<th>{E(h)}</th>" for h in head)
    tr = "".join("<tr>" + "".join(f"<td>{E(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tw"><table class="ct"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>'


def render(st, d, site, base, today):
    """-> (body html, json-ld list, csv text). d = derive() result: {vars, kpis?, chart_html, table_head, table_rows, csv_head, csv_rows}."""
    v = d["vars"]
    lead = fmt(st["lead"], v)
    kp = "".join(f'<li><b>{E(fmt(k["value"], v))}</b><span>{E(k["label"])}</span></li>' for k in st["kpis"] if "{" not in fmt(k["value"], v))
    secs = "".join(f'<h2>{E(s["h2"])}</h2>{fmt(s["html"], v)}' for s in st["sections"])
    faq = "".join(f"<details><summary>{E(q)}</summary><div>{fmt(a, v)}</div></details>" for q, a in st.get("faq", []))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(d["csv_head"])
    w.writerows(d["csv_rows"])
    url = f"{base}/studie/{st['slug']}/"
    csv_url = f"/data/studie/{st['slug']}.csv"
    quote = fmt(st["quote"], v)
    body = (f'<article class="study"><header class="study__h"><p class="post__meta">Datová studie · {E(d["updated"])}</p><h1>{E(fmt(st["h1"], v))}</h1><p class="lead">{lead}</p></header>'
            f'<ul class="kpis">{kp}</ul>'
            f'<figure class="study__fig">{d["chart_html"]}<figcaption>{E(fmt(st["chart_caption"], v))}</figcaption></figure>'
            f'{secs}'
            f'<h2>Tabulka</h2>{table(d["table_head"], d["table_rows"])}'
            f'<h2>Jak jsme měřili</h2>{fmt(st["method"], v)}'
            f'<section class="study__cite"><h2>Citace a data ke stažení</h2><p>Data můžete volně použít se zdrojem. Doporučená citace: <q>{E(quote)}</q> Zdroj: {E(site["name"])}, <a href="{url}">{url}</a>.</p>'
            f'<p><a class="btn" href="{csv_url}" download>Stáhnout data (CSV)</a> <small>Licence CC BY 4.0, agregovaná data bez cen jednotlivých obchodů.</small></p></section>'
            f'{"<section class=faq><h2>Časté otázky</h2>" + faq + "</section>" if faq else ""}</article>')
    pub = site["publisher"]
    ld = [
        {"@context": "https://schema.org", "@type": "Article", "headline": fmt(st["h1"], v), "description": fmt(st["desc"], v), "mainEntityOfPage": url,
         "datePublished": st["publish"], "dateModified": d["updated_iso"], "inLanguage": "cs-CZ", "author": pub, "publisher": pub},
        {"@context": "https://schema.org", "@type": "Dataset", "name": fmt(st["dataset"]["name"], v), "description": fmt(st["dataset"]["description"], v), "url": url,
         "license": "https://creativecommons.org/licenses/by/4.0/", "isAccessibleForFree": True, "inLanguage": "cs-CZ", "creator": pub, "publisher": pub,
         "dateModified": d["updated_iso"], "temporalCoverage": d["coverage"], "spatialCoverage": "Česko",
         "variableMeasured": st["dataset"]["variables"], "distribution": [{"@type": "DataDownload", "encodingFormat": "text/csv", "contentUrl": base + csv_url}]},
    ]
    return body, ld, buf.getvalue()


def build(out, S, app, base, site, derive_fns, agg, suffix, root_crumb, today, log=print):
    """Writes every published study + /studie/ index + CSV. Returns the list of published slugs."""
    sdir = Path(app) / "content/studies"
    studies = [json.loads(f.read_text()) for f in sorted(sdir.glob("*.json"))] if sdir.exists() else []
    preview = os.environ.get("STUDY_PREVIEW") == "1"
    done = []
    for st in studies:
        if st["publish"] > today.isoformat() and not preview:
            continue
        fn = derive_fns.get(st["slug"])
        d = fn(agg) if fn else None
        if not d:
            log(f"study {st['slug']}: not enough data yet, skipped")
            continue
        body, ld, csv_text = render(st, d, site, base, today)
        v = d["vars"]
        title = fmt(st["title"], v) + suffix
        out.page(f"/studie/{st['slug']}/", S.layout(f"/studie/{st['slug']}/", title, fmt(st["desc"], v), body, crumbs=[root_crumb, ("Studie", "/studie/"), (fmt(st["h1"], v), f"/studie/{st['slug']}/")], jsonld=ld,
                                                  noindex=st["publish"] > today.isoformat()), "pages", index=st["publish"] <= today.isoformat())
        out.write(f"data/studie/{st['slug']}.csv", csv_text)
        done.append((st, d))
    if done:
        cards = "".join(f'<li class="bc"><div class="bc__b"><p class="post__meta">{E(d["updated"])}</p><h2><a href="/studie/{st["slug"]}/">{E(fmt(st["h1"], d["vars"]))}</a></h2><p>{E(fmt(st["desc"], d["vars"]))}</p></div></li>' for st, d in done)
        idx = (f'<div class="blogh"><h1>Datové studie</h1><p class="lead">Čísla z našich dat o cenách. Agregovaná, bez cen jednotlivých obchodů, ke stažení a k citaci se zdrojem.</p></div><ul class="blog">{cards}</ul>')
        out.page("/studie/", S.layout("/studie/", "Datové studie o cenách" + suffix, "Datové studie o cenách z našich dat: metodika, grafy a data ke stažení.", idx, crumbs=[root_crumb, ("Studie", "/studie/")]), "pages")
    return [st["slug"] for st, _ in done]
