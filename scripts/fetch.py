#!/usr/bin/env python3
"""Fetch public Opendoor (NASDAQ: OPEN) data into data/*.json.

Stdlib only. On failure, keep the last successful payload and mark it stale.
Does not modify data/briefing.json (that file is replaced by a later process).
"""

from __future__ import annotations

import email.utils
import gzip
import json
import re
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

SEC_UA = "opendoor-radar contact@example.com"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
SEC_HEADERS = {
    "User-Agent": SEC_UA,
    "Accept": "application/json,application/xml,text/plain,*/*",
}
BROWSER_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/json,text/plain,text/html,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

CIK = "0001801169"
CIK_INT = "1801169"
SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"
EDGAR_BROWSE = f"https://www.sec.gov/edgar/browse/?CIK={CIK_INT}"
ACCOUNTABLE_URL = "https://accountable.opendoor.com/"
ARMY_URL = "https://opendoor.army/charts/weekly"
YAHOO_CHART = (
    "https://query1.finance.yahoo.com/v8/finance/chart/OPEN"
    "?range=6mo&interval=1d&includePrePost=false"
)
YAHOO_PAGE = "https://finance.yahoo.com/quote/OPEN/"
NASDAQ_INFO = "https://api.nasdaq.com/api/quote/OPEN/info?assetclass=stocks"
NASDAQ_HIST = "https://api.nasdaq.com/api/quote/OPEN/historical?assetclass=stocks"
NASDAQ_SI = "https://api.nasdaq.com/api/quote/OPEN/short-interest?assetclass=stocks"
NASDAQ_PAGE = "https://www.nasdaq.com/market-activity/stocks/open"
NASDAQ_SI_PAGE = "https://www.nasdaq.com/market-activity/stocks/open/short-interest"
FINRA_TMPL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{ymd}.txt"
NEWS_RSS = "https://news.google.com/rss/search?q=Opendoor&hl=en-US&gl=US&ceid=US:en"

# Verified Q2 2026 13F snapshot. The fetcher rewrites this constant; it does
# not scrape a new 13F table, so an unverified number cannot replace it.
SNAPSHOT_13F = {
    "as_of": "2026-06-30",
    "quarter_label": "2026 年第二季度",
    "source_label": "13fholding.com",
    "source_url": "https://13fholding.com/stock/open-683712103",
    "static_seed": True,
    "holders": [
        {"name": "Morgan Stanley", "shares": 115205390},
        {"name": "Vanguard Portfolio Management", "shares": 76214771},
        {"name": "BlackRock", "shares": 72920992},
    ],
}

ITEM_ZH = {
    "1.01": "重大协议",
    "1.02": "终止重大协议",
    "1.03": "破产或接管",
    "2.01": "完成资产收购或处置",
    "2.02": "经营业绩与财务状况",
    "2.03": "产生直接财务义务",
    "2.04": "触发直接财务义务",
    "3.01": "退市或无法继续上市",
    "3.02": "未注册股权出售",
    "3.03": "证券持有人权利重大修改",
    "5.02": "董事或高管变动",
    "5.03": "章程或细则修订",
    "5.07": "提交股东表决的事项",
    "7.01": "Regulation FD 披露",
    "8.01": "其他事项",
    "9.01": "财务报表与附件",
}

OWNERSHIP_FORMS = {"3", "3/A", "4", "4/A", "5", "5/A", "144", "144/A"}
_LAST_SEC = 0.0


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def decompress(body: bytes) -> bytes:
    if len(body) >= 2 and body[:2] == b"\x1f\x8b":
        return gzip.decompress(body)
    return body


def http_get(url: str, headers: dict, timeout: int = 30, retries: int = 3) -> tuple[int, bytes]:
    last_exc: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, decompress(resp.read())
        except urllib.error.HTTPError as exc:
            body = decompress(exc.read() or b"")
            if exc.code in (429, 500, 502, 503, 504) and attempt + 1 < retries:
                time.sleep(1.2 * (attempt + 1))
                continue
            return exc.code, body
        except Exception as exc:  # noqa: BLE001 - network errors are data-source failures
            last_exc = exc
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"request failed for {url}: {last_exc}")


