#!/usr/bin/env python3
"""Fetch public Opendoor (NASDAQ: OPEN) data into data/*.json.

Stdlib only. On failure, keep the last successful payload and mark it stale.
After each run, write an English data/briefing.json from those files and
archive the previous day under data/briefings/.
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
    "quarter_label": "Q2 2026",
    "source_label": "13fholding.com",
    "source_url": "https://13fholding.com/stock/open-683712103",
    "static_seed": True,
    "holders": [
        {"name": "Morgan Stanley", "shares": 115205390},
        {"name": "Vanguard Portfolio Management", "shares": 76214771},
        {"name": "BlackRock", "shares": 72920992},
    ],
}

ITEM_EN = {
    "1.01": "entry into a material agreement",
    "1.02": "termination of a material agreement",
    "1.03": "bankruptcy or receivership",
    "2.01": "completion of an acquisition or disposition of assets",
    "2.02": "results of operations and financial condition",
    "2.03": "creation of a direct financial obligation",
    "2.04": "triggering events that accelerate a direct financial obligation",
    "3.01": "notice of delisting or failure to satisfy a listing rule",
    "3.02": "unregistered sales of equity securities",
    "3.03": "material modification of security-holder rights",
    "5.02": "departure or appointment of directors or officers",
    "5.03": "amendments to articles or bylaws",
    "5.07": "submission of matters to a vote of security holders",
    "7.01": "Regulation FD disclosure",
    "8.01": "other events",
    "9.01": "financial statements and exhibits",
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
            gloss = ITEM_EN.get(code)
            parts.append(f"{code} {gloss}" if gloss else code)
    if parts:
        return "Items " + "; ".join(parts)
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
        "note": "Form 3/4/5 and Form 144 are omitted here and listed under Insider trades. Filing dates are the EDGAR dates, with no timezone conversion.",
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
        "note": "Transaction codes and 10b5-1 flags are copied from the filing. They describe the form, not a recommendation.",
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
        title_parts.append("Officer")
    if flag("isDirector") == "1":
        title_parts.append("Director")
    if flag("isTenPercentOwner") == "1":
        title_parts.append("More than 5% owner")
    if flag("isOther") == "1" and not title_parts:
        title_parts.append("Other")

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
        "source_label": "SEC EDGAR 13G/13D and the checked 13F snapshot",
        "source_url": EDGAR_BROWSE,
        "snapshot_13f": SNAPSHOT_13F,
        "filings_13dg": filings,
        "filings_13dg_stale": stale,
        "filings_13dg_error": error,
        "filings_13dg_fetched_at": dg_fetched_at,
        "note_13f": "13F filings can lag up to 45 days after quarter end. The table above is a checked static snapshot as of 2026-06-30. The automatic job does not replace it with unchecked figures.",
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
                item["parse_error"] = "No cover-page reporting person in the XML"
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
        "contracts_label": "Contracts are agreements under which Opendoor expects to buy a property, including Cash Now, More Later. They are not completed purchases, and later cancellations are not removed.",
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
        return {"ok": False, "source_url": ARMY_URL, "error": "No recognizable weekly contract counts on the page", "latest": None}
    latest = weeks[-1]
    match = next((pair for pair in found if pair[0] == latest["week_ending"]), None)
    if not match:
        return {
            "ok": False,
            "source_url": ARMY_URL,
            "error": "The latest week-ending date was not found",
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
        "source_label": "Nasdaq short interest and FINRA daily short volume",
        "source_url": NASDAQ_SI_PAGE,
        "short_interest": si,
        "finra": finra,
        "note": "Exchange short interest is a settlement figure published about every two weeks. The daily ratio is FINRA Reg SHO short volume divided by that file's total volume. They are not the same measure.",
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
        "note": "Headlines are the publishers' original text. This site does not rewrite them or endorse them.",
        "items": items,
    }


def source_status(name: str, label: str, payload: dict, error: str | None, url: str | None) -> dict:
    return {
        "id": name,
        "label": label,
        "ok": bool(payload.get("ok")),
        "placeholder": bool(payload.get("placeholder")),
        "stale": bool(payload.get("stale")),
        "error": error or payload.get("fetch_error"),
        "fetched_at": payload.get("fetched_at"),
        "fetch_attempted_at": payload.get("fetch_attempted_at"),
        "source_url": url or payload.get("source_url"),
    }


BRIEFINGS = DATA / "briefings"
MYT = ZoneInfo("Asia/Kuala_Lumpur")


def myt_today() -> str:
    return datetime.now(MYT).date().isoformat()


def fmt_price(value) -> str | None:
    if value is None:
        return None
    number = float(value)
    sign = "-" if number < 0 else ""
    text = f"{abs(number):.4f}".rstrip("0").rstrip(".")
    if "." not in text:
        text += ".00"
    elif len(text.split(".")[1]) == 1:
        text += "0"
    whole, frac = text.split(".")
    return f"{sign}${int(whole):,}.{frac}"


def signed_price(value) -> str:
    if value is None:
        return "No data"
    if float(value) > 0:
        return "+" + (fmt_price(value) or "No data")
    return fmt_price(value) or "No data"


def fmt_count(value) -> str | None:
    if value is None:
        return None
    number = float(value)
    if abs(number - round(number)) < 1e-6:
        return f"{int(round(number)):,}"
    return f"{number:,.2f}"


def tx_action(code: str | None) -> str:
    return {
        "P": "buy",
        "S": "sell",
        "A": "award",
        "M": "exercise or conversion",
        "X": "exercise or conversion",
        "F": "tax withholding",
        "G": "gift",
        "C": "conversion",
        "D": "disposition to the issuer",
        "J": "other",
        "V": "voluntary transaction",
        "L": "small acquisition",
        "W": "inheritance",
        "I": "discretionary exercise",
    }.get(code or "", f"code {code}" if code else "transaction")


def plan_phrase(flag: str | None) -> str:
    if flag == "yes":
        return "10b5-1 plan flagged"
    if flag == "footnote":
        return "10b5-1 mentioned in a footnote"
    if flag == "no":
        return "10b5-1 not flagged"
    return "10b5-1 flag not available"


def briefing_highlight(form: str) -> bool:
    text = (form or "").upper().strip()
    compact = text.replace(" ", "")
    if text.startswith(("8-K", "10-Q", "10-K", "S-3", "424B")):
        return True
    return "13D" in compact or "13G" in compact


def prior_briefing(today: str) -> dict | None:
    """Diff against the latest briefing from an earlier MYT date.

    A later run on the same date rebuilds that day's briefing instead of
    treating the morning bullets as already seen.
    """
    prior = None
    if BRIEFINGS.exists():
        older = sorted(path for path in BRIEFINGS.glob("????-??-??.json") if path.stem < today)
        if older:
            prior = load_json(older[-1])
    current = load_json(DATA / "briefing.json")
    if current and not current.get("placeholder") and current.get("date") and current["date"] < today:
        if not prior or current["date"] > (prior or {}).get("date", ""):
            prior = current
    return prior


def archive_previous(previous: dict | None, today: str) -> None:
    if not previous or previous.get("placeholder") or not previous.get("date"):
        return
    if previous["date"] == today:
        return
    BRIEFINGS.mkdir(parents=True, exist_ok=True)
    save_json(BRIEFINGS / f"{previous['date']}.json", previous)


def refresh_briefing_index(today: str) -> None:
    dates = []
    if BRIEFINGS.exists():
        dates = sorted((path.stem for path in BRIEFINGS.glob("????-??-??.json")), reverse=True)
    if today not in dates:
        dates.insert(0, today)
    save_json(BRIEFINGS / "index.json", {"dates": dates[:7]})


def bullet(category: str, text: str, href: str | None) -> dict:
    return {"category": category, "text": text, "href": href}


def news_title(item: dict) -> str:
    title = item.get("title") or "Headline"
    source = item.get("source")
    suffix = f" - {source}" if source else ""
    if suffix and title.endswith(suffix):
        title = title[: -len(suffix)]
    if source:
        return f"{title} ({source})"
    return title


def build_briefing(quote, filings, insiders, accountable, holders, short, news) -> dict:
    today = myt_today()
    now = utcnow()
    previous = prior_briefing(today)
    archive_previous(previous, today)
    seen_prev = (previous or {}).get("seen") or {}
    bullets = []
    seen = {
        "filings": [],
        "insiders": [],
        "launches": [],
        "news": [],
        "contracts_week": None,
        "short_settlement": None,
    }

    if quote.get("ok") and quote.get("last_close") is not None:
        close = fmt_price(quote.get("last_close")) or "No data"
        volume = fmt_count(quote.get("volume")) or "No data"
        pct = quote.get("change_pct")
        pct_text = "No data" if pct is None else f"{float(pct):+.2f}%"
        text = (
            f"Last close {close} on {quote.get('session_date') or 'No data'}, "
            f"change {signed_price(quote.get('change'))} ({pct_text}), "
            f"volume {volume}."
        )
        if pct is not None and abs(float(pct)) > 10:
            text += " The absolute move is over 10%."
        if quote.get("stale"):
            text += " Quote figures are from the last successful fetch."
        bullets.append(bullet("price", text, quote.get("source_url")))
    else:
        bullets.append(bullet("price", "No data", quote.get("source_url")))

    filing_rows = list(filings.get("filings") or [])
    known = {row.get("accession") for row in filing_rows}
    for row in holders.get("filings_13dg") or []:
        if row.get("accession") not in known:
            filing_rows.append(
                {
                    "form": row.get("form"),
                    "filing_date": row.get("filing_date"),
                    "description": row.get("form") or "",
                    "accession": row.get("accession"),
                    "url": row.get("url"),
                }
            )
            known.add(row.get("accession"))
    prev_filings = set(seen_prev.get("filings") or [])
    new_filings = []
    for row in filing_rows:
        accession = row.get("accession")
        if accession:
            seen["filings"].append(accession)
        if accession and accession not in prev_filings:
            new_filings.append(row)
    if not filing_rows and not filings.get("ok"):
        bullets.append(bullet("filings", "No data", filings.get("source_url") or EDGAR_BROWSE))
    elif not new_filings:
        bullets.append(bullet("filings", "No new updates", filings.get("source_url") or EDGAR_BROWSE))
    else:
        for row in new_filings:
            form = row.get("form") or "Filing"
            filed = row.get("filing_date") or "No data"
            desc = (row.get("description") or "").strip()
            detail = f"{form}: {desc}" if desc and desc.upper() != form.upper() else form
            kind = "Highlighted new SEC filing" if briefing_highlight(form) else "New SEC filing"
            bullets.append(bullet("filings", f"{kind} {detail}, filed {filed}.", row.get("url")))

    prev_tx = set(seen_prev.get("insiders") or [])
    new_trades = []
    for filing in insiders.get("filings") or []:
        for index, tx in enumerate(filing.get("transactions") or []):
            key = "|".join(
                [
                    str(filing.get("accession") or ""),
                    str(tx.get("date") or ""),
                    str(tx.get("code") or ""),
                    str(tx.get("shares") if tx.get("shares") is not None else ""),
                    str(tx.get("security") or ""),
                    str(index),
                ]
            )
            seen["insiders"].append(key)
            if key in prev_tx:
                continue
            who = filing.get("owner_name") or "No data"
            title = filing.get("owner_title") or "No data"
            shares = fmt_count(tx.get("shares")) or "No data"
            security = f" of {tx['security']}" if tx.get("security") else ""
            price = fmt_price(tx.get("price")) if tx.get("price") is not None else "No data"
            when = tx.get("date") or filing.get("filing_date") or "No data"
            text = (
                f"{who} ({title}) {tx_action(tx.get('code'))}: {shares} shares{security} "
                f"at {price} on {when}. {plan_phrase(tx.get('plan_10b5_1'))}."
            )
            new_trades.append(bullet("insiders", text, filing.get("url")))
    if not (insiders.get("filings") or []) and not insiders.get("ok"):
        bullets.append(bullet("insiders", "No data", insiders.get("source_url") or EDGAR_BROWSE))
    elif not new_trades:
        bullets.append(bullet("insiders", "No new updates", insiders.get("source_url") or EDGAR_BROWSE))
    else:
        bullets.extend(new_trades)

    weeks = accountable.get("weeks") or []
    if not weeks or not accountable.get("ok") and not weeks:
        bullets.append(bullet("accountable", "No data", accountable.get("source_url") or ACCOUNTABLE_URL))
    else:
        latest = weeks[-1]
        seen["contracts_week"] = latest.get("week_ending")
        wow = latest.get("wow")
        wow_text = "no prior published week to compare" if wow is None else f"{int(wow):+,} versus the prior week"
        bullets.append(
            bullet(
                "accountable",
                (
                    f"Latest weekly acquisition contracts: {int(latest['contracts']):,} "
                    f"for the week ending {latest.get('week_ending')} ({wow_text}). "
                    "These are agreements to buy, not completed purchases."
                ),
                accountable.get("source_url"),
            )
        )
        prev_launches = set(seen_prev.get("launches") or [])
        cutoff = None
        if not prev_launches:
            cutoff = (datetime.now(MYT).date() - timedelta(days=14)).isoformat()
        new_launches = []
        for item in accountable.get("launches") or []:
            launch_id = item.get("id") or f"{item.get('date')}:{item.get('title')}"
            seen["launches"].append(str(launch_id))
            if str(launch_id) in prev_launches:
                continue
            if cutoff and (item.get("date") or "") < cutoff:
                continue
            new_launches.append(item)
        for item in new_launches:
            when = item.get("date") or item.get("date_raw") or "No data"
            title = item.get("title") or "No data"
            tag = f" [{item['tag']}]" if item.get("tag") else ""
            bullets.append(
                bullet(
                    "accountable",
                    f"New launch{tag} on {when}: {title}.",
                    accountable.get("source_url"),
            )
        )

    si = (short or {}).get("short_interest") or {}
    finra = (short or {}).get("finra") or {}
    latest_si = si.get("latest")
    prev_settlement = seen_prev.get("short_settlement")
    if latest_si and latest_si.get("settlement_date"):
        seen["short_settlement"] = latest_si.get("settlement_date")
        if not prev_settlement or prev_settlement != latest_si.get("settlement_date"):
            days = latest_si.get("days_to_cover")
            days_text = "No data" if days is None else f"{float(days):.2f}"
            bullets.append(
                bullet(
                    "short",
                    (
                        f"Exchange short interest {fmt_count(latest_si.get('shares_short')) or 'No data'} shares, "
                        f"settlement date {latest_si.get('settlement_date')}, "
                        f"average daily volume {fmt_count(latest_si.get('avg_daily_volume')) or 'No data'}, "
                        f"days to cover {days_text}."
                    ),
                    si.get("source_url") or NASDAQ_SI_PAGE,
                )
            )
        else:
            bullets.append(bullet("short", "No new updates", si.get("source_url") or NASDAQ_SI_PAGE))
    else:
        bullets.append(bullet("short", "No data", si.get("source_url") or NASDAQ_SI_PAGE))
    finra_rows = finra.get("rows") or []
    if finra_rows:
        last = finra_rows[-1]
        ratio = last.get("short_ratio")
        ratio_text = "No data" if ratio is None else f"{float(ratio) * 100:.1f}%"
        bullets.append(
            bullet(
                "short",
                (
                    f"Latest FINRA short-volume ratio {ratio_text} on {last.get('date') or 'No data'} "
                    f"(short volume {fmt_count(last.get('short_volume')) or 'No data'}, "
                    f"total volume {fmt_count(last.get('total_volume')) or 'No data'})."
                ),
                last.get("source_url") or finra.get("source_url"),
            )
        )
    elif not latest_si:
        pass
    else:
        bullets.append(bullet("short", "No data", finra.get("source_url")))

    prev_news = set(seen_prev.get("news") or [])
    fresh_news = []
    for item in news.get("items") or []:
        key = item.get("url") or item.get("title")
        if key:
            seen["news"].append(key)
        if key and key not in prev_news:
            fresh_news.append(item)
    if not (news.get("items") or []) and not news.get("ok"):
        bullets.append(bullet("news", "No data", news.get("source_url") or NEWS_RSS))
    elif not fresh_news:
        bullets.append(bullet("news", "No new updates", news.get("source_url") or NEWS_RSS))
    else:
        for item in fresh_news[:5]:
            bullets.append(bullet("news", news_title(item), item.get("url")))

    for key in ("filings", "insiders", "launches", "news"):
        seen[key] = sorted(set(seen[key]))
    return {
        "ok": True,
        "placeholder": False,
        "stale": False,
        "date": today,
        "updated_at": now,
        "fetched_at": now,
        "fetch_error": None,
        "source_label": "This site's fetch",
        "source_url": None,
        "bullets": bullets,
        "seen": seen,
    }


def write_briefing(quote, filings, insiders, accountable, holders, short, news) -> dict:
    payload = build_briefing(quote, filings, insiders, accountable, holders, short, news)
    save_json(DATA / "briefing.json", payload)
    BRIEFINGS.mkdir(parents=True, exist_ok=True)
    save_json(BRIEFINGS / f"{payload['date']}.json", payload)
    refresh_briefing_index(payload["date"])
    print(f"briefing {payload['date']} bullets={len(payload['bullets'])}")
    return payload


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
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

    print("writing briefing")
    briefing = write_briefing(quote, filings, insiders, accountable, holders, short, news)
    status = {
        "ok": True,
        "fetched_at": utcnow(),
        "sources": [
            source_status("quote", "Price", quote, quote_err, quote.get("source_url")),
            source_status("accountable", "Opendoor Accountable", accountable, acc_err, ACCOUNTABLE_URL),
            source_status("filings", "Company filings", filings, fil_err, EDGAR_BROWSE),
            source_status("insiders", "Insider trades", insiders, ins_err, EDGAR_BROWSE),
            source_status("holders", "Institutional holdings", holders, hold_err, EDGAR_BROWSE),
            source_status("short", "Short interest", short, short_err, NASDAQ_SI_PAGE),
            source_status("news", "News", news, news_err, NEWS_RSS),
            source_status("briefing", "Daily briefing", briefing, None, None),
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
