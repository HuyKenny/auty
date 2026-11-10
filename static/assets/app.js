(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var cache = {};
  function getJSON(u) {
    if (!cache[u]) cache[u] = fetch(u).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
    return cache[u];
  }
  function kc(n) { return Math.round(n).toLocaleString("cs-CZ").replace(/\s/g, " ") + " Kč"; }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function fold(s) { return s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, ""); }
  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }

  /* ---------- size in free text: 205/55 R16, 205 55 16, 205/55r16 ---------- */
  var DIM = /(\d{3})\s*[\/ -]?\s*(\d{2})\s*[ \/-]?\s*z?r?\s*(\d{2}(?:[.,]5)?)/i;
  function dimSlug(m) { return m[1] + "-" + m[2] + "-r" + m[3].replace(/[.,]/, "-"); }

  /* ---------- header search ---------- */
  var sInput = $("#q"), sBox = $("#sugg");
  if (sInput) {
    var idx = null, sel = -1, hits = [];
    var load = function () { if (!idx) getJSON("/data/search.json").then(function (d) { idx = d.map(function (x) { return [fold(x[0]), x[0], x[1], x[2]]; }); run(); }); };
    var run = function () {
      var q = sInput.value.trim(); hits = [];
      var m = q.match(DIM);
      if (m) hits.push(["Pneumatiky " + m[1] + "/" + m[2] + " R" + m[3], "/pneumatiky/" + dimSlug(m) + "/", "rozměr"]);
      if (idx && q.length > 1) {
        var words = fold(q).split(/\s+/).filter(Boolean);
        for (var i = 0; i < idx.length && hits.length < 12; i++) {
          var ok = true;
          for (var j = 0; j < words.length; j++) if (idx[i][0].indexOf(words[j]) < 0) { ok = false; break; }
          if (ok) hits.push([idx[i][1], idx[i][2], idx[i][3]]);
        }
      }
      sel = -1;
      sBox.innerHTML = hits.map(function (h, i) { return '<a href="' + h[1] + '" data-i="' + i + '">' + esc(h[0]) + ' <small>' + esc(h[2]) + "</small></a>"; }).join("");
      sBox.style.display = hits.length ? "block" : "none";
    };
    sInput.addEventListener("focus", load);
    sInput.addEventListener("input", function () { load(); run(); });
    sInput.addEventListener("keydown", function (e) {
      var links = $$("a", sBox);
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault(); sel = Math.max(0, Math.min(links.length - 1, sel + (e.key === "ArrowDown" ? 1 : -1)));
        links.forEach(function (a, i) { a.classList.toggle("on", i === sel); });
      } else if (e.key === "Enter") {
        e.preventDefault(); var h = hits[sel < 0 ? 0 : sel]; if (h) location.href = h[1];
      } else if (e.key === "Escape") { sBox.style.display = "none"; }
    });
    document.addEventListener("click", function (e) { if (!e.target.closest(".search")) sBox.style.display = "none"; });
    var go = $("#qgo");
    go && go.addEventListener("click", function () { load(); run(); var h = hits[0]; if (h) location.href = h[1]; else sInput.focus(); });
  }

  /* ---------- size picker ---------- */
  var pk = $("#picker");
  if (pk) {
    var W = $("#pw", pk), P = $("#pp", pk), R = $("#pr", pk);
    getJSON("/data/dims.json").then(function (dims) {
      var opt = function (sel, vals, cur) {
        sel.innerHTML = vals.map(function (v) { return "<option" + (v === cur ? " selected" : "") + ">" + v + "</option>"; }).join("");
      };
      var keys = Object.keys(dims);
      var uniq = function (a) { return a.filter(function (v, i) { return a.indexOf(v) === i; }).sort(function (x, y) { return parseFloat(x) - parseFloat(y); }); };
      var fill = function (lvl) {
        if (lvl < 1) opt(W, uniq(keys.map(function (k) { return dims[k][0]; })), W.value || pk.dataset.w || "205");
        if (lvl < 2) opt(P, uniq(keys.filter(function (k) { return dims[k][0] === W.value; }).map(function (k) { return dims[k][1]; })), P.value || pk.dataset.p || "55");
        opt(R, uniq(keys.filter(function (k) { return dims[k][0] === W.value && dims[k][1] === P.value; }).map(function (k) { return dims[k][2]; })), R.value || pk.dataset.r || "16");
      };
      fill(0);
      W.addEventListener("change", function () { P.value = ""; R.value = ""; fill(1); });
      P.addEventListener("change", function () { R.value = ""; fill(2); });
      pk.addEventListener("submit", function (e) {
        e.preventDefault();
        var slug = W.value + "-" + P.value + "-r" + R.value.replace(".", "-");
        var s = ($("input[name=season]:checked", pk) || {}).value || "";
        var d = dims[slug];
        location.href = "/pneumatiky/" + slug + "/" + (s && d && d[4] && d[4].indexOf(s) >= 0 ? s + "/" : (s ? "#" + s : ""));
      });
    });
  }

  /* ---------- listing filters over the pre-built JSON ---------- */
  var list = $("#plist");
  if (list && list.dataset.json) {
    // Item: [name, url, img, price, shops, season 0/1/2, brandIdx, li, si, xl, rf, vehicle, eu, saving%, dim, go?, shop?]
    // Brands are compared by NAME (each JSON numbers its brands on its own).
    // Hub pages (data-hub): the page JSON holds only the most compared tyres; picking a size or a brand loads the small
    // per-size / per-brand JSON and keeps the hub's own season / vehicle restriction.
    var form = $("#filters"), sortSel = $("#sort"), countEl = $("#count"), note = $("#hubnote");
    var PER = 24, pg = +(list.dataset.page || 1), pager = $("#pager"), SEAS = ["summer", "winter", "all"], SEAS_CZ = { summer: "letní", winter: "zimní", all: "celoroční" };
    var VEH = { car: "osobní", suv: "SUV", van: "dodávka", moto: "moto", truck: "nákladní" };
    var KMH = { J: 100, K: 110, L: 120, M: 130, N: 140, P: 150, Q: 160, R: 170, S: 180, T: 190, U: 200, H: 210, V: 240, W: 270, Y: 300 };
    var SI = "JKLMNPQRSTUHVWYZ", IMG = { summer: "letni", winter: "zimni", all: "celorocni" };
    var hub = list.dataset.hub ? JSON.parse(list.dataset.hub) : null;
    var named = function (d) { d.items.forEach(function (it) { it.bn = d.brands[it[6]]; }); return d.items; };
    var load = function (url) { return getJSON(url).then(function (d) { if (!d._n) { named(d); d._n = 1; } return d; }); };
    var base = function () { return load(list.dataset.json); };
    var source = function (dims, slugs) {
      if (!hub || (!dims.length && !slugs.length)) return base().then(function (d) { return { items: d.items, full: !hub }; });
      var urls = dims.length ? dims.map(function (x) { return "/data/d/" + x + ".json"; }) : slugs.map(function (x) { return "/data/b/" + x + ".json"; });
      return Promise.all(urls.map(load)).then(function (ds) {
        var seen = {}, out = [];
        ds.forEach(function (d) { d.items.forEach(function (it) { var k = it[1] || it[0]; if (!seen[k]) { seen[k] = 1; out.push(it); } }); });
        return { items: out, full: true };
      });
    };
    var buildBrands = function () {
      var box = $("#fbrands"); if (!box) return;
      base().then(function (d) {
        var cnt = {};
        d.items.forEach(function (it) { cnt[it.bn] = (cnt[it.bn] || 0) + 1; });
        box.innerHTML = Object.keys(cnt).sort(function (a, b) { return cnt[b] - cnt[a]; }).map(function (b) {
          return '<label><input type="checkbox" name="brand" value="' + esc(b) + '"> ' + esc(b) + '<span class="count">' + cnt[b] + "</span></label>"; }).join("");
      });
    };
    var row = function (it) {
      var eu = it[12] ? '<span class="eu" title="Štítek EU: valivý odpor, přilnavost, hluk"><span class="eu-' + it[12][0] + '">' + it[12][0] + '</span><span class="eu-' + it[12][1] + '">' + it[12][1] + "</span><span>" + it[12].slice(2) + " dB</span></span>" : "";
      var s = SEAS[it[5]], name = esc(it[0]), img = esc(it[2] || "/assets/tire.svg");
      var pic = it[1] ? '<a class="pc__img" href="' + it[1] + '" tabindex="-1"><img src="' + img + '" alt="" loading="lazy" width="180" height="150"></a>'
        : '<span class="pc__img"><img src="' + img + '" alt="" loading="lazy" width="180" height="150"></span>';
      var cta = it[1] ? '<a class="btn btn--o" href="' + it[1] + '">Porovnat ceny</a>'
        : '<a class="btn" href="' + esc(it[15]) + '" rel="sponsored nofollow noopener" target="_blank" data-go="' + esc(it[16] || "") + '">Do obchodu</a>';
      var badge = it[13] >= 5 ? '<span class="pc__save" title="Rozdíl mezi nejlevnějším a nejdražším obchodem">−' + it[13] + " %</span>" : "";
      var TIP = { summer: "Letní pneumatika: nejlepší nad 7 °C.", winter: "Zimní pneumatika: splňuje povinnost od 1. 11. do 31. 3.", all: "Celoroční pneumatika: nemusíte přezouvat." };
      var chips = '<span class="chip s-' + s + '" data-tip="' + TIP[s] + '"><img src="/img/ic/' + IMG[s] + '.webp" alt="" width="14" height="14"> ' + SEAS_CZ[s] + "</span>" +
        (it[7] ? '<span class="chip" data-tip="Index nosnosti ' + it[7] + (KMH[it[8]] ? ", rychlostní index " + it[8] + " = do " + KMH[it[8]] + " km/h" : "") + '.">' + it[7] + it[8] + "</span>" : "") +
        (it[9] ? '<span class="chip" data-tip="XL = zesílená pneumatika s vyšší nosností.">XL</span>' : "") + (it[10] ? '<span class="chip" data-tip="Run-flat: s defektem dojedete do servisu.">run-flat</span>' : "") +
        (it[11] !== "car" ? '<span class="chip">' + VEH[it[11]] + "</span>" : "") + eu;
      return '<li class="pc">' + pic + badge + '<div class="pc__b"><h3>' + (it[1] ? '<a href="' + it[1] + '">' + name + "</a>" : name) + '</h3><div class="chips">' + chips +
        '</div><div><span class="spz"><i>CZ</i><b>' + (it[4] > 1 ? "<small>od</small>" : "") + kc(it[3]) + '</b></span></div><p class="pc__n">' +
        (it[4] > 1 ? "v " + it[4] + " obchodech" : "v 1 obchodě") + "</p>" + cta + "</div></li>";
    };
    // filtered results: same 1 2 3 pager, but client-side (the static /strana-N/ links only describe the unfiltered list)
    var renderPager = function (pages) {
      if (!pager) { pager = document.createElement("nav"); pager.className = "pager"; pager.id = "pager"; pager.setAttribute("aria-label", "Stránkování"); list.parentNode.insertBefore(pager, list.nextSibling); }
      if (pages < 2) { pager.innerHTML = ""; return; }
      var ks = [1, pages, pg - 2, pg - 1, pg, pg + 1, pg + 2].filter(function (k, i, a) { return k >= 1 && k <= pages && a.indexOf(k) === i; }).sort(function (a, b) { return a - b; });
      var h = pg > 1 ? '<a class="pp" href="#" data-p="' + (pg - 1) + '" aria-label="Předchozí strana">‹</a>' : "", last = 0;
      ks.forEach(function (k) { if (k - last > 1) h += '<span class="gap">…</span>'; h += k === pg ? '<a class="on" aria-current="page">' + k + "</a>" : '<a href="#" data-p="' + k + '">' + k + "</a>"; last = k; });
      if (pg < pages) h += '<a class="pp" href="#" data-p="' + (pg + 1) + '" aria-label="Další strana">›</a>';
      pager.innerHTML = h;
    };
    list.parentNode.addEventListener("click", function (e) {
      var a = e.target.closest("#pager a[data-p]");
      if (!a) return;
      e.preventDefault(); pg = +a.getAttribute("data-p"); apply(false);
      list.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    var tierP = null, getTiers = function () { return tierP || (tierP = getJSON("/data/tiers.json")); };
    var on = function (k, attr) { return $$('.flinks a.on[data-f="' + k + '"]', form).map(function (a) { return a.getAttribute(attr); }); };
    // one test per facet, so the live counts can ask "how many would match if this facet were ignored"
    var state = function () {
      var f = new FormData(form);
      return { seasons: f.getAll("season"), veh: f.getAll("vehicle"), dims: on("dim", "data-v"), rims: f.getAll("rim"),
        bnames: f.getAll("brand").concat(on("brand", "data-n")), slugs: on("brand", "data-v"), tiers: f.getAll("tier"),
        xl: f.get("xl"), rf: f.get("rf"), multi: f.get("multi"), pmsf: f.get("pmsf"), ev: f.get("ev"), simin: f.get("si") || "",
        li: +(f.get("li") || 0), wg: f.get("wg") || "", rr: f.get("rr") || "", db: +(f.get("db") || 0),
        pmin: +(f.get("pmin") || 0), pmax: +(f.get("pmax") || 0) };
    };
    var rimOf = function (it) { var m = /-r(\d+(?:-5)?)$/.exec(it[14] || ""); return m ? m[1].replace("-", ".") : ""; };
    var tests = function (st, TI) {
      return {
        season: function (it) { return !st.seasons.length || st.seasons.indexOf(SEAS[it[5]]) >= 0; },
        vehicle: function (it) { return !st.veh.length || st.veh.indexOf(it[11]) >= 0; },
        rim: function (it) { return !st.rims.length || st.rims.indexOf(rimOf(it)) >= 0; },
        rest: function (it) {
          if (hub && hub.season && SEAS[it[5]] !== hub.season) return false;
          if (hub && hub.vehicle && it[11] !== hub.vehicle) return false;
          if (st.bnames.length && st.bnames.indexOf(it.bn) < 0) return false;
          if (st.dims.length && st.dims.indexOf(it[14]) < 0) return false;
          if (st.xl && !it[9]) return false;
          if (st.rf && !it[10]) return false;
          if (st.pmsf && !it[17]) return false;
          if (st.ev && !it[18]) return false;
          if (st.multi && it[4] < 2) return false;
          if (st.simin && SI.indexOf(it[8]) < SI.indexOf(st.simin)) return false;
          if (st.li && !(parseInt(it[7], 10) >= st.li)) return false;
          if (st.pmin && it[3] < st.pmin) return false;
          if (st.pmax && it[3] > st.pmax) return false;
          if ((st.wg || st.rr || st.db) && !it[12]) return false;
          if (st.wg && it[12][1] > st.wg) return false;
          if (st.rr && it[12][0] > st.rr) return false;
          if (st.db && +it[12].slice(2) > st.db) return false;
          if (st.tiers.length && st.tiers.indexOf(TI[it.bn] || "b") < 0) return false;
          return true;
        }
      };
    };
    var FACETS = ["season", "vehicle", "rim"];
    var liveCounts = function (items, T) {
      FACETS.forEach(function (k) {
        var cnt = {};
        items.forEach(function (it) {
          if (!T.rest(it)) return;
          for (var i = 0; i < FACETS.length; i++) if (FACETS[i] !== k && !T[FACETS[i]](it)) return;
          var v = k === "season" ? SEAS[it[5]] : k === "vehicle" ? it[11] : rimOf(it);
          cnt[v] = (cnt[v] || 0) + 1;
        });
        $$('.count[data-c^="' + k + ':"]', form).forEach(function (c) {
          var n = cnt[c.getAttribute("data-c").slice(k.length + 1)] || 0;
          c.textContent = n; c.parentNode.classList.toggle("zero", !n);
        });
      });
    };
    // active filters as removable chips + the same state in the URL (shareable, survives reload)
    var chipsEl = null;
    var activeList = function () {
      var out = [];
      $$("input[type=checkbox]:checked", form).forEach(function (i) {
        var lb = i.closest("label"), t = i.value;
        if (lb) { var cl = lb.cloneNode(true); $$(".count", cl).forEach(function (c) { c.remove(); }); t = cl.textContent.trim(); }
        out.push({ t: t, off: function () { i.checked = false; } });
      });
      $$("select", form).forEach(function (sl) {
        if (!sl.value) return;
        var lg = sl.closest("label") ? sl.closest("label").querySelector("span") : sl.closest("fieldset").querySelector("legend");
        out.push({ t: (lg ? lg.textContent + ": " : "") + sl.options[sl.selectedIndex].text, off: function () { sl.value = ""; } });
      });
      var pmin = form.elements.pmin, pmax = form.elements.pmax;
      if (pmin && pmax && (pmin.value || pmax.value)) out.push({ t: "Cena " + (pmin.value ? "od " + pmin.value + " " : "") + (pmax.value ? "do " + pmax.value + " " : "") + "Kč",
        off: function () { pmin.value = ""; pmax.value = ""; } });
      $$(".flinks a.on", form).forEach(function (a) { out.push({ t: a.getAttribute("data-n"), off: function () { a.classList.remove("on"); } }); });
      return out;
    };
    var renderChips = function () {
      var act = activeList();
      if (!chipsEl) { chipsEl = document.createElement("div"); chipsEl.className = "fchips"; list.parentNode.insertBefore(chipsEl, list); }
      chipsEl.innerHTML = act.map(function (a, i) { return '<button type="button" class="fchip" data-i="' + i + '">' + esc(a.t) + ' <b aria-hidden="true">×</b></button>'; }).join("") +
        (act.length > 1 ? '<button type="button" class="fchip fchip--all">Zrušit vše</button>' : "");
      chipsEl._act = act;
    };
    var sort0 = sortSel ? sortSel.value : "";
    var syncURL = function () {
      if (!history.replaceState) return;
      var q = new URLSearchParams();
      $$("input[type=checkbox]:checked", form).forEach(function (i) { q.append(i.name, i.value); });
      $$("select, input[type=number]", form).forEach(function (i) { if (i.value && i.name) q.set(i.name, i.value); });
      $$(".flinks a.on", form).forEach(function (a) { q.append(a.getAttribute("data-f"), a.getAttribute("data-v")); });
      if (sortSel && sortSel.value !== sort0) q.set("sort", sortSel.value);
      var s = q.toString();
      history.replaceState(null, "", location.pathname + (s ? "?" + s : "") + location.hash);
    };
    var restoreURL = function () {
      var q = new URLSearchParams(location.search), any = false;
      if (q.get("trida")) q.append("tier", q.get("trida"));
      q.forEach(function (v, k) {
        var vv = v.replace(/"/g, "");
        var el = $('input[type=checkbox][name="' + k + '"][value="' + vv + '"]', form);
        if (el) { el.checked = true; any = true; return; }
        var a = $('.flinks a[data-f="' + k + '"][data-v="' + vv + '"]', form);
        if (a) { a.classList.add("on"); any = true; return; }
        if (k === "sort" && sortSel) { sortSel.value = v; any = true; return; }
        var x = form.elements[k];
        if (x && (x.tagName === "SELECT" || x.type === "number")) { x.value = v; any = true; }
      });
      return any;
    };
    var apply = function (reset) {
      if (reset) pg = 1;
      var st = state();
      Promise.all([source(st.dims, st.slugs), st.tiers.length ? getTiers() : null]).then(function (r) {
        var src = r[0], T = tests(st, r[1] || {});
        var items = src.items.filter(function (it) { return T.rest(it) && T.season(it) && T.vehicle(it) && T.rim(it); });
        var s = sortSel ? sortSel.value : "pop";
        items.sort(s === "price" ? function (a, b) { return a[3] - b[3]; } : s === "pricedesc" ? function (a, b) { return b[3] - a[3]; }
          : s === "save" ? function (a, b) { return (b[13] - a[13]) || (a[3] - b[3]); }
          : function (a, b) { return (b[4] - a[4]) || (a[3] - b[3]); });
        var pages = Math.max(1, Math.ceil(items.length / PER));
        if (pg > pages) pg = pages;
        list.innerHTML = items.length ? items.slice((pg - 1) * PER, pg * PER).map(row).join("") : '<li class="card empty">Tomu filtru nic neodpovídá. Zkuste některou volbu vypnout.</li>';
        renderPager(pages);
        if (countEl) countEl.textContent = items.length.toLocaleString("cs-CZ");
        if (note) note.hidden = src.full;
        if (src.full) liveCounts(src.items, T);
        renderChips(); syncURL();
        var rs = $(".freset", form);
        if (rs) rs.hidden = !activeList().length;
      });
    };
    list.parentNode.addEventListener("click", function (e) {
      var c = e.target.closest(".fchip");
      if (!c || !chipsEl) return;
      if (c.classList.contains("fchip--all")) chipsEl._act.forEach(function (a) { a.off(); });
      else chipsEl._act[+c.getAttribute("data-i")].off();
      apply(true);
    });
    form && form.addEventListener("change", function (e) { if (!e.target.classList.contains("fsearch")) apply(true); });
    var numT;
    form && form.addEventListener("input", function (e) { if (e.target.type === "number") { clearTimeout(numT); numT = setTimeout(function () { apply(true); }, 500); } });
    // link filters: real links for crawlers, toggles for people
    form && form.addEventListener("click", function (e) {
      var a = e.target.closest(".flinks a");
      if (a) { e.preventDefault(); a.classList.toggle("on"); apply(true); if (window.innerWidth < 980) list.scrollIntoView({ behavior: "smooth", block: "start" }); return; }
      if (e.target.closest(".freset")) { form.reset(); $$(".flinks a.on", form).forEach(function (x) { x.classList.remove("on"); }); apply(true); }
    });
    var norm = function (x) { return fold(x).replace(/[^0-9a-z]/g, "").replace(/r(?=\d)/g, ""); };
    $$(".fsearch", form).forEach(function (inp) {
      inp.addEventListener("input", function () {
        var q = norm(inp.value), box = inp.nextElementSibling;
        $$("a", box).forEach(function (a) { a.style.display = !q || norm(a.getAttribute("data-n") || "").indexOf(q) >= 0 ? "" : "none"; });
      });
    });
    sortSel && sortSel.addEventListener("change", function () { apply(true); });
    var ft = $(".ftoggle"), fc = $(".fclose", form);
    var sheet = function (on) { form.classList.toggle("open", on); document.documentElement.classList.toggle("noscroll", on && innerWidth < 981);
      if (fc) fc.textContent = "Zobrazit " + ($("#count") ? $("#count").textContent : "") + " pneumatik"; };
    ft && ft.addEventListener("click", function () { sheet(!form.classList.contains("open")); });
    fc && fc.addEventListener("click", function () { sheet(false); $("#plist").scrollIntoView({ block: "start" }); });
    form.addEventListener("change", function () { if (fc) fc.textContent = "Zobrazit " + ($("#count") ? $("#count").textContent : "") + " pneumatik"; });
    if ($("#fbrands")) buildBrands();
    var restored = form && restoreURL();
    if (location.hash && form) {
      var h = $('input[name=season][value="' + location.hash.slice(1) + '"]', form);
      if (h) { h.checked = true; restored = true; }
    }
    if (restored) apply(true);
    if (!list.dataset.lazy && "requestIdleCallback" in window) requestIdleCallback(function () { base(); });
  }

  /* ---------- chip explanations: hover = native title, tap = small toast at the bottom ---------- */
  var tipEl;
  document.addEventListener("mouseover", function (e) { var c = e.target.closest && e.target.closest(".chip[data-tip]"); if (c && !c.title) c.title = c.getAttribute("data-tip"); });
  document.addEventListener("click", function (e) {
    var c = e.target.closest && e.target.closest(".chip[data-tip]");
    if (!c) return;
    if (!tipEl) { tipEl = document.createElement("div"); tipEl.className = "tip"; tipEl.setAttribute("role", "status"); document.body.appendChild(tipEl); }
    tipEl.textContent = c.getAttribute("data-tip"); tipEl.classList.add("on");
    clearTimeout(tipEl._t); tipEl._t = setTimeout(function () { tipEl.classList.remove("on"); }, 4000);
  });

  /* ---------- 3-step wizard ---------- */
  var wiz = $("#wiz");
  if (wiz) {
    var ww = $("#ww"), wp = $("#wp"), wr = $("#wr"), wc = $("#wc"), wcs = $("#wcs"), DIMS = {};
    getJSON("/data/dims.json").then(function (dims) {
      DIMS = dims;
      var keys = Object.keys(dims), uniq = function (a) { return a.filter(function (v, i) { return a.indexOf(v) === i; }).sort(function (x, y) { return parseFloat(x) - parseFloat(y); }); };
      var opt = function (sel, vals, cur) { sel.innerHTML = vals.map(function (v) { return "<option" + (v === cur ? " selected" : "") + ">" + v + "</option>"; }).join(""); };
      var fill = function (l) {
        if (l < 1) opt(ww, uniq(keys.map(function (k) { return dims[k][0]; })), ww.value || "205");
        if (l < 2) opt(wp, uniq(keys.filter(function (k) { return dims[k][0] === ww.value; }).map(function (k) { return dims[k][1]; })), wp.value || "55");
        opt(wr, uniq(keys.filter(function (k) { return dims[k][0] === ww.value && dims[k][1] === wp.value; }).map(function (k) { return dims[k][2]; })), wr.value || "16");
      };
      fill(0);
      ww.addEventListener("change", function () { wp.value = ""; wr.value = ""; fill(1); });
      wp.addEventListener("change", function () { wr.value = ""; fill(2); });
    });
    $$("input[name=how]", wiz).forEach(function (r) { r.addEventListener("change", function () { var car = r.value === "car" && r.checked; $(".wiz__size", wiz).hidden = car; $(".wiz__car", wiz).hidden = !car; }); });
    wc.addEventListener("change", function () {
      var o = wc.options[wc.selectedIndex], ss = (o && o.getAttribute("data-s") || "").split("|").filter(Boolean);
      wcs.innerHTML = ss.length ? ss.map(function (x, i) { var p = x.split(":"); return '<label class="opt"><input type="radio" name="cdim" value="' + p[0] + '"' + (i ? "" : " checked") + "><b>" + p[1] + "</b></label>"; }).join("")
        : '<p class="muted">Pro tohle auto zatím nemáme nabídky v továrním rozměru.</p>';
    });
    wiz.addEventListener("submit", function () {
      var f = new FormData(wiz), d = f.get("how") === "car" ? f.get("cdim") : ww.value + "-" + wp.value + "-r" + (wr.value || "").replace(".", "-");
      var s = f.get("season") || "", t = f.get("tier") || "", err = $(".wiz__err", wiz);
      if (!d || !DIMS[d]) { err.hidden = false; return; }
      err.hidden = true;
      location.href = "/pneumatiky/" + d + "/" + (s && DIMS[d][4] && DIMS[d][4].indexOf(s) >= 0 ? s + "/" : "") + (t ? "?trida=" + t : "") + (s && !(DIMS[d][4] && DIMS[d][4].indexOf(s) >= 0) ? "#" + ({ letni: "summer", zimni: "winter", celorocni: "all" })[s] : "");
    });
  }

  /* ---------- tyres by car: filter the car list as you type ---------- */
  var cs = $(".carsearch");
  cs && cs.addEventListener("input", function () {
    var q = fold(cs.value).trim();
    $$(".carlist .make").forEach(function (sec) {
      var any = false;
      $$("li", sec).forEach(function (li) { var a = li.querySelector("a"); var ok = !q || fold(sec.querySelector("h2").textContent + " " + (a.getAttribute("data-n") || a.textContent)).indexOf(q) >= 0; li.style.display = ok ? "" : "none"; any = any || ok; });
      sec.style.display = any ? "" : "none";
    });
  });

  /* ---------- product: sticky buy bar once the price box scrolls away (mobile) ---------- */
  var bb = $("#buybar"), bx = $("#bestbox");
  if (bb && bx && "IntersectionObserver" in window) {
    bb.hidden = false;
    new IntersectionObserver(function (e) { bb.classList.toggle("on", !e[0].isIntersecting && e[0].boundingClientRect.top < 0); }).observe(bx);
  }

  /* ---------- consent + GA4 (loaded only after consent) ---------- */
  var gaId = (document.querySelector('meta[name="ga4"]') || {}).content;
  var consent = $("#consent");
  var startGA = function () {
    if (!gaId || window.gtag) return;
    var s = document.createElement("script"); s.async = true; s.src = "https://www.googletagmanager.com/gtag/js?id=" + gaId; document.head.appendChild(s);
    window.dataLayer = window.dataLayer || [];
    window.gtag = function () { dataLayer.push(arguments); };
    gtag("js", new Date()); gtag("config", gaId, { anonymize_ip: true });
  };
  var c = store("auty-consent");
  if (c === "yes") startGA();
  else if (c !== "no" && consent) consent.style.display = "block";
  $$("[data-consent]").forEach(function (b) {
    b.addEventListener("click", function () {
      var yes = b.dataset.consent === "yes"; store("auty-consent", yes ? "yes" : "no");
      if (consent) consent.style.display = "none"; if (yes) startGA();
    });
  });
  var reopen = $("#consent-open"); reopen && reopen.addEventListener("click", function (e) { e.preventDefault(); if (consent) consent.style.display = "block"; });
  document.addEventListener("click", function (e) {
    var a = e.target.closest("a[data-go]");
    if (a && window.gtag) gtag("event", "affiliate_click", { shop: a.dataset.go, product: document.title, transport_type: "beacon" });
  });
})();
// wheel listing sidebar: brand search box
document.querySelectorAll(".wsearch").forEach(function (inp) {
  inp.addEventListener("input", function () {
    var q = inp.value.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").trim();
    inp.nextElementSibling.querySelectorAll("a").forEach(function (a) {
      a.style.display = !q || a.textContent.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").indexOf(q) >= 0 ? "" : "none";
    });
  });
});

// wheel listings: client-side facets (width, offset, centre bore, price, sort) over /data/w/<listing>.json
(function () {
  var list = document.getElementById("wlist"), form = document.getElementById("wff");
  if (!list || !form) return;
  var more = form.closest("details");
  if (more && (window.innerWidth > 980 || location.search)) more.open = true;
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var esc = function (s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
  var kc = function (n) { return Math.round(n).toLocaleString("cs-CZ").replace(/\s/g, " ") + " Kč"; };
  var sortSel = document.getElementById("wsort"), countEl = document.getElementById("wcount");
  var staticHTML = list.innerHTML, staticPager = list.parentNode.querySelector(".pager"), total = countEl ? countEl.textContent : "";
  var data = null, pg = 1, PER = 24, pager = null;
  var load = function () { return data ? Promise.resolve(data) : fetch(list.dataset.json).then(function (r) { return r.json(); }).then(function (d) { data = d; return d; }); };
  var card = function (it, shops) {
    var img = esc(it[2] || "/img/ic/disk.webp");
    return '<li class="pc wc"><span class="pc__img"><img src="' + img + '" alt="" loading="lazy" width="180" height="150"></span><div class="pc__b">' +
      "<h3>" + esc(it[0]) + '</h3><p class="wspec">' + esc(it[1]) + '</p><div><span class="spz"><i>CZ</i><b>' + (it[4] > 1 ? "<small>od</small>" : "") + kc(it[3]) + "</b></span></div>" +
      '<p class="pc__n">' + (shops[it[6]] || "") + (it[4] > 1 ? " v " + it[4] + " obchodech" : "") + "</p>" +
      '<a class="btn" href="' + esc(it[5]) + '" rel="sponsored nofollow noopener" target="_blank" data-go="' + esc(it[6]) + '">Do obchodu</a></div></li>';
  };
  var st = function () {
    var f = new FormData(form), n = function (k) { var v = f.get(k); return v === null || v === "" ? null : +v; };
    return { w: f.getAll("w").map(function (x) { return +x.replace(",", "."); }), etmin: n("etmin"), etmax: n("etmax"), cb: n("cb"), pmin: n("pmin"), pmax: n("pmax"),
      sort: sortSel ? sortSel.value : "price" };
  };
  var active = function (s) { return s.w.length || s.etmin !== null || s.etmax !== null || s.cb !== null || s.pmin !== null || s.pmax !== null || s.sort !== "price"; };
  var renderPager = function (pages) {
    if (!pager) { pager = document.createElement("nav"); pager.className = "pager"; pager.setAttribute("aria-label", "Stránkování"); list.parentNode.insertBefore(pager, list.nextSibling); }
    if (pages < 2) { pager.innerHTML = ""; return; }
    var ks = [1, pages, pg - 2, pg - 1, pg, pg + 1, pg + 2].filter(function (k, i, a) { return k >= 1 && k <= pages && a.indexOf(k) === i; }).sort(function (a, b) { return a - b; });
    var h = "", last = 0;
    ks.forEach(function (k) { if (k - last > 1) h += '<span class="gap">…</span>'; h += k === pg ? '<a class="on" aria-current="page">' + k + "</a>" : '<a href="#" data-p="' + k + '">' + k + "</a>"; last = k; });
    pager.innerHTML = h;
  };
  var apply = function (reset) {
    if (reset) pg = 1;
    var s = st(), rs = form.querySelector(".wreset");
    var q = new URLSearchParams();
    s.w.forEach(function (w) { q.append("w", String(w).replace(".", ",")); });
    ["etmin", "etmax", "cb", "pmin", "pmax"].forEach(function (k) { if (s[k] !== null) q.set(k, s[k]); });
    if (s.sort !== "price") q.set("sort", s.sort);
    if (history.replaceState) history.replaceState(null, "", location.pathname + (q.toString() ? "?" + q : ""));
    if (rs) rs.hidden = !active(s);
    if (!active(s)) {
      list.innerHTML = staticHTML; if (staticPager) staticPager.hidden = false; if (pager) pager.innerHTML = ""; if (countEl) countEl.textContent = total; return;
    }
    load().then(function (d) {
      var items = d.items.filter(function (it) {
        if (s.w.length && s.w.indexOf(it[7]) < 0) return false;
        if (s.etmin !== null && !(it[10] !== null && it[10] >= s.etmin)) return false;
        if (s.etmax !== null && !(it[10] !== null && it[10] <= s.etmax)) return false;
        if (s.cb !== null && !(it[11] && it[11] >= s.cb - 0.05)) return false;
        if (s.pmin !== null && it[3] < s.pmin) return false;
        if (s.pmax !== null && it[3] > s.pmax) return false;
        return true;
      });
      var by = { price: function (a, b) { return a[3] - b[3]; }, pricedesc: function (a, b) { return b[3] - a[3]; },
        et: function (a, b) { return ((a[10] === null ? 999 : a[10]) - (b[10] === null ? 999 : b[10])) || a[3] - b[3]; }, w: function (a, b) { return (a[7] - b[7]) || a[3] - b[3]; } };
      items.sort(by[s.sort] || by.price);
      var pages = Math.max(1, Math.ceil(items.length / PER));
      if (pg > pages) pg = pages;
      list.innerHTML = items.length ? items.slice((pg - 1) * PER, pg * PER).map(function (it) { return card(it, d.shops); }).join("")
        : '<li class="card empty">Tomu filtru nic neodpovídá. Zkuste některou volbu vypnout.</li>';
      if (staticPager) staticPager.hidden = true;
      renderPager(pages);
      if (countEl) countEl.textContent = items.length.toLocaleString("cs-CZ");
    });
  };
  var t;
  form.addEventListener("change", function () { apply(true); });
  form.addEventListener("input", function (e) { if (e.target.type === "number") { clearTimeout(t); t = setTimeout(function () { apply(true); }, 500); } });
  sortSel && sortSel.addEventListener("change", function () { apply(true); });
  form.querySelector(".wreset") && form.querySelector(".wreset").addEventListener("click", function () { form.reset(); if (sortSel) sortSel.value = "price"; apply(true); });
  list.parentNode.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest(".pager a[data-p]");
    if (!a || !pager || !pager.contains(a)) return;
    e.preventDefault(); pg = +a.getAttribute("data-p"); apply(false); list.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  // restore from the URL
  var q = new URLSearchParams(location.search), any = false;
  q.forEach(function (v, k) {
    if (k === "w") { var c = form.querySelector('input[name=w][value="' + v.replace(/"/g, "") + '"]'); if (c) { c.checked = true; any = true; } return; }
    if (k === "sort" && sortSel) { sortSel.value = v; any = true; return; }
    if (form.elements[k]) { form.elements[k].value = v; any = true; }
  });
  if (any) apply(true);
})();