def sec_get(url: str) -> tuple[int, bytes]:
    global _LAST_SEC
    wait = 0.21 - (time.time() - _LAST_SEC)
    if wait > 0:
        time.sleep(wait)
    status, body = http_get(url, SEC_HEADERS, timeout=40, retries=3)
    _LAST_SEC = time.time()
    return status, body


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def refresh(path: Path, producer) -> tuple[dict, str | None]:
    """Write producer() on success. On failure keep the last ok payload."""
    old = load_json(path)
    try:
        new = producer(old)
        if not isinstance(new, dict):
            raise RuntimeError("producer did not return an object")
        new["ok"] = True
        new["stale"] = False
        new["fetch_error"] = None
        new["fetched_at"] = utcnow()
        new["fetch_attempted_at"] = new["fetched_at"]
        save_json(path, new)
        return new, None
    except Exception as exc:  # noqa: BLE001 - record and preserve last good data
        err = f"{type(exc).__name__}: {exc}"
        print(f"  FAIL {path.name}: {err}", file=sys.stderr)
        traceback.print_exc()
        if old and old.get("ok"):
            old["stale"] = True
            old["fetch_error"] = err
            old["fetch_attempted_at"] = utcnow()
            save_json(path, old)
            return old, err
        stub = {
            "ok": False,
            "stale": False,
            "fetched_at": None,
            "fetch_attempted_at": utcnow(),
            "fetch_error": err,
        }
        save_json(path, stub)
        return stub, err


