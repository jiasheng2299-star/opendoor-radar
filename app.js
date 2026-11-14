"use strict";

const FILES = ["briefing", "quote", "accountable", "filings", "insiders", "holders", "short", "news", "status"];

const FORM4_CODES = {
  P: "open-market buy",
  S: "open-market sell",
  A: "grant or award",
  M: "exercise or conversion",
  F: "tax withholding",
  G: "gift",
  D: "disposition to the issuer",
  C: "conversion",
  J: "other",
  V: "voluntary transaction",
  L: "small acquisition",
  W: "inheritance",
  I: "discretionary exercise",
  X: "exercise or conversion",
};

const CATEGORY_LABELS = {
  price: "Price",
  filings: "SEC filings",
  insiders: "Insider trades",
  accountable: "Accountable",
  short: "Short interest",
  news: "News",
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function safeUrl(url) {
  if (typeof url !== "string") return null;
  if (url.startsWith("https://") || url.startsWith("http://")) return url;
  return null;
}

function link(url, label, className) {
  const href = safeUrl(url);
  if (!href) return el("span", className, label || "No data");
  const a = el("a", className, label || href);
  a.href = href;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  return a;
}

function formatMYT(iso) {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kuala_Lumpur",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const get = (type) => parts.find((part) => part.type === type).value;
  return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")} MYT`;
}

function num(value, digits) {
  if (value == null || !Number.isFinite(Number(value))) return null;
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(Number(value));
}

function price(value) {
  if (value == null || !Number.isFinite(Number(value))) return "No data";
  const abs = Math.abs(Number(value));
  const digits = abs >= 100 ? 2 : abs >= 1 ? 3 : 4;
  let text = Number(value).toFixed(digits).replace(/0+$/, "").replace(/\.$/, "");
  const [whole, frac = ""] = text.split(".");
  const padded = (frac + "00").slice(0, Math.max(2, frac.length));
  return `$${Number(whole).toLocaleString("en-US")}.${padded}`;
}

function fmtPct(value) {
  if (value == null || !Number.isFinite(Number(value))) return "No data";
  const n = Number(value);
  const oneDecimal = Math.abs(n * 10 - Math.round(n * 10)) < 1e-6;
  return `${num(n, oneDecimal ? 1 : 2)}%`;
}

function signed(value, digits) {
  if (value == null || !Number.isFinite(Number(value))) return "No data";
  const n = Number(value);
  const body = num(Math.abs(n), digits);
  if (n > 0) return `+${body}`;
  if (n < 0) return `-${body}`;
  return body;
}

function direction(value) {
  if (value == null || !Number.isFinite(Number(value)) || Number(value) === 0) return "flat";
  return Number(value) > 0 ? "up" : "down";
}

function addBanner(parent, data) {
  const banner = staleBanner(data);
  if (banner) parent.append(banner);
}

function staleBanner(data, noun) {
  if (!data || !data.stale) return null;
  const when = formatMYT(data.fetched_at);
  const tried = formatMYT(data.fetch_attempted_at);
  const banner = el("p", "banner");
  banner.textContent = `No data (this update failed${tried ? ", tried " + tried : ""}). Showing the last successful values${when ? ", updated " + when : ""}.`;
  if (noun) banner.dataset.section = noun;
  return banner;
}

function metaRow(url, label, fetchedAt) {
  const p = el("p", "meta");
  p.append("Source: ");
  p.append(link(url, label || "link"));
  const when = formatMYT(fetchedAt);
  p.append(` · Updated ${when || "No data"}`);
  return p;
}

function section(id, title, kicker) {
  const card = el("section", "card");
  card.id = id;
  card.append(el("h2", null, title));
  if (kicker) card.append(el("p", "kicker", kicker));
  return card;
}

function emptyNote(text) {
  return el("p", "empty", text || "No data");
}

function drawChart(host, series, yFormat) {
  host.replaceChildren();
  const usable = series
    .map((item) => ({ ...item, points: item.points.filter((point) => point.y != null && Number.isFinite(point.y)) }))
    .filter((item) => item.points.length);
  if (!usable.length) {
    host.append(emptyNote());
    return;
  }
  const w = 360;
  const h = 176;
  const pad = { l: 46, r: 10, t: 12, b: 24 };
  const ys = usable.flatMap((item) => item.points.map((point) => point.y));
  let min = Math.min(...ys);
  let max = Math.max(...ys);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const slack = (max - min) * 0.1;
  min -= slack;
  max += slack;
  const xs = [...new Set(usable.flatMap((item) => item.points.map((point) => point.x)))].sort();
  const xAt = (x) => {
    const index = xs.indexOf(x);
    if (xs.length === 1) return pad.l + (w - pad.l - pad.r) / 2;
    return pad.l + (index / (xs.length - 1)) * (w - pad.l - pad.r);
  };
  const yAt = (y) => pad.t + ((max - y) / (max - min)) * (h - pad.t - pad.b);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", host.dataset.label || "Chart");
  const ticks = [max, (max + min) / 2, min];
  ticks.forEach((tick) => {
    const y = yAt(tick);
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", pad.l);
    line.setAttribute("x2", w - pad.r);
    line.setAttribute("y1", y);
    line.setAttribute("y2", y);
    line.setAttribute("stroke", "#e6eef1");
    svg.append(line);
    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.setAttribute("x", pad.l - 6);
    label.setAttribute("y", y + 3);
    label.setAttribute("text-anchor", "end");
    label.setAttribute("fill", "#6b7c84");
    label.setAttribute("font-size", "10");
    label.textContent = yFormat(tick);
    svg.append(label);
  });
  usable.forEach((item) => {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", item.color);
    path.setAttribute("stroke-width", "2");
    path.setAttribute("stroke-linejoin", "round");
    path.setAttribute("stroke-linecap", "round");
    if (item.dash) path.setAttribute("stroke-dasharray", item.dash);
    path.setAttribute("points", item.points.map((point) => `${xAt(point.x)},${yAt(point.y)}`).join(" "));
    svg.append(path);
  });
  [xs[0], xs[xs.length - 1]].forEach((x, index) => {
    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.setAttribute("x", xAt(x));
    label.setAttribute("y", h - 6);
    label.setAttribute("text-anchor", index === 0 ? "start" : "end");
    label.setAttribute("fill", "#6b7c84");
    label.setAttribute("font-size", "10");
    label.textContent = x.slice(5);
    svg.append(label);
  });
  host.append(svg);
  if (usable.length > 1 || usable[0].name) {
    const legend = el("p", "legend");
    usable.forEach((item) => {
      const span = el("span");
      const swatch = el("i", "swatch");
      swatch.style.background = item.color;
      span.append(swatch, item.name);
      legend.append(span);
    });
    host.append(legend);
  }
}

function renderBulletGroups(bullets) {
  const wrap = el("div", "brief-groups");
  let current = null;
  let group = null;
  (bullets || []).forEach((item) => {
    const category = item.category || "note";
    if (category !== current) {
      current = category;
      group = el("div", "brief-group");
      if (CATEGORY_LABELS[category]) group.append(el("h3", null, CATEGORY_LABELS[category]));
      wrap.append(group);
    }
    const line = el("p", "brief-item");
    const href = safeUrl(item.href);
    if (href) line.append(link(href, item.text || href));
    else line.textContent = item.text || "No data";
    group.append(line);
  });
  return wrap;
}

async function loadJson(url) {
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) return null;
    return await response.json();
  } catch (error) {
    return null;
  }
}

