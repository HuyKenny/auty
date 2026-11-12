"""derive functions of the data studies: state["agg"] (daily aggregates, scripts/catalog.py collect_study_data) -> numbers for scripts/studie.py."""
from datetime import date

import studie as ST


def cz(n, d=1):
    return f"{n:.{d}f}".replace(".", ",")


def tyre_spread(agg):
    days = sorted(int(k) for k in agg)
    if not days:
        return None
    k = days[-1]
    a = agg[str(k)]
    if a["multi"] < 1000:
        return None
    d = date.fromordinal(k)
    seas = a["season"]
    winter = seas.get("winter") or seas.get("zimni") or [0, 0, 0]
    sizes = [r for r in a["sizes"] if r[1] >= 15]
    v = {"n": f'{a["n"]:,}'.replace(",", " "), "multi": f'{a["multi"]:,}'.replace(",", " "), "med": cz(a["med"]), "p75": cz(a["p75"]), "p90": cz(a["p90"]),
         "gt10": cz(a["gt10"], 0), "gt20": cz(a["gt20"], 0), "gt50": cz(a["gt50"], 0), "save": f'{a["save_med"]:,}'.replace(",", " "), "eu": cz(a["eu"], 0),
         "date": f"{d.day}. {d.month}. {d.year}", "winter_n": f'{winter[0]:,}'.replace(",", " "), "winter_med": cz(winter[1]),
         "top_size": sizes[0][0] if sizes else "", "top_med": cz(sizes[0][2]) if sizes else ""}
    rows = [[r[0], r[1], f"{cz(r[2])} %", f"{r[3]:,} Kč".replace(",", " ")] for r in sizes[:20]]
    chart = ST.bar_chart([(r[0], r[2]) for r in sorted(sizes, key=lambda r: -r[2])[:12]], " %", "Medián rozdílu cen mezi nejlevnější a nejdražší nabídkou podle rozměru")
    csv_rows = [[d.isoformat(), r[0], r[1], r[2], r[3]] for r in a["sizes"]] + [[d.isoformat(), f"sezona:{s}", x[0], x[1], x[2]] for s, x in seas.items()]
    return {"vars": v, "updated": v["date"], "updated_iso": d.isoformat(), "coverage": d.isoformat(), "chart_html": chart,
            "table_head": ["Rozměr", "Pneumatik ve více obchodech", "Medián rozdílu", "Medián nejnižší ceny"], "table_rows": rows,
            "csv_head": ["datum", "rozmer_nebo_sezona", "pneumatik", "median_rozdilu_procent", "median_nejnizsi_ceny_kc"], "csv_rows": csv_rows}