def parse_money(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("$", "").replace(",", "")
    if text in ("", "N/A", "NA", "--", "null", "None"):
        return None
    return float(text)


def parse_intish(value) -> int | None:
    num = parse_money(value)
    if num is None:
        return None
    return int(round(num))


def parse_any_date(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def strip_xml_ns(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    text = re.sub(r'\sxmlns(:\w+)?="[^"]*"', "", text)
    text = re.sub(r'\s[\w.\-]+:[\w.\-]+="[^"]*"', "", text)
    text = re.sub(r"<(/?)[\w.\-]+:", r"<\1", text)
    return text


def accession_cik(accession: str) -> str:
    return str(int(accession.split("-")[0]))


def accession_nodash(accession: str) -> str:
    return accession.replace("-", "")


def filing_index_url(accession: str) -> str:
    cik = accession_cik(accession)
    accn = accession_nodash(accession)
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{accession}-index.html"


def is_highlight(form: str) -> bool:
    text = (form or "").upper().strip()
    compact = text.replace(" ", "")
    if text.startswith("8-K"):
        return True
    if text.startswith("10-Q") or text.startswith("10-K"):
        return True
    if text.startswith("S-3"):
        return True
    if text.startswith("424B"):
        return True
    if compact.startswith("DEF14A"):
        return True
    return False


def describe_filing(form: str, items: str, primary_desc: str) -> str:
    parts = []
    if items:
        for code in [p.strip() for p in items.split(",") if p.strip()]:
            gloss = ITEM_ZH.get(code)
            parts.append(f"{code} {gloss}" if gloss else code)
    if parts:
        return "事项 " + "；".join(parts)
    desc = (primary_desc or "").strip()
    if desc and desc.upper() != (form or "").upper():
        return desc
    return form or ""


def load_submissions() -> dict:
    status, body = sec_get(SUBMISSIONS_URL)
    if status != 200:
        raise RuntimeError(f"EDGAR submissions HTTP {status}")
    return json.loads(body.decode("utf-8"))


def iter_recent(submissions: dict):
    recent = submissions["filings"]["recent"]
    count = len(recent["form"])
    for i in range(count):
        yield {
            "form": recent["form"][i],
            "filing_date": recent["filingDate"][i],
            "report_date": recent["reportDate"][i] or None,
            "accession": recent["accessionNumber"][i],
            "primary_document": recent["primaryDocument"][i],
            "description": recent["primaryDocDescription"][i] or "",
            "items": recent["items"][i] or "",
            "acceptance": recent["acceptanceDateTime"][i] or None,
        }


def produce_filings(_old) -> dict:
    submissions = load_submissions()
    name = submissions.get("name")
    rows = []
    for row in iter_recent(submissions):
        if row["form"] in OWNERSHIP_FORMS:
            continue
        rows.append(
            {
                "form": row["form"],
                "filing_date": row["filing_date"],
                "report_date": row["report_date"] or None,
                "description": describe_filing(row["form"], row["items"], row["description"]),
                "items": row["items"] or None,
                "highlight": is_highlight(row["form"]),
                "accession": row["accession"],
                "url": filing_index_url(row["accession"]),
            }
        )
        if len(rows) >= 20:
            break
    if not rows:
        raise RuntimeError("no company filings parsed")
    return {
        "company": name,
        "cik": CIK,
        "source_label": "SEC EDGAR submissions",
        "source_url": EDGAR_BROWSE,
        "source_api": SUBMISSIONS_URL,
        "note": "列表不含 Form 3/4/5 与 Form 144，那些表格在「内部人交易」。申报日按 EDGAR 原文，不做时区换算。",
        "filings": rows,
    }


def child_value(parent: ET.Element, tag: str) -> str | None:
    node = parent.find(tag)
    if node is None:
        return None
    value = node.find("value")
    if value is not None and value.text and value.text.strip():
        return value.text.strip()
    if node.text and node.text.strip():
        return " ".join(node.text.split())
    return None


def produce_insiders(_old) -> dict:
    submissions = load_submissions()
    filings = []
    errors = []
    wanted = [row for row in iter_recent(submissions) if row["form"] in {"4", "4/A"}][:10]
    if not wanted:
        raise RuntimeError("no Form 4 filings in submissions")
    for row in wanted:
        try:
            parsed = fetch_form4(row)
            filings.append(parsed)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{row['accession']}: {exc}")
            filings.append(
                {
                    "form": row["form"],
                    "filing_date": row["filing_date"],
                    "accession": row["accession"],
                    "url": filing_index_url(row["accession"]),
                    "owner_name": None,
                    "owner_title": None,
                    "transactions": [],
                    "parse_error": str(exc),
                }
            )
    if not any(item.get("transactions") for item in filings):
        raise RuntimeError("Form 4 XML parse produced no transactions; " + "; ".join(errors[:3]))
    return {
        "source_label": "SEC EDGAR Form 4 XML",
        "source_url": EDGAR_BROWSE,
        "source_api": SUBMISSIONS_URL,
        "note": "交易代码与 10b5-1 标记均来自申报原文。代码只说明表格里填写的类型，不是买卖建议。",
        "filings": filings,
        "parse_errors": errors,
    }


def fetch_form4(row: dict) -> dict:
    cik = accession_cik(row["accession"])
    accn = accession_nodash(row["accession"])
    fname = (row["primary_document"] or "").split("/")[-1]
    urls = [
        f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{fname}",
        f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{row['primary_document']}",
    ]
    last_status = None
    root = None
    for url in urls:
        if not url.endswith(".xml"):
            continue
        status, body = sec_get(url)
        last_status = status
        if status == 200 and body.lstrip().startswith(b"<?xml"):
            root = ET.fromstring(strip_xml_ns(body))
            break
    if root is None:
        raise RuntimeError(f"Form 4 XML HTTP {last_status}")
    footnotes = {}
    for fn in root.findall(".//footnote"):
        fid = fn.get("id")
        if fid:
            footnotes[fid] = " ".join("".join(fn.itertext()).split())
    doc_flags = [n.text.strip() for n in root.findall("./aff10b5One") if n.text and n.text.strip()]
    owner = root.find(".//rptOwnerName")
    officer = root.find(".//officerTitle")
    rel = root.find(".//reportingOwnerRelationship")

    def flag(tag: str) -> str:
        if rel is None:
            return "0"
        node = rel.find(tag)
        return (node.text or "").strip() if node is not None and node.text else "0"

    title_parts = []
    officer_title = officer.text.strip() if officer is not None and officer.text and officer.text.strip() else ""
    if officer_title:
        title_parts.append(officer_title)
    elif flag("isOfficer") == "1":
        title_parts.append("高管")
    if flag("isDirector") == "1":
        title_parts.append("董事")
    if flag("isTenPercentOwner") == "1":
        title_parts.append("持股超过 5%")
    if flag("isOther") == "1" and not title_parts:
        title_parts.append("其他")

    transactions = []
    for tx in list(root.findall(".//nonDerivativeTransaction")) + list(root.findall(".//derivativeTransaction")):
        amounts = tx.find("transactionAmounts")
        share_node = amounts.find("transactionShares") if amounts is not None else None
        price_node = amounts.find("transactionPricePerShare") if amounts is not None else None
        ad_node = amounts.find("transactionAcquiredDisposedCode") if amounts is not None else None
        fn_ids = []
        if share_node is not None:
            fn_ids.extend(n.get("id") for n in share_node.findall("footnoteId") if n.get("id"))
        fn_ids.extend(n.get("id") for n in tx.findall("./transactionCoding/footnoteId") if n.get("id"))
        tx_flags = [n.text.strip() for n in tx.findall(".//aff10b5One") if n.text and n.text.strip()]
        shares = child_value(share_node, "value") if share_node is not None else None
        if shares is None and share_node is not None:
            shares = child_value(amounts, "transactionShares")
        price = child_value(price_node, "value") if price_node is not None else None
        code = None
        coding = tx.find("transactionCoding")
        if coding is not None:
            code_node = coding.find("transactionCode")
            if code_node is not None and code_node.text:
                code = code_node.text.strip()
        ad = child_value(ad_node, "value") if ad_node is not None else None
        security = child_value(tx, "securityTitle")
        tx_date = child_value(tx, "transactionDate")
        blob = " ".join(footnotes.get(fid, "") for fid in fn_ids).lower()
        if "1" in tx_flags or "1" in doc_flags:
            plan = "yes"
        elif "10b5-1" in blob or "10b5–1" in blob:
            plan = "footnote"
        elif "0" in tx_flags or "0" in doc_flags:
            plan = "no"
        else:
            plan = "unknown"
        share_num = parse_money(shares)
        transactions.append(
            {
                "date": parse_any_date(tx_date) or tx_date,
                "security": security,
                "code": code,
                "acquired_disposed": ad,
                "shares": share_num,
                "price": parse_money(price),
                "plan_10b5_1": plan,
                "derivative": tx.tag == "derivativeTransaction",
            }
        )
    return {
        "form": row["form"],
        "filing_date": row["filing_date"],
        "period_of_report": (root.findtext("periodOfReport") or "").strip() or None,
        "accession": row["accession"],
        "url": filing_index_url(row["accession"]),
        "owner_name": owner.text.strip() if owner is not None and owner.text else None,
        "owner_title": " · ".join(title_parts) or None,
        "transactions": transactions,
        "parse_error": None,
    }


def is_13dg(form: str) -> bool:
    compact = (form or "").upper().replace(" ", "")
    return "13G" in compact or "13D" in compact


def produce_holders(old) -> dict:
    old = old or {}
    filings = None
    error = None
    try:
        filings = fetch_13dg()
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        print(f"  13D/G fail: {error}", file=sys.stderr)
        traceback.print_exc()
    stale = False
    if filings is None:
        previous = old.get("filings_13dg")
        if previous:
            filings = previous
            stale = True
        else:
            filings = []
    if stale:
        dg_fetched_at = old.get("filings_13dg_fetched_at")
    elif error:
        dg_fetched_at = None
    else:
        dg_fetched_at = utcnow()
    return {
        "source_label": "SEC EDGAR 13G/13D 与已核对的 13F 快照",
        "source_url": EDGAR_BROWSE,
        "snapshot_13f": SNAPSHOT_13F,
        "filings_13dg": filings,
        "filings_13dg_stale": stale,
        "filings_13dg_error": error,
        "filings_13dg_fetched_at": dg_fetched_at,
        "note_13f": "13F 在季度结束后最多滞后约 45 天才申报。上表是已核对的 2026-06-30 静态快照，自动任务不会用未核对的数字覆盖它。",
    }


def fetch_13dg() -> list:
    submissions = load_submissions()
    rows = [row for row in iter_recent(submissions) if is_13dg(row["form"])][:12]
    if not rows:
        raise RuntimeError("no 13G/13D filings in submissions")
    parsed = []
    failures = 0
    for row in rows:
        item = {
            "form": row["form"],
            "kind": "13D" if "13D" in row["form"].upper() else "13G",
            "filing_date": row["filing_date"],
            "accession": row["accession"],
            "url": filing_index_url(row["accession"]),
            "event_date": None,
            "reporting_persons": [],
            "parse_error": None,
        }
        try:
            root = fetch_13dg_xml(row)
            event = root.find(".//eventDateRequiresFilingThisStatement")
            item["event_date"] = parse_any_date(event.text if event is not None else None)
            persons = []
            for person in root.findall(".//coverPageHeaderReportingPersonDetails"):
                name_node = person.find("reportingPersonName")
                shares_node = person.find("reportingPersonBeneficiallyOwnedAggregateNumberOfShares")
                pct_node = person.find("classPercent")
                name = " ".join((name_node.text or "").split()) if name_node is not None and name_node.text else None
                if not name:
                    continue
                persons.append(
                    {
                        "name": name,
                        "shares": parse_intish(shares_node.text if shares_node is not None else None),
                        "percent": parse_money(pct_node.text if pct_node is not None else None),
                    }
                )
            item["reporting_persons"] = persons
            if not persons:
                item["parse_error"] = "XML 中没有封面报告人"
        except Exception as exc:  # noqa: BLE001
            failures += 1
            item["parse_error"] = str(exc)
        parsed.append(item)
    if failures == len(parsed):
        raise RuntimeError("every 13G/13D document failed to parse")
    return parsed


def fetch_13dg_xml(row: dict) -> ET.Element:
    cik = accession_cik(row["accession"])
    accn = accession_nodash(row["accession"])
    fname = (row["primary_document"] or "").split("/")[-1]
    candidates = [f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/primary_doc.xml"]
    if fname and fname != "primary_doc.xml":
        candidates.append(f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{fname}")
    last = None
    for url in candidates:
        status, body = sec_get(url)
        last = status
        if status == 200 and b"<" in body[:200]:
            try:
                return ET.fromstring(strip_xml_ns(body))
            except ET.ParseError:
                continue
    raise RuntimeError(f"13D/G document HTTP {last}")


def extract_json_after(text: str, marker: str):
    idx = text.find(marker)
    if idx < 0:
        raise RuntimeError(f"missing marker {marker}")
    start = text.find("[", idx)
    if start < 0:
        raise RuntimeError("missing array")
    decoder = json.JSONDecoder()
    value, _end = decoder.raw_decode(text[start:])
    return value


def decode_rsc(html: str) -> str:
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"((?:\\.|[^"\\])*)"\]\)', html)
    if not chunks:
        raise RuntimeError("Accountable page has no embedded data")
    return "".join(json.loads(f'"{chunk}"') for chunk in chunks)


def official_contract_note(html: str) -> str | None:
    match = re.search(
        r"Acquisition contract volume is based on any contract under which Opendoor expects to acquire a property.{0,220}?cancelled\.",
        html,
    )
    if match:
        return " ".join(match.group(0).split())
    return None


def produce_accountable(_old) -> dict:
    status, body = http_get(ACCOUNTABLE_URL, {**BROWSER_HEADERS, "Accept": "text/html,*/*"}, timeout=40)
    if status != 200:
        raise RuntimeError(f"Accountable HTTP {status}")
    html = body.decode("utf-8", "replace")
    decoded = decode_rsc(html)
    chart = extract_json_after(decoded, '"chart":')
    if not isinstance(chart, list) or not chart:
        raise RuntimeError("acquisition chart missing")
    updates = extract_json_after(decoded, '"updates":')
    if not isinstance(updates, list):
        raise RuntimeError("product updates missing")
    weeks = []
    for point in chart:
        if not isinstance(point, dict):
            continue
        week = parse_any_date(str(point.get("date") or ""))
        actual = point.get("actual")
        if not week or actual is None:
            continue
        weeks.append(
            {
                "week_ending": week,
                "contracts": int(actual),
                "prior_year": None if point.get("priorYear") is None else int(point.get("priorYear")),
            }
        )
    weeks.sort(key=lambda row: row["week_ending"])
    if not weeks:
        raise RuntimeError("no completed acquisition-contract weeks")
    for i, row in enumerate(weeks):
        if i == 0:
            row["wow"] = None
            row["wow_pct"] = None
        else:
            prev = weeks[i - 1]["contracts"]
            row["wow"] = row["contracts"] - prev
            row["wow_pct"] = (row["wow"] / prev * 100) if prev else None
    launches = []
    for item in updates:
        if not isinstance(item, dict):
            continue
        raw_date = str(item.get("date") or "")
        launches.append(
            {
                "id": item.get("id"),
                "date": parse_any_date(raw_date),
                "date_raw": raw_date or None,
                "tag": item.get("tag"),
                "title": item.get("title"),
                "description": item.get("description"),
            }
        )
    launches.sort(key=lambda row: (row["date"] or "", row["date_raw"] or ""), reverse=True)
    note = official_contract_note(html) or official_contract_note(decoded)
    if note is None:
        note = find_note_in_scripts(html)
    cross = try_army_crosscheck(weeks)
    return {
        "source_label": "Opendoor Accountable",
        "source_url": ACCOUNTABLE_URL,
        "official_note": note,
        "contracts_label": "合约是 Opendoor 预期收购房产的协议（含 Cash Now, More Later），不是已完成的购买，也不扣除之后取消的合约。",
        "weeks": weeks,
        "launches": launches,
        "crosscheck": cross,
    }


def find_note_in_scripts(html: str) -> str | None:
    srcs = []
    for src in re.findall(r'<script[^>]+src="(https://[^"]+\.js)"', html):
        if src not in srcs:
            srcs.append(src)
    for src in srcs[:30]:
        try:
            status, body = http_get(src, BROWSER_HEADERS, timeout=20, retries=1)
        except Exception:
            continue
        if status != 200:
            continue
        text = body.decode("utf-8", "replace")
        note = official_contract_note(text)
        if note:
            return note
    return None


def try_army_crosscheck(weeks: list) -> dict:
    try:
        status, body = http_get(ARMY_URL, BROWSER_HEADERS, timeout=20, retries=1)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "source_url": ARMY_URL, "error": str(exc), "latest": None}
    if status != 200:
        return {"ok": False, "source_url": ARMY_URL, "error": f"HTTP {status}", "latest": None}
    text = body.decode("utf-8", "replace")
    if "Just a moment" in text or "cf-challenge" in text or "challenge-platform" in text:
        return {"ok": False, "source_url": ARMY_URL, "error": "Cloudflare challenge", "latest": None}
    found = re.findall(r"(20\d\d-\d\d-\d\d)[^\d]{0,40}(\d{2,5})", text)
    if not found:
        return {"ok": False, "source_url": ARMY_URL, "error": "页面里没有可识别的周合约数", "latest": None}
    latest = weeks[-1]
    match = next((pair for pair in found if pair[0] == latest["week_ending"]), None)
    if not match:
        return {
            "ok": False,
            "source_url": ARMY_URL,
            "error": "没有对上最新一周的日期",
            "latest": None,
        }
    return {
        "ok": True,
        "source_url": ARMY_URL,
        "error": None,
        "latest": {"week_ending": match[0], "contracts": int(match[1])},
        "matches_accountable": int(match[1]) == latest["contracts"],
    }


def yahoo_points(payload: dict) -> list:
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators") or {}).get("quote") or [{}]
    closes = quote[0].get("close") or []
    volumes = quote[0].get("volume") or []
    points = []
    for i, ts in enumerate(timestamps):
        close = closes[i] if i < len(closes) else None
        if close is None:
            continue
        volume = volumes[i] if i < len(volumes) else None
        day = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ZoneInfo("America/New_York")).date()
        points.append(
            {
                "date": day.isoformat(),
                "close": round(float(close), 4),
                "volume": None if volume is None else int(volume),
            }
        )
    return points