async function renderBriefing(data) {
  const card = section("briefing", "Daily briefing", "Built automatically at each 08:00 MYT update from the public files");
  if (!data) return card.append(emptyNote()), card;
  if (data.placeholder) {
    card.append(el("p", null, (data.bullets && data.bullets[0] && data.bullets[0].text) || "No data"));
    card.append(el("p", "meta", "Source: site placeholder · Updated not generated yet (next 08:00 MYT)"));
    return card;
  }
  addBanner(card, data);
  if (data.date) card.append(el("p", "kicker", `Briefing date ${data.date}`));
  if (!data.bullets || !data.bullets.length) card.append(emptyNote());
  else card.append(renderBulletGroups(data.bullets));
  card.append(metaRow(data.source_url, data.source_label || "This site's fetch", data.updated_at || data.fetched_at));

  const index = await loadJson("data/briefings/index.json");
  const dates = ((index && index.dates) || []).filter((date) => date !== data.date).slice(0, 7);
  const older = [];
  for (const date of dates) {
    const archived = await loadJson(`data/briefings/${date}.json`);
    if (archived) older.push({ date, data: archived });
  }
  card.append(el("h3", null, "Earlier briefings"));
  if (!older.length) {
    card.append(el("p", "meta", "No earlier briefings yet."));
  } else {
    older.forEach((row) => {
      const details = el("details", "more");
      details.append(el("summary", null, row.date));
      details.append(renderBulletGroups(row.data.bullets || []));
      card.append(details);
    });
  }
  return card;
}

