# Opendoor Radar

Public data dashboard for Opendoor Technologies (NASDAQ: OPEN). The page is in English. Clock times use US Eastern time (America/New_York, labelled ET), including daylight saving.

The site only reads JSON from `data/`. It does not call brokers or the SEC from the browser. Figures, source links, and update times come from those files. If a fetch fails, the script keeps the last successful file and the page shows "No data" together with that file's time.

**Public data summary only. Not investment advice.**

Live URL: <https://jiasheng2299-star.github.io/opendoor-radar/>

## Sections

| Section | File | Contents |
| --- | --- | --- |
| Daily briefing | `data/briefing.json` | English bullets generated from the other files, in importance order. Earlier days are in `data/briefings/` |
| Price | `data/quote.json` | Last close, dollar and percent change, volume, and about six months of closes |
| Opendoor Accountable | `data/accountable.json` | Weekly acquisition contracts (agreements, not completed purchases) and product, feature, and partnership launches |
| Company filings | `data/filings.json` | Latest SEC filings. 8-K, 10-Q, 10-K, S-3, 424B, and DEF 14A are highlighted. Form 3/4/5 and 144 are not in this list |
| Insider trades | `data/insiders.json` | Recent Form 4 filings: name, title, transaction code, shares, price, 10b5-1 flag |
| Institutional holdings | `data/holders.json` | Checked 2026-06-30 13F snapshot, plus 13G/13D filings from EDGAR |
| Short interest | `data/short.json` | Nasdaq short interest and about 20 trading days of FINRA short-volume ratios |
| News | `data/news.json` | About 15 Google News headlines that mention Opendoor |
| Data status | `data/status.json` | Whether each source succeeded, kept the last good file, or has no data |

## Data sources

The fetch script is `scripts/fetch.py`. It uses only the Python standard library. SEC requests use the User-Agent `opendoor-radar contact@example.com`.

- Price: Yahoo daily bars at `query1.finance.yahoo.com/v8/finance/chart/OPEN` first. If that fails, Nasdaq history at `api.nasdaq.com/api/quote/OPEN/historical`. The price on the page is the last completed session.
- Accountable: the weekly acquisition contracts and launch list embedded in <https://accountable.opendoor.com/>. Contracts are agreements under which Opendoor expects to buy a property, including Cash Now, More Later. Later cancellations are not removed.
- Cross-check: <https://opendoor.army/charts/weekly>. If it cannot be opened, the official numbers are left unchanged.
- Filings and insiders: <https://data.sec.gov/submissions/CIK0001801169.json>, then Form 4 XML from EDGAR.
- 13G/13D: Schedule 13G and 13D filings from that same EDGAR list, then the cover-page XML.
- The 13F snapshot is a constant in the script and is not replaced by an automatic scrape: Morgan Stanley 115,205,390 shares; Vanguard Portfolio Management 76,214,771; BlackRock 72,920,992. Source <https://13fholding.com/stock/open-683712103>, as of 2026-06-30. 13F filings can lag up to 45 days after quarter end.
- Short interest: <https://api.nasdaq.com/api/quote/OPEN/short-interest?assetclass=stocks>
- Daily short volume: the `OPEN` row in <https://cdn.finra.org/equity/regsho/daily/CNMSshvolYYYYMMDD.txt>. The ratio is short volume divided by that file's total volume.
- News: <https://news.google.com/rss/search?q=Opendoor&hl=en-US&gl=US&ceid=US:en>

## Daily briefing

Every fetch writes `data/briefing.json` in English from the files it just saved. Bullets stay in this order:

1. Last close, dollar change, percent change, and volume. If the absolute move is over 10%, the bullet says so.
2. SEC filings that were not in the previous briefing. 8-K, 10-Q, 10-K, S-3, 424B, 13D, and 13G are marked as highlighted.
3. Form 4 trades that were not in the previous briefing: who, title, buy/sell/award (or the other filed code), shares, price, and the 10b5-1 flag.
4. The latest Accountable week, the week-over-week change, and any launches that were not already recorded. On the first run, only launches from the past 14 days are listed.
5. Exchange short interest when the settlement date is new, plus the latest FINRA short-volume ratio.
6. Up to five news headlines that were not in the previous briefing.

Each bullet links to its source. A category with nothing new says `No new updates`. A category with no file says `No data`. The script does not invent figures. The file's briefing date is still the calendar date the fetch writes; the page shows that instant in ET.

What counts as new is stored in `data/seen.json` (accession numbers, Form 4 trade keys, 13F snapshot date, Accountable weeks and launch ids, short-interest settlement dates, FINRA dates, and news URLs). The next run only reports identifiers that are not already in that file. If `seen.json` is missing, the fallback is items dated within the last calendar day in US Eastern time; a Saturday, Sunday, or Monday run looks back to the prior weekday. The price line is always included.

The previous day's briefing is copied to `data/briefings/YYYY-MM-DD.json`. `data/briefings/index.json` lists the latest seven dates, and the page shows the earlier ones under the current briefing.

## Schedule

GitHub Actions workflow `.github/workflows/update.yml`:

| Trigger | UTC | ET |
| --- | --- | --- |
| Every day | 00:00 | 19:00 previous evening (EST) or 20:00 (EDT) |
| Every Wednesday | 00:00 | Same clock time as the daily run |
| Manual | Run workflow on the Actions page | The time the run starts |

The Wednesday schedule is the same clock time as the daily run, so it sits after Opendoor's Tuesday-evening US Accountable update. The workflow commits `data/` when it changes, then deploys to GitHub Pages. A commit that only changes `data/` does not start the workflow again.

## Update by hand

From the repository root:

```bash
python3 scripts/fetch.py
```

Commit `data/` and push to `main` when you want it published. Or open Actions, choose **Update data and deploy Pages**, and click **Run workflow**.

Local preview:

```bash
python3 -m http.server 8765
```

Open <http://127.0.0.1:8765/>. Do not open the file directly; the browser will block the JSON reads.

## GitHub Pages

The workflow uses `actions/configure-pages` (`enablement: true`), `actions/upload-pages-artifact`, and `actions/deploy-pages`. `GITHUB_TOKEN` usually cannot enable Pages the first time. If the deploy fails, open **Settings → Pages → Source**, choose **GitHub Actions**, and rerun the workflow.