def market_is_open(now: datetime | None = None) -> bool:
    """Regular US equity session, weekdays 09:30–16:00 America/New_York. Holidays are not listed."""
    current = now or datetime.now(ZoneInfo("America/New_York"))
    if current.weekday() >= 5:
        return False
    minutes = current.hour * 60 + current.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


def quote_from_points(points: list, source: str, source_url: str, source_api: str) -> dict:
    if len(points) < 2:
        raise RuntimeError(f"{source} returned too few daily bars")
    points = sorted(points, key=lambda row: row["date"])
    # A daily bar dated today is still forming while the cash session is open.
    today_ny = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    if market_is_open() and points[-1]["date"] == today_ny and len(points) >= 3:
        points = points[:-1]
    last = points[-1]
    prev = points[-2]
    change = last["close"] - prev["close"]
    pct = (change / prev["close"] * 100) if prev["close"] else None
    return {
        "symbol": "OPEN",
        "currency": "USD",
        "source": source,
        "source_label": "Yahoo Finance" if source == "yahoo" else "Nasdaq",
        "source_url": source_url,
        "source_api": source_api,
        "session_date": last["date"],
        "previous_session_date": prev["date"],
        "last_close": last["close"],
        "previous_close": prev["close"],
        "change": round(change, 4),
        "change_pct": None if pct is None else round(pct, 4),
        "volume": last["volume"],
        "points": points,
    }