function renderQuote(data) {
  const card = section("quote", "Price", "Last completed session, not an intraday quote");
  if (!data || !data.ok) return card.append(emptyNote()), card;
  addBanner(card, data);
  card.append(el("p", "price", price(data.last_close)));
  const row = el("div", "row");
  const change = el("span", `chg ${direction(data.change)}`, `${signed(data.change, 3)} (${signed(data.change_pct, 2)}%)`);
  row.append(change);
  card.append(row);
  const stats = el("div", "stats");
  const cells = [
    ["Session date", data.session_date || "No data"],
    ["Prior close", price(data.previous_close)],
    ["Volume", data.volume == null ? "No data" : num(data.volume, 0)],
    ["Currency", data.currency || "USD"],
  ];
  cells.forEach(([label, value]) => {
    const box = el("div", "stat");
    box.append(el("span", null, label), el("strong", null, String(value)));
    stats.append(box);
  });
  card.append(stats);
  const chart = el("div", "chart");
  chart.dataset.label = "About six months of closing prices";
  drawChart(chart, [{
    name: "Close",
    color: "#0f6e6b",
    points: (data.points || []).map((point) => ({ x: point.date, y: point.close })),
  }], (value) => `$${value.toFixed(2)}`);
  card.append(chart);
  if (data.fallback_error) card.append(el("p", "note", `The preferred source was unavailable this time. Using ${data.source_label}.`));
  card.append(metaRow(data.source_url, data.source_label, data.fetched_at));
  return card;
}

