"use strict";

const FILES = ["briefing", "quote", "accountable", "filings", "insiders", "holders", "short", "news", "status"];

const FORM4_CODES = {
  P: "公开市场购入",
  S: "公开市场售出",
  A: "授予或奖励",
  M: "衍生品行权或转换",
  F: "缴税预扣",
  G: "赠与",
  D: "向公司处置",
  C: "转换",
  J: "其他",
  V: "自愿申报",
  L: "小额购入",
  W: "继承或遗嘱",
  I: "自由支配的行权",
  X: "行权或衍生品结算",
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
  if (!href) return el("span", className, label || "暂无数据");
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
  if (value == null || !Number.isFinite(Number(value))) return "暂无数据";
  const abs = Math.abs(Number(value));
  const digits = abs >= 100 ? 2 : abs >= 1 ? 3 : 4;
  let text = Number(value).toFixed(digits).replace(/0+$/, "").replace(/\.$/, "");
  const [whole, frac = ""] = text.split(".");
  const padded = (frac + "00").slice(0, Math.max(2, frac.length));
  return `$${Number(whole).toLocaleString("en-US")}.${padded}`;
}

function fmtPct(value) {
  if (value == null || !Number.isFinite(Number(value))) return "比例暂无数据";
  const n = Number(value);
  const oneDecimal = Math.abs(n * 10 - Math.round(n * 10)) < 1e-6;
  return `${num(n, oneDecimal ? 1 : 2)}%`;
}

function signed(value, digits) {
  if (value == null || !Number.isFinite(Number(value))) return "暂无数据";
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
  banner.textContent = `暂无数据（本次更新失败${tried ? "，尝试于 " + tried : ""}）。以下为上次成功数值${when ? "，更新于 " + when : ""}。`;
  if (noun) banner.dataset.section = noun;
  return banner;
}