def fetch_yahoo_quote() -> dict:
    headers = {**BROWSER_HEADERS, "Accept": "application/json", "Referer": "https://finance.yahoo.com/"}
    status, body = http_get(YAHOO_CHART, headers, timeout=25, retries=2)
    if status != 200:
        raise RuntimeError(f"Yahoo chart HTTP {status}")
    payload = json.loads(body.decode("utf-8"))
    if payload.get("chart", {}).get("error"):
        raise RuntimeError(str(payload["chart"]["error"]))
    return quote_from_points(yahoo_points(payload), "yahoo", YAHOO_PAGE, YAHOO_CHART)


def fetch_nasdaq_quote() -> dict:
    today = date.today()
    start = today - timedelta(days=190)
    url = (
        f"{NASDAQ_HIST}&fromdate={start.isoformat()}&todate={today.isoformat()}&limit=300"
    )
    status, body = http_get(url, {**BROWSER_HEADERS, "Accept": "application/json"}, timeout=30)
    if status != 200:
        raise RuntimeError(f"Nasdaq historical HTTP {status}")
    payload = json.loads(body.decode("utf-8"))
    rows = (((payload.get("data") or {}).get("tradesTable") or {}).get("rows")) or []
    points = []
    for row in rows:
        day = parse_any_date(row.get("date"))
        close = parse_money(row.get("close"))
        if not day or close is None:
            continue
        points.append({"date": day, "close": round(close, 4), "volume": parse_intish(row.get("volume"))})
    return quote_from_points(points, "nasdaq", NASDAQ_PAGE, url)