function renderAccountable(data) {
  const card = section("accountable", "Opendoor Accountable", "Official weekly acquisition contracts");
  if (!data || !data.ok) return card.append(emptyNote()), card;
  addBanner(card, data);
  card.append(el("p", "note", data.contracts_label || "Contracts are agreements to buy, not completed purchases."));
  const weeks = data.weeks || [];
  const latest = weeks[weeks.length - 1];
  if (latest) {
    const stats = el("div", "stats");
    [["Latest week", latest.week_ending], ["Contracts", num(latest.contracts, 0)], ["Vs prior week", latest.wow == null ? "—" : signed(latest.wow, 0)], ["Prior year", latest.prior_year == null ? "No data" : num(latest.prior_year, 0)]]
      .forEach(([label, value]) => {
        const box = el("div", "stat");
        box.append(el("span", null, label), el("strong", null, String(value)));
        stats.append(box);
      });
    card.append(stats);
  }
  const chart = el("div", "chart");
  chart.dataset.label = "Weekly acquisition contracts";
  drawChart(chart, [
    { name: "Contracts", color: "#0f6e6b", points: weeks.map((row) => ({ x: row.week_ending, y: row.contracts })) },
    { name: "Prior year", color: "#98a8b0", dash: "4 3", points: weeks.map((row) => ({ x: row.week_ending, y: row.prior_year })) },
  ], (value) => num(value, 0));
  card.append(chart);
  const table = el("table");
  table.append(el("caption", "kicker", "Vs prior week is the difference between two adjacent published weeks."));
  const head = el("tr");
  ["Week ending", "Contracts", "Vs prior week", "Prior year"].forEach((label) => head.append(el("th", null, label)));
  table.append(head);
  weeks.slice().reverse().slice(0, 16).forEach((row) => {
    const tr = el("tr");
    tr.append(el("td", null, row.week_ending));
    tr.append(el("td", null, num(row.contracts, 0)));
    const wow = el("td", direction(row.wow), row.wow == null ? "—" : signed(row.wow, 0));
    tr.append(wow);
    tr.append(el("td", null, row.prior_year == null ? "—" : num(row.prior_year, 0)));
    table.append(tr);
  });
  const wrap = el("div", "scroll");
  wrap.append(table);
  card.append(wrap);
  if ((weeks.length || 0) > 16) card.append(el("p", "meta", `The table shows the latest 16 weeks. The chart shows all ${weeks.length} published weeks.`));
  if (data.official_note) card.append(el("p", "note", `Official wording: ${data.official_note}`));
  const cross = data.crosscheck;
  if (cross && cross.ok && cross.latest) {
    const crossLine = el("p", "meta");
    crossLine.append(`Cross-check ${cross.latest.week_ending} = ${num(cross.latest.contracts, 0)}. ${cross.matches_accountable ? "It matches the official board." : "It does not match; the page still uses the official board."} Source: `);
    crossLine.append(link(cross.source_url, "opendoor.army"));
    card.append(crossLine);
  } else {
    const crossLine = el("p", "meta");
    crossLine.append("Cross-check: No data. Official figures were not changed. Source: ");
    crossLine.append(link(ARMY_URL, "opendoor.army"));
    card.append(crossLine);
  }
  card.append(metaRow(data.source_url, data.source_label, data.fetched_at));
  card.append(el("h3", null, "Product, feature, and partnership launches"));
  const launches = data.launches || [];
  if (!launches.length) card.append(emptyNote());
  else {
    const box = el("div", "launches");
    launches.forEach((item) => {
      const block = el("article", "launch");
      block.append(el("h3", null, item.title || "No data"));
      const line = el("p", "subline");
      if (item.tag) line.append(el("span", "tag", item.tag), " · ");
      line.append(item.date || item.date_raw || "No data");
      block.append(line);
      if (item.description) block.append(el("p", "subline", item.description));
      box.append(block);
    });
    card.append(box);
    card.append(el("p", "meta", `${launches.length} items, same source and update as the contracts above.`));
  }
  return card;
}

const ARMY_URL = "https://opendoor.army/charts/weekly";
const FORM_GLOSS = {
  "10-Q": "Quarterly report",
  "10-Q/A": "Amended quarterly report",
  "10-K": "Annual report",
  "10-K/A": "Amended annual report",
  "8-K": "Current report",
  "8-K/A": "Amended current report",
  "S-3": "Securities registration",
  "S-3ASR": "Automatic shelf registration",
  "DEF 14A": "Proxy statement",
};

function renderFilings(data) {
  const card = section("filings", "Company filings", "Latest SEC EDGAR filings. 8-K, 10-Q, 10-K, S-3, 424B, and DEF 14A are highlighted.");
  if (!data || !data.ok) return card.append(emptyNote()), card;
  addBanner(card, data);
  (data.filings || []).forEach((row) => {
    const block = el("article", "filing");
    const title = el("h3");
    if (row.highlight) title.append(el("span", "pill hot", row.form || "Form"));
    else title.append(el("span", "pill flat", row.form || "Form"));
    title.append(" ");
    let desc = row.description || row.form || "Filing";
    if (FORM_GLOSS[row.form] && (desc === row.form || desc === "")) desc = FORM_GLOSS[row.form];
    title.append(link(row.url, desc));
    block.append(title);
    block.append(el("p", "subline", `Filed ${row.filing_date || "No data"}${row.report_date ? " · Period " + row.report_date : ""}`));
    card.append(block);
  });
  if (!data.filings || !data.filings.length) card.append(emptyNote());
  card.append(el("p", "note", data.note || ""));
  card.append(metaRow(data.source_url, data.source_label, data.fetched_at));
  return card;
}

