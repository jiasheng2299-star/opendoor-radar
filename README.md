# Opendoor Radar

Opendoor Technologies（NASDAQ: OPEN）的公开数据看板。页面是简体中文，时钟时间用马来西亚时间（MYT，UTC+8）。

站点只读取 `data/` 里的 JSON，不在浏览器里直接请求券商或 SEC。数字、来源链接和更新时间都来自这些文件。抓取失败时，脚本保留上一份成功结果，页面显示「暂无数据」，并标明上次成功的时间。

**仅汇总公开数据，不构成投资建议。**

线上地址：<https://jiasheng2299-star.github.io/opendoor-radar/>

## 板块

| 板块 | 文件 | 内容 |
| --- | --- | --- |
| 每日简报 | `data/briefing.json` | 日期和带链接的条目。当前是占位，等下一次 08:00 MYT 更新时替换整个文件 |
| 股价 | `data/quote.json` | 最近收盘、涨跌额、涨跌幅、成交量，以及约 6 个月收盘价 |
| Opendoor Accountable | `data/accountable.json` | 每周收购合约（协议，不是已完成的购买）和产品 / 功能 / 合作发布 |
| 公司公告 | `data/filings.json` | SEC 最新申报。8-K、10-Q、10-K、S-3、424B、DEF 14A 会标出。Form 3/4/5 和 144 不在这里 |
| 内部人交易 | `data/insiders.json` | 最近 Form 4：姓名、职务、交易代码、股数、价格、10b5-1 标记 |
| 机构持仓 | `data/holders.json` | 已核对的 2026-06-30 13F 快照，以及 EDGAR 上的 13G/13D |
| 做空数据 | `data/short.json` | Nasdaq 未平仓做空，以及 FINRA 约 20 个交易日的卖空成交比 |
| 新闻 | `data/news.json` | 标题含 Opendoor 的约 15 条 Google News 结果 |
| 数据状态 | `data/status.json` | 各来源这次是成功、沿用上次，还是没有数据 |

## 数据来源

抓取脚本是 `scripts/fetch.py`，只用 Python 标准库。SEC 请求使用 User-Agent：`opendoor-radar contact@example.com`。

- 股价：先请求 Yahoo 日线 `query1.finance.yahoo.com/v8/finance/chart/OPEN`。若失败，改用 Nasdaq 历史行情 `api.nasdaq.com/api/quote/OPEN/historical`。页面上的价格是最近一个已收盘交易日。
- Accountable：<https://accountable.opendoor.com/> 页面里嵌入的每周收购合约和发布列表。合约按官网口径，是预期收购房产的协议（含 Cash Now, More Later），不扣除之后取消的合约。
- 交叉验证：<https://opendoor.army/charts/weekly>。打不开时不改写官方数字。
- 公告与内部人：<https://data.sec.gov/submissions/CIK0001801169.json>，Form 4 再读 EDGAR XML。
- 13G/13D：同一份 EDGAR 列表里的 Schedule 13G / 13D，再读封面 XML。
- 13F 快照写死在脚本里，不会被自动抓取覆盖：Morgan Stanley 115,205,390 股；Vanguard Portfolio Management 76,214,771 股；BlackRock 72,920,992 股。来源 <https://13fholding.com/stock/open-683712103>，截止日期 2026-06-30。13F 在季末之后最多滞后约 45 天。
- 未平仓做空：<https://api.nasdaq.com/api/quote/OPEN/short-interest?assetclass=stocks>
- 每日卖空量：<https://cdn.finra.org/equity/regsho/daily/CNMSshvolYYYYMMDD.txt> 里的 `OPEN` 行。比例是卖空量除以当日总量。
- 新闻：<https://news.google.com/rss/search?q=Opendoor&hl=en-US&gl=US&ceid=US:en>

## 更新时间

GitHub Actions 工作流 `.github/workflows/update.yml`：

| 触发 | UTC | MYT |
| --- | --- | --- |
| 每天 | 00:00 | 08:00 |
| 每周三 | 00:00 | 08:00 |
| 手动 | Actions 页面里的 Run workflow | 运行当时 |

周三的定时和每天的 00:00 UTC 是同一时刻，用来盖住 Opendoor 在美国时间周二晚间更新 Accountable 之后的那一轮。工作流会在数据有变化时提交 `data/*.json`，然后用 Actions 部署到 GitHub Pages。只改 `data/` 的提交不会再次触发工作流，避免循环。

`data/briefing.json` 不由抓取脚本改写。换成正式简报时保持这个形状：

```json
{
  "ok": true,
  "placeholder": false,
  "date": "2026-11-15",
  "updated_at": "2026-11-15T00:00:00Z",
  "source_label": "来源名称",
  "source_url": "https://example.com",
  "bullets": [
    {"text": "一条说明", "href": "https://example.com/story"}
  ]
}
```

`updated_at` 用 UTC 时间，页面会换成 MYT 并加上 MYT 字样。

## 手动更新

在仓库根目录：

```bash
python3 scripts/fetch.py
```

需要发布时再提交 `data/` 并推到 `main`。也可以打开 GitHub 的 Actions，选 **Update data and deploy Pages**，点 **Run workflow**。

本地查看：

```bash
python3 -m http.server 8765
```

浏览器打开 <http://127.0.0.1:8765/> 。不要用 `file://` 打开，浏览器会拦截对 JSON 的读取。

## GitHub Pages

工作流使用 `actions/configure-pages`（`enablement: true`）、`actions/upload-pages-artifact` 和 `actions/deploy-pages`。`GITHUB_TOKEN` 通常不能第一次打开 Pages。若部署失败，到仓库 **Settings → Pages → Source**，选择 **GitHub Actions**，再重新跑工作流。