def produce_quote(_old) -> dict:
    errors = []
    try:
        quote = fetch_yahoo_quote()
        quote["fallback_error"] = None
        return quote
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Yahoo: {exc}")
        print(f"  Yahoo unavailable: {exc}")
    try:
        quote = fetch_nasdaq_quote()
        quote["fallback_error"] = "; ".join(errors)
        return quote
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Nasdaq: {exc}")
        raise RuntimeError("; ".join(errors)) from exc


def fetch_short_interest() -> dict:
    status, body = http_get(NASDAQ_SI, {**BROWSER_HEADERS, "Accept": "application/json"}, timeout=30)
    if status != 200:
        raise RuntimeError(f"Nasdaq short interest HTTP {status}")
    payload = json.loads(body.decode("utf-8"))
    rows = (((payload.get("data") or {}).get("shortInterestTable") or {}).get("rows")) or []
    history = []
    for row in rows:
        day = parse_any_date(row.get("settlementDate"))
        shares = parse_intish(row.get("interest"))
        if not day or shares is None:
            continue
        history.append(
            {
                "settlement_date": day,
                "shares_short": shares,
                "avg_daily_volume": parse_intish(row.get("avgDailyShareVolume")),
                "days_to_cover": parse_money(row.get("daysToCover")),
            }
        )
    if not history:
        raise RuntimeError("short interest table empty")
    latest = history[0]
    return {
        "ok": True,
        "stale": False,
        "fetch_error": None,
        "fetched_at": utcnow(),
        "source_label": "Nasdaq",
        "source_url": NASDAQ_SI_PAGE,
        "source_api": NASDAQ_SI,
        "latest": latest,
        "history": history[:8],
    }