function planLabel(flag) {
  if (flag === "yes") return "10b5-1: yes (box checked)";
  if (flag === "footnote") return "10b5-1: mentioned in a footnote";
  if (flag === "no") return "10b5-1: no (box not checked)";
  return "10b5-1: No data";
}

function renderInsiders(data) {
  const card = section("insiders", "Insider trades", "Recent Form 4 filings. Names and titles are shown as filed.");
  if (!data || !data.ok) return card.append(emptyNote()), card;
  addBanner(card, data);
  let shown = 0;
  (data.filings || []).forEach((filing) => {
    (filing.transactions || []).forEach((tx) => {
      shown += 1;
      const block = el("article", "filing");
      const title = el("h3", null, filing.owner_name || "No data");
      block.append(title);
      const code = tx.code || "—";
      const meaning = FORM4_CODES[code] ? `${code} ${FORM4_CODES[code]}` : code;
      block.append(el("p", "subline", `${filing.owner_title || "No data"} · ${tx.date || filing.filing_date || "No data"}`));
      block.append(el("p", null, `${tx.security || "No data"}${tx.derivative ? " (derivative)" : ""}`));
      const shares = tx.shares == null ? "No data" : `${num(tx.shares, 0)} shares`;
      const px = tx.price == null ? "No data" : price(tx.price);
      block.append(el("p", null, `${meaning} · ${shares} · ${px}${tx.acquired_disposed ? " · A/D " + tx.acquired_disposed : ""}`));
      block.append(el("p", "subline", planLabel(tx.plan_10b5_1)));
      block.append(link(filing.url, `${filing.form || "Form 4"} · Filed ${filing.filing_date || "No data"}`));
      card.append(block);
    });
    if ((!filing.transactions || !filing.transactions.length) && filing.parse_error) {
      const block = el("article", "filing");
      block.append(el("h3", null, filing.owner_name || "No data"));
      block.append(el("p", "subline", `${filing.form} · Filed ${filing.filing_date || "No data"} · The XML could not be parsed this time`));
      block.append(link(filing.url, "Open filing"));
      card.append(block);
    }
  });
  if (!shown) card.append(emptyNote());
  const gloss = el("p", "glossary");
  gloss.textContent = "Code key (SEC Form 4, not a recommendation): P open-market buy, S open-market sell, A grant or award, M/X exercise or conversion, F tax withholding. In A/D, A means acquired and D means disposed.";
  card.append(gloss);
  card.append(metaRow(data.source_url, data.source_label, data.fetched_at));
  return card;
}

function renderHolders(data) {
  const card = section("holders", "Institutional holdings", "The 13F snapshot and 13G/13D positions above 5% are listed separately.");
  if (!data || !data.ok) return card.append(emptyNote()), card;
  addBanner(card, data);
  const snap = data.snapshot_13f;
  card.append(el("h3", null, snap ? `${snap.quarter_label || ""} 13F holdings` : "13F holdings"));
  card.append(el("p", "note", data.note_13f || "13F filings can lag up to 45 days after quarter end."));
  if (!snap || !snap.holders || !snap.holders.length) card.append(emptyNote());
  else {
    const table = el("table");
    const head = el("tr");
    ["Institution", "Shares"].forEach((label) => head.append(el("th", null, label)));
    table.append(head);
    snap.holders.forEach((holder) => {
      const tr = el("tr");
      tr.append(el("td", null, holder.name || "No data"));
      tr.append(el("td", null, holder.shares == null ? "No data" : num(holder.shares, 0)));
      table.append(tr);
    });
    card.append(table);
    const snapMeta = el("p", "meta");
    snapMeta.append("Source: ");
    snapMeta.append(link(snap.source_url, snap.source_label || "13F source"));
    snapMeta.append(` · Snapshot date ${snap.as_of || "No data"} (a checked static value, not this fetch's clock time)`);
    card.append(snapMeta);
  }
  card.append(el("h3", null, "13G / 13D (beneficial ownership above 5%)"));
  card.append(el("p", "kicker", "Share counts and percentages come from the cover-page reporting persons. They are not the same measure as the 13F holdings above."));
  if (data.filings_13dg_stale) {
    const banner = el("p", "banner");
    const when = formatMYT(data.filings_13dg_fetched_at);
    banner.textContent = `No data (the 13G/13D update failed). Showing the last successful parse${when ? ", updated " + when : ""}.`;
    card.append(banner);
  }
  const filings = data.filings_13dg || [];
  if (!filings.length) card.append(emptyNote());
  filings.forEach((filing) => {
    const block = el("article", "filing");
    const title = el("h3");
    title.append(el("span", filing.kind === "13D" ? "pill d13" : "pill g13", filing.form || filing.kind || "13G"));
    title.append(" ");
    title.append(link(filing.url, "Open filing"));
    block.append(title);
    block.append(el("p", "subline", `Filed ${filing.filing_date || "No data"}${filing.event_date ? " · Event date " + filing.event_date : ""}`));
    if (!filing.reporting_persons || !filing.reporting_persons.length) block.append(emptyNote());
    (filing.reporting_persons || []).forEach((person) => {
      const line = el("p", "person");
      const shares = person.shares == null ? "No data" : `${num(person.shares, 0)} shares`;
      const pct = fmtPct(person.percent);
      line.append(el("strong", null, person.name || "No data"));
      line.append(el("span", "subline", ` ${shares} · ${pct}`));
      block.append(line);
    });
    card.append(block);
  });
  card.append(metaRow(data.source_url, "SEC EDGAR", data.filings_13dg_fetched_at || data.fetched_at));
  return card;
}