function metaRow(url, label, fetchedAt) {
  const p = el("p", "meta");
  p.append("来源：");
  p.append(link(url, label || "链接"));
  const when = formatMYT(fetchedAt);
  p.append(` · 更新于 ${when || "暂无数据"}`);
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
  return el("p", "empty", text || "暂无数据");
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
  svg.setAttribute("aria-label", host.dataset.label || "图表");
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

function renderBriefing(data) {
  const card = section("briefing", "每日简报", "每日 08:00 MYT 更新窗口之后替换本段");
  if (!data) return card.append(emptyNote()), card;
  if (data.placeholder) {
    card.append(el("p", null, (data.bullets && data.bullets[0] && data.bullets[0].text) || "暂无数据"));
    const meta = el("p", "meta", "来源：本站占位 · 更新于 尚未生成（下次 08:00 MYT）");
    card.append(meta);
    return card;
  }
  addBanner(card, data);
  if (data.date) card.append(el("p", "kicker", `简报日期 ${data.date}`));
  const list = el("ul", "list");
  (data.bullets || []).forEach((bullet) => {
    const li = el("li");
    const href = safeUrl(bullet.href);
    if (href) li.append(link(href, bullet.text || href));
    else li.textContent = bullet.text || "暂无数据";
    list.append(li);
  });
  if (!data.bullets || !data.bullets.length) card.append(emptyNote());
  else card.append(list);
  card.append(metaRow(data.source_url, data.source_label || "简报来源", data.updated_at || data.fetched_at));
  return card;
}

function renderQuote(data) {
  const card = section("quote", "股价", "最近一个已收盘交易日，不是盘中报价");
  if (!data || !data.ok) return card.append(emptyNote()), card;
  addBanner(card, data);
  card.append(el("p", "price", price(data.last_close)));
  const row = el("div", "row");
  const change = el("span", `chg ${direction(data.change)}`, `${signed(data.change, 3)}（${signed(data.change_pct, 2)}%）`);
  row.append(change);
  card.append(row);
  const stats = el("div", "stats");
  const cells = [
    ["收盘日", data.session_date || "暂无数据"],
    ["前收", price(data.previous_close)],
    ["成交量", data.volume == null ? "暂无数据" : num(data.volume, 0)],
    ["货币", data.currency || "USD"],
  ];
  cells.forEach(([label, value]) => {
    const box = el("div", "stat");
    box.append(el("span", null, label), el("strong", null, String(value)));
    stats.append(box);
  });
  card.append(stats);
  const chart = el("div", "chart");
  chart.dataset.label = "约六个月收盘价";
  drawChart(chart, [{
    name: "收盘价",
    color: "#0f6e6b",
    points: (data.points || []).map((point) => ({ x: point.date, y: point.close })),
  }], (value) => `$${value.toFixed(2)}`);
  card.append(chart);
  if (data.fallback_error) card.append(el("p", "note", `首选来源本次不可用，已改用 ${data.source_label}。`));
  card.append(metaRow(data.source_url, data.source_label, data.fetched_at));
  return card;
}

function renderAccountable(data) {
  const card = section("accountable", "Opendoor Accountable", "官方看板的每周收购合约");
  if (!data || !data.ok) return card.append(emptyNote()), card;
  addBanner(card, data);
  card.append(el("p", "note", data.contracts_label || "合约是同意收购的协议，不是已完成的购买。"));
  const weeks = data.weeks || [];
  const latest = weeks[weeks.length - 1];
  if (latest) {
    const stats = el("div", "stats");
    [["最新一周", latest.week_ending], ["合约数", num(latest.contracts, 0)], ["较上周", latest.wow == null ? "—" : signed(latest.wow, 0)], ["去年同期", latest.prior_year == null ? "暂无数据" : num(latest.prior_year, 0)]]
      .forEach(([label, value]) => {
        const box = el("div", "stat");
        box.append(el("span", null, label), el("strong", null, String(value)));
        stats.append(box);
      });
    card.append(stats);
  }
  const chart = el("div", "chart");
  chart.dataset.label = "每周收购合约";
  drawChart(chart, [
    { name: "合约数", color: "#0f6e6b", points: weeks.map((row) => ({ x: row.week_ending, y: row.contracts })) },
    { name: "去年同期", color: "#98a8b0", dash: "4 3", points: weeks.map((row) => ({ x: row.week_ending, y: row.prior_year })) },
  ], (value) => num(value, 0));
  card.append(chart);
  const table = el("table");
  table.append(el("caption", "kicker", "较上周由相邻两个已公布周的合约数相减。"));
  const head = el("tr");
  ["周截止", "合约", "较上周", "去年同期"].forEach((label) => head.append(el("th", null, label)));
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
  if ((weeks.length || 0) > 16) card.append(el("p", "meta", `表内为最近 16 周，图为全部 ${weeks.length} 个已公布周。`));
  if (data.official_note) card.append(el("p", "note", `官网原文：${data.official_note}`));
  const cross = data.crosscheck;
  if (cross && cross.ok && cross.latest) {
    const crossLine = el("p", "meta");
    crossLine.append(`交叉验证 ${cross.latest.week_ending} = ${num(cross.latest.contracts, 0)}，${cross.matches_accountable ? "与官方看板一致" : "与官方看板不一致，页面仍用官方看板"}。来源：`);
    crossLine.append(link(cross.source_url, "opendoor.army"));
    card.append(crossLine);
  } else {
    const crossLine = el("p", "meta");
    crossLine.append("交叉验证暂无数据，未改写官方数字。来源：");
    crossLine.append(link(ARMY_URL, "opendoor.army"));
    card.append(crossLine);
  }
  card.append(metaRow(data.source_url, data.source_label, data.fetched_at));
  card.append(el("h3", null, "产品、功能与合作发布"));
  const launches = data.launches || [];
  if (!launches.length) card.append(emptyNote());
  else {
    const box = el("div", "launches");
    launches.forEach((item) => {
      const block = el("article", "launch");
      block.append(el("h3", null, item.title || "暂无数据"));
      const line = el("p", "subline");
      if (item.tag) line.append(el("span", "tag", item.tag), " · ");
      line.append(item.date || item.date_raw || "日期暂无数据");
      block.append(line);
      if (item.description) block.append(el("p", "subline", item.description));
      box.append(block);
    });
    card.append(box);
    card.append(el("p", "meta", `共 ${launches.length} 条，与上方合约同一来源、同一次更新。`));
  }
  return card;
}

const ARMY_URL = "https://opendoor.army/charts/weekly";
const FORM_GLOSS = {
  "10-Q": "季度报告",
  "10-Q/A": "季度报告修订",
  "10-K": "年度报告",
  "10-K/A": "年度报告修订",
  "8-K": "临时报告",
  "8-K/A": "临时报告修订",
  "S-3": "证券注册声明",
  "S-3ASR": "自动生效的货架注册",
  "DEF 14A": "股东委托书",
};

function renderFilings(data) {
  const card = section("filings", "公司公告", "SEC EDGAR 最新申报。8-K、10-Q、10-K、S-3、424B、DEF 14A 会标出。");
  if (!data || !data.ok) return card.append(emptyNote()), card;
  addBanner(card, data);
  (data.filings || []).forEach((row) => {
    const block = el("article", "filing");
    const title = el("h3");
    if (row.highlight) title.append(el("span", "pill hot", row.form || "表格"));
    else title.append(el("span", "pill flat", row.form || "表格"));
    title.append(" ");
    let desc = row.description || row.form || "申报";
    if (FORM_GLOSS[row.form] && (desc === row.form || desc === "")) desc = FORM_GLOSS[row.form];
    title.append(link(row.url, desc));
    block.append(title);
    block.append(el("p", "subline", `申报日 ${row.filing_date || "暂无数据"}${row.report_date ? " · 报告期 " + row.report_date : ""}`));
    card.append(block);
  });
  if (!data.filings || !data.filings.length) card.append(emptyNote());
  card.append(el("p", "note", data.note || ""));
  card.append(metaRow(data.source_url, data.source_label, data.fetched_at));
  return card;
}

function planLabel(flag) {
  if (flag === "yes") return "10b5-1：是（申报已勾选）";
  if (flag === "footnote") return "10b5-1：脚注提及";
  if (flag === "no") return "10b5-1：否（申报未勾选）";
  return "10b5-1：暂无数据";
}

function renderInsiders(data) {
  const card = section("insiders", "内部人交易", "最近 Form 4。姓名与职务按申报原文。");
  if (!data || !data.ok) return card.append(emptyNote()), card;
  addBanner(card, data);
  let shown = 0;
  (data.filings || []).forEach((filing) => {
    (filing.transactions || []).forEach((tx) => {
      shown += 1;
      const block = el("article", "filing");
      const title = el("h3", null, filing.owner_name || "姓名暂无数据");
      block.append(title);
      const code = tx.code || "—";
      const meaning = FORM4_CODES[code] ? `${code} ${FORM4_CODES[code]}` : code;
      block.append(el("p", "subline", `${filing.owner_title || "职务暂无数据"} · ${tx.date || filing.filing_date || "日期暂无数据"}`));
      block.append(el("p", null, `${tx.security || "证券暂无数据"}${tx.derivative ? "（衍生）" : ""}`));
      const shares = tx.shares == null ? "股数暂无数据" : `${num(tx.shares, 0)} 股`;
      const px = tx.price == null ? "价格暂无数据" : price(tx.price);
      block.append(el("p", null, `${meaning} · ${shares} · ${px}${tx.acquired_disposed ? " · A/D " + tx.acquired_disposed : ""}`));
      block.append(el("p", "subline", planLabel(tx.plan_10b5_1)));
      block.append(el("p", "subline", ""), link(filing.url, `${filing.form || "Form 4"} · 申报日 ${filing.filing_date || "暂无数据"}`));
      card.append(block);
    });
    if ((!filing.transactions || !filing.transactions.length) && filing.parse_error) {
      const block = el("article", "filing");
      block.append(el("h3", null, filing.owner_name || "明细暂无数据"));
      block.append(el("p", "subline", `${filing.form} · 申报日 ${filing.filing_date || "暂无数据"} · 本次未能解析 XML`));
      block.append(link(filing.url, "打开申报"));
      card.append(block);
    }
  });
  if (!shown) card.append(emptyNote());
  const gloss = el("p", "glossary");
  gloss.textContent = "代码对照（SEC Form 4，不是操作建议）：P 公开市场购入，S 公开市场售出，A 授予或奖励，M/X 行权或转换，F 缴税预扣。A/D 里 A 表示获得、D 表示处置。";
  card.append(gloss);
  card.append(metaRow(data.source_url, data.source_label, data.fetched_at));
  return card;
}

function renderHolders(data) {
  const card = section("holders", "机构持仓", "13F 快照与超过 5% 的 13G/13D 分开列示。");
  if (!data || !data.ok) return card.append(emptyNote()), card;
  addBanner(card, data);
  const snap = data.snapshot_13f;
  card.append(el("h3", null, snap ? `${snap.quarter_label || ""} 13F 持仓` : "13F 持仓"));
  card.append(el("p", "note", data.note_13f || "13F 在季度结束后最多滞后约 45 天。"));
  if (!snap || !snap.holders || !snap.holders.length) card.append(emptyNote());
  else {
    const table = el("table");
    const head = el("tr");
    ["机构", "股数"].forEach((label) => head.append(el("th", null, label)));
    table.append(head);
    snap.holders.forEach((holder) => {
      const tr = el("tr");
      tr.append(el("td", null, holder.name || "暂无数据"));
      tr.append(el("td", null, holder.shares == null ? "暂无数据" : num(holder.shares, 0)));
      table.append(tr);
    });
    card.append(table);
    const snapMeta = el("p", "meta");
    snapMeta.append("来源：");
    snapMeta.append(link(snap.source_url, snap.source_label || "13F 来源"));
    snapMeta.append(` · 快照日 ${snap.as_of || "暂无数据"}（静态核对值，不是本次抓取时钟）`);
    card.append(snapMeta);
  }
  card.append(el("h3", null, "13G / 13D（受益持有超过 5%）"));
  card.append(el("p", "kicker", "股数与比例来自封面报告人，和上面的 13F 持仓不是同一口径。"));
  if (data.filings_13dg_stale) {
    const banner = el("p", "banner");
    const when = formatMYT(data.filings_13dg_fetched_at);
    banner.textContent = `暂无数据（13G/13D 本次更新失败）。以下为上次成功解析${when ? "，更新于 " + when : ""}。`;
    card.append(banner);
  }
  const filings = data.filings_13dg || [];
  if (!filings.length) card.append(emptyNote());
  filings.forEach((filing) => {
    const block = el("article", "filing");
    const title = el("h3");
    title.append(el("span", filing.kind === "13D" ? "pill d13" : "pill g13", filing.form || filing.kind || "13G"));
    title.append(" ");
    title.append(link(filing.url, "打开申报"));
    block.append(title);
    block.append(el("p", "subline", `申报日 ${filing.filing_date || "暂无数据"}${filing.event_date ? " · 事件日 " + filing.event_date : ""}`));
    if (!filing.reporting_persons || !filing.reporting_persons.length) block.append(emptyNote(filing.parse_error ? "股数暂无数据" : "暂无数据"));
    (filing.reporting_persons || []).forEach((person) => {
      const line = el("p", "person");
      const shares = person.shares == null ? "股数暂无数据" : `${num(person.shares, 0)} 股`;
      const pct = fmtPct(person.percent);
      line.append(el("strong", null, person.name || "名称暂无数据"));
      line.append(el("span", "subline", ` ${shares} · ${pct}`));
      block.append(line);
    });
    card.append(block);
  });
  card.append(metaRow(data.source_url, "SEC EDGAR", data.filings_13dg_fetched_at || data.fetched_at));
  return card;
}

function renderShort(data) {
  const card = section("short", "做空数据", "未平仓做空与每日卖空成交比分开列示");
  if (!data || !data.ok) return card.append(emptyNote()), card;
  addBanner(card, data);
  const si = data.short_interest || {};
  card.append(el("h3", null, "交易所未平仓做空"));
  if (si.stale) {
    const banner = el("p", "banner");
    banner.textContent = `暂无数据（本次更新失败）。以下为上次成功数值${formatMYT(si.fetched_at) ? "，更新于 " + formatMYT(si.fetched_at) : ""}。`;
    card.append(banner);
  }
  const latest = si.latest;
  if (!si.ok && !latest) card.append(emptyNote());
  else if (latest) {
    const stats = el("div", "stats");
    [
      ["卖空股数", num(latest.shares_short, 0) || "暂无数据"],
      ["结算日", latest.settlement_date || "暂无数据"],
      ["日均成交量", latest.avg_daily_volume == null ? "暂无数据" : num(latest.avg_daily_volume, 0)],
      ["回补天数", latest.days_to_cover == null ? "暂无数据" : num(latest.days_to_cover, 2)],
    ].forEach(([label, value]) => {
      const box = el("div", "stat");
      box.append(el("span", null, label), el("strong", null, String(value)));
      stats.append(box);
    });
    card.append(stats);
    if (si.history && si.history.length) {
      const table = el("table");
      const head = el("tr");
      ["结算日", "卖空股数", "回补天数"].forEach((label) => head.append(el("th", null, label)));
      table.append(head);
      si.history.forEach((row) => {
        const tr = el("tr");
        tr.append(el("td", null, row.settlement_date));
        tr.append(el("td", null, num(row.shares_short, 0) || "暂无数据"));
        tr.append(el("td", null, row.days_to_cover == null ? "暂无数据" : num(row.days_to_cover, 2)));
        table.append(tr);
      });
      card.append(table);
    }
    card.append(metaRow(si.source_url, si.source_label || "Nasdaq", si.fetched_at));
  }
  const finra = data.finra || {};
  card.append(el("h3", null, "每日卖空成交比"));
  card.append(el("p", "kicker", "FINRA Reg SHO：卖空成交量 ÷ 该文件里的当日总量。这个总量不是 Nasdaq 全日成交量，也不是上面的未平仓做空。"));
  if (finra.stale) {
    const banner = el("p", "banner");
    banner.textContent = `暂无数据（本次更新失败）。以下为上次成功数值${formatMYT(finra.fetched_at) ? "，更新于 " + formatMYT(finra.fetched_at) : ""}。`;
    card.append(banner);
  }
  const rows = finra.rows || [];
  if (!rows.length) card.append(emptyNote());
  else {
    const last = rows[rows.length - 1];
    card.append(el("p", null, `最近交易日 ${last.date} · 比例 ${last.short_ratio == null ? "暂无数据" : num(last.short_ratio * 100, 1) + "%"}`));
    const chart = el("div", "chart");
    chart.dataset.label = "每日卖空成交比";
    drawChart(chart, [{
      name: "卖空成交比",
      color: "#0f6e6b",
      points: rows.map((row) => ({ x: row.date, y: row.short_ratio == null ? null : row.short_ratio * 100 })),
    }], (value) => `${value.toFixed(0)}%`);
    card.append(chart);
    const table = el("table");
    const head = el("tr");
    ["日期", "卖空量", "总量", "比例"].forEach((label) => head.append(el("th", null, label)));
    table.append(head);
    rows.slice().reverse().forEach((row) => {
      const tr = el("tr");
      tr.append(el("td", null, row.date));
      tr.append(el("td", null, num(row.short_volume, 0) || "暂无数据"));
      tr.append(el("td", null, num(row.total_volume, 0) || "暂无数据"));
      tr.append(el("td", null, row.short_ratio == null ? "暂无数据" : `${num(row.short_ratio * 100, 1)}%`));
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
  const card = section("news", "新闻", "标题含 Opendoor 的公开报道");
  if (!data || !data.ok) return card.append(emptyNote()), card;
  addBanner(card, data);
  (data.items || []).forEach((item) => {
    const block = el("article", "news-item");
    const title = el("h3");
    let headline = item.title || "暂无数据";
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
    } else line.append("来源暂无数据 · ");
    line.append(when || "时间暂无数据");
    block.append(line);
    card.append(block);
  });
  if (!data.items || !data.items.length) card.append(emptyNote());
  if (data.note) card.append(el("p", "note", data.note));
  card.append(metaRow(data.source_url, data.source_label, data.fetched_at));
  return card;
}

function renderStatus(data) {
  const card = section("status", "数据状态", "每次抓取的结果。失败时页面保留上次成功的数字。");
  if (!data || !data.sources) return card.append(emptyNote()), card;
  const grid = el("div", "status-grid");
  data.sources.forEach((row) => {
    const line = el("div");
    const state = row.id === "briefing" && row.ok ? "占位" : row.stale ? "沿用上次" : row.ok ? "成功" : "暂无数据";
    line.append(el("span", null, row.label || row.id), el("strong", null, state));
    grid.append(line);
  });
  card.append(grid);
  card.append(metaRow(null, "本站抓取脚本", data.fetched_at));
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
    renderBriefing(data.briefing),
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