def fetch_finra(days: int = 20) -> dict:
    found = []
    cursor = date.today()
    checked = 0
    while len(found) < days and checked < 50:
        checked += 1
        ymd = cursor.strftime("%Y%m%d")
        url = FINRA_TMPL.format(ymd=ymd)
        status, body = http_get(url, BROWSER_HEADERS, timeout=20, retries=1)
        if status == 200:
            text = body.decode("utf-8", "replace")
            for line in text.splitlines():
                parts = line.split("|")
                if len(parts) >= 6 and parts[1] == "OPEN":
                    short_volume = float(parts[2])
                    exempt = float(parts[3])
                    total = float(parts[4])
                    found.append(
                        {
                            "date": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}",
                            "short_volume": short_volume,
                            "short_exempt_volume": exempt,
                            "total_volume": total,
                            "short_ratio": (short_volume / total) if total else None,
                            "markets": parts[5],
                            "source_url": url,
                        }
                    )
                    break
        cursor -= timedelta(days=1)
    if not found:
        raise RuntimeError("no FINRA CNMS rows for OPEN")
    found.sort(key=lambda row: row["date"])
    return {
        "ok": True,
        "stale": False,
        "fetch_error": None,
        "fetched_at": utcnow(),
        "source_label": "FINRA Reg SHO CNMS",
        "source_url": found[-1]["source_url"],
        "directory_url": "https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data/daily-short-sale-volume-files",
        "rows": found,
    }


def produce_short(old) -> dict:
    old = old or {}
    si = None
    si_error = None
    finra = None
    finra_error = None
    try:
        si = fetch_short_interest()
    except Exception as exc:  # noqa: BLE001
        si_error = f"{type(exc).__name__}: {exc}"
        print(f"  short interest fail: {si_error}", file=sys.stderr)
    try:
        finra = fetch_finra()
    except Exception as exc:  # noqa: BLE001
        finra_error = f"{type(exc).__name__}: {exc}"
        print(f"  FINRA fail: {finra_error}", file=sys.stderr)
    if si is None:
        previous = old.get("short_interest")
        if previous and previous.get("ok"):
            si = previous
            si["stale"] = True
            si["fetch_error"] = si_error
        else:
            si = {"ok": False, "stale": False, "fetch_error": si_error, "fetched_at": None, "latest": None, "history": []}
    if finra is None:
        previous = old.get("finra")
        if previous and previous.get("ok"):
            finra = previous
            finra["stale"] = True
            finra["fetch_error"] = finra_error
        else:
            finra = {"ok": False, "stale": False, "fetch_error": finra_error, "fetched_at": None, "rows": []}
    if not si.get("ok") and not finra.get("ok") and not si.get("stale") and not finra.get("stale"):
        raise RuntimeError(f"{si_error}; {finra_error}")
    return {
        "source_label": "Nasdaq 未平仓做空 与 FINRA 每日卖空量",
        "source_url": NASDAQ_SI_PAGE,
        "short_interest": si,
        "finra": finra,
        "note": "未平仓做空来自交易所结算数据，大约每两周一次。每日比例是 FINRA Reg SHO 卖空成交量除以当日总成交量，两者不是同一指标。",
    }