function renderShort(data) {
  const card = section("short", "Short interest", "Exchange short interest and the daily short-volume ratio are listed separately");
  if (!data || !data.ok) return card.append(emptyNote()), card;
  addBanner(card, data);
  const si = data.short_interest || {};
  card.append(el("h3", null, "Exchange short interest"));
  if (si.stale) {
    const banner = el("p", "banner");
    banner.textContent = `No data (this update failed). Showing the last successful values${formatMYT(si.fetched_at) ? ", updated " + formatMYT(si.fetched_at) : ""}.`;
    card.append(banner);
  }
  const latest = si.latest;
  if (!si.ok && !latest) card.append(emptyNote());
  else if (latest) {
    const stats = el("div", "stats");
    [
      ["Shares short", num(latest.shares_short, 0) || "No data"],
      ["Settlement date", latest.settlement_date || "No data"],
      ["Avg. daily volume", latest.avg_daily_volume == null ? "No data" : num(latest.avg_daily_volume, 0)],
      ["Days to cover", latest.days_to_cover == null ? "No data" : num(latest.days_to_cover, 2)],
    ].forEach(([label, value]) => {
      const box = el("div", "stat");
      box.append(el("span", null, label), el("strong", null, String(value)));
      stats.append(box);
    });
    card.append(stats);
    if (si.history && si.history.length) {
      const table = el("table");
      const head = el("tr");
      ["Settlement", "Shares short", "Days to cover"].forEach((label) => head.append(el("th", null, label)));
      table.append(head);
      si.history.forEach((row) => {
        const tr = el("tr");
        tr.append(el("td", null, row.settlement_date));
        tr.append(el("td", null, num(row.shares_short, 0) || "No data"));
        tr.append(el("td", null, row.days_to_cover == null ? "No data" : num(row.days_to_cover, 2)));
        table.append(tr);
      });
      card.append(table);
    }
    card.append(metaRow(si.source_url, si.source_label || "Nasdaq", si.fetched_at));
  }
  const finra = data.finra || {};
  card.append(el("h3", null, "Daily short-volume ratio"));
  card.append(el("p", "kicker", "FINRA Reg SHO: short volume divided by that file's total volume. That total is not Nasdaq's full-day volume, and it is not the short interest above."));
  if (finra.stale) {
    const banner = el("p", "banner");
    banner.textContent = `No data (this update failed). Showing the last successful values${formatMYT(finra.fetched_at) ? ", updated " + formatMYT(finra.fetched_at) : ""}.`;
    card.append(banner);
  }
  const rows = finra.rows || [];
  if (!rows.length) card.append(emptyNote());
  else {
    const last = rows[rows.length - 1];
    card.append(el("p", null, `Latest session ${last.date} · ratio ${last.short_ratio == null ? "No data" : num(last.short_ratio * 100, 1) + "%"}`));
    const chart = el("div", "chart");
    chart.dataset.label = "Daily short-volume ratio";
    drawChart(chart, [{
      name: "Short-volume ratio",
      color: "#0f6e6b",
      points: rows.map((row) => ({ x: row.date, y: row.short_ratio == null ? null : row.short_ratio * 100 })),
    }], (value) => `${value.toFixed(0)}%`);
    card.append(chart);
    const table = el("table");
    const head = el("tr");
    ["Date", "Short volume", "Total volume", "Ratio"].forEach((label) => head.append(el("th", null, label)));
    table.append(head);
    rows.slice().reverse().forEach((row) => {
      const tr = el("tr");
      tr.append(el("td", null, row.date));
      tr.append(el("td", null, num(row.short_volume, 0) || "No data"));
      tr.append(el("td", null, num(row.total_volume, 0) || "No data"));
      tr.append(el("td", null, row.short_ratio == null ? "No data" : `${num(row.short_ratio * 100, 1)}%`));
      table.append(tr);
    });
    const wrap = el("div", "scroll");
    wrap.append(table);
    card.append(wrap);
    card.append(metaRow(finra.source_url, finra.source_label || "FINRA", finra.fetched_at));
  }
  if (data.note) card.append(el("p", "note", data.note));
  return card;
}