def produce_news(_old) -> dict:
    status, body = http_get(NEWS_RSS, BROWSER_HEADERS, timeout=30)
    if status != 200:
        raise RuntimeError(f"Google News RSS HTTP {status}")
    root = ET.fromstring(body)
    items = []
    seen = set()
    for item in root.findall("./channel/item"):
        title = " ".join((item.findtext("title") or "").split())
        if "opendoor" not in title.lower():
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        source = item.find("source")
        published = item.findtext("pubDate")
        published_at = None
        if published:
            dt = email.utils.parsedate_to_datetime(published)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            published_at = dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        items.append(
            {
                "title": title,
                "url": (item.findtext("link") or "").strip() or None,
                "source": source.text.strip() if source is not None and source.text else None,
                "source_home": source.get("url") if source is not None else None,
                "published_at": published_at,
            }
        )
    items.sort(key=lambda row: row["published_at"] or "", reverse=True)
    items = items[:15]
    if not items:
        raise RuntimeError("RSS had no Opendoor headlines")
    return {
        "source_label": "Google News RSS",
        "source_url": NEWS_RSS,
        "note": "标题是媒体原文，本站不改写，也不代表本站观点。",
        "items": items,
    }


def source_status(name: str, label: str, payload: dict, error: str | None, url: str | None) -> dict:
    return {
        "id": name,
        "label": label,
        "ok": bool(payload.get("ok")),
        "stale": bool(payload.get("stale")),
        "error": error or payload.get("fetch_error"),
        "fetched_at": payload.get("fetched_at"),
        "fetch_attempted_at": payload.get("fetch_attempted_at"),
        "source_url": url or payload.get("source_url"),
    }


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    # briefing.json is intentionally not written here.
    print("fetching quote")
    quote, quote_err = refresh(DATA / "quote.json", produce_quote)
    print("fetching accountable")
    accountable, acc_err = refresh(DATA / "accountable.json", produce_accountable)
    print("fetching filings")
    filings, fil_err = refresh(DATA / "filings.json", produce_filings)
    print("fetching insiders")
    insiders, ins_err = refresh(DATA / "insiders.json", produce_insiders)
    print("fetching holders")
    holders, hold_err = refresh(DATA / "holders.json", produce_holders)
    print("fetching short data")
    short, short_err = refresh(DATA / "short.json", produce_short)
    print("fetching news")
    news, news_err = refresh(DATA / "news.json", produce_news)

    if accountable.get("ok"):
        week = next((row for row in accountable.get("weeks") or [] if row.get("week_ending") == "2026-11-07"), None)
        if week:
            print(f"sanity week 2026-11-07 contracts={week.get('contracts')}")
        else:
            print("sanity week 2026-11-07 not in current series")

    status = {
        "ok": True,
        "fetched_at": utcnow(),
        "sources": [
            source_status("quote", "股价", quote, quote_err, quote.get("source_url")),
            source_status("accountable", "Opendoor Accountable", accountable, acc_err, ACCOUNTABLE_URL),
            source_status("filings", "公司公告", filings, fil_err, EDGAR_BROWSE),
            source_status("insiders", "内部人交易", insiders, ins_err, EDGAR_BROWSE),
            source_status("holders", "机构持仓", holders, hold_err, EDGAR_BROWSE),
            source_status("short", "做空数据", short, short_err, NASDAQ_SI_PAGE),
            source_status("news", "新闻", news, news_err, NEWS_RSS),
            source_status("briefing", "每日简报", load_json(DATA / "briefing.json") or {"ok": False}, None, None),
        ],
    }
    save_json(DATA / "status.json", status)
    failed = [row["label"] for row in status["sources"] if row["id"] != "briefing" and not row["ok"]]
    stale = [row["label"] for row in status["sources"] if row["stale"]]
    print("done", "failed=" + (",".join(failed) or "none"), "stale=" + (",".join(stale) or "none"))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