function renderNews(data) {
  const card = section("news", "News", "Public headlines that mention Opendoor");
  if (!data || !data.ok) return card.append(emptyNote()), card;
  addBanner(card, data);
  (data.items || []).forEach((item) => {
    const block = el("article", "news-item");
    const title = el("h3");
    let headline = item.title || "No data";
    if (item.source && headline.endsWith(` - ${item.source}`)) {
      headline = headline.slice(0, -(` - ${item.source}`).length);
    }
    title.append(link(item.url, headline));
    block.append(title);
    const when = formatMYT(item.published_at);
    const line = el("p", "subline");
    if (item.source) {
      line.append(link(item.source_home, item.source));
      line.append(" · ");
    } else line.append("No data · ");
    line.append(when || "No data");
    block.append(line);
    card.append(block);
  });
  if (!data.items || !data.items.length) card.append(emptyNote());
  if (data.note) card.append(el("p", "note", data.note));
  card.append(metaRow(data.source_url, data.source_label, data.fetched_at));
  return card;
}

function renderStatus(data) {
  const card = section("status", "Data status", "Result of each fetch. If a fetch fails, the page keeps the last successful figures.");
  if (!data || !data.sources) return card.append(emptyNote()), card;
  const grid = el("div", "status-grid");
  data.sources.forEach((row) => {
    const line = el("div");
    const state = row.placeholder ? "Placeholder" : row.stale ? "Last good" : row.ok ? "OK" : "No data";
    line.append(el("span", null, row.label || row.id), el("strong", null, state));
    grid.append(line);
  });
  card.append(grid);
  card.append(metaRow(null, "This site's fetch script", data.fetched_at));
  return card;
}

async function loadAll() {
  const entries = await Promise.all(FILES.map(async (name) => {
    try {
      const response = await fetch(`data/${name}.json`, { cache: "no-store" });
      if (!response.ok) return [name, null];
      return [name, await response.json()];
    } catch (error) {
      return [name, null];
    }
  }));
  return Object.fromEntries(entries);
}

async function main() {
  const boot = document.getElementById("boot");
  const host = document.getElementById("sections");
  const data = await loadAll();
  host.hidden = false;
  boot.hidden = true;
  host.append(
    await renderBriefing(data.briefing),
    renderQuote(data.quote),
    renderAccountable(data.accountable),
    renderFilings(data.filings),
    renderInsiders(data.insiders),
    renderHolders(data.holders),
    renderShort(data.short),
    renderNews(data.news),
    renderStatus(data.status),
  );
}

main();
