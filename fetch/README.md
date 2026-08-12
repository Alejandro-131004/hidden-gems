# fetch

Download a whole Wyscout league into one CSV. No 500-row limit.

## Install

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Use

Open `fetch_wyscout.py`, edit the two lines at the top:

```python
COMP_SCOPE = "Germany. 2. Bundesliga"   # "<Country>. <League>"
TIMEFRAME  = "2025/2026"
```

Run it:

```bash
python fetch_wyscout.py
```

CSV lands in `out/Germany_2_Bundesliga__2025_2026.csv`.

Several leagues at once — fill in `BATCH` and `COMP_SCOPE` is ignored:

```python
BATCH = ["Germany. 2. Bundesliga", "Germany. 3. Liga", "Portugal. Liga 3"]
```

## First run

A browser opens. Log in, go to Advanced Search, set the column layout you want,
press ENTER in the terminal. The script clicks Export once, keeps a copy of that
request in `template.json`, and closes the browser.

Every run after that uses `template.json` and never opens a browser. When the
token in it expires the script deletes it and reopens the browser by itself.

The layout you have on screen during that first run is the layout every CSV gets.
To change it: delete `template.json`, run again, pick a different layout.

## Why it needs that one browser step

The export request carries a `columns` block built inside the page from your
display preset. Wyscout's column catalogue has 51 definitions which the server
expands into your 115 output columns — one "Duels" definition becomes both
"Duels per 90" and "Duels won, %". Rebuilding that block by hand would not
reproduce your export. Copying the real one does, exactly.

## How it works

The Export button is one POST. From Wyscout's own `app-bundle.js`:

```js
var c = {search: {...filters}, count: 500, page: 0, sort, columns, language}
fetch("https://searchapi.wyscout.com/api/v1/search/export.xlsx", {method:"POST", body: JSON.stringify(c)})
```

`count: 500` is hardcoded in the front-end. It is not a limit on your account or
on the data — it is a number the page always sends. `page` sits right beside it,
so the API pages natively.

So the script takes your captured request and changes four fields:

| field | what it does |
|---|---|
| `search.competition` | league id, e.g. `"635"` (the UI's "Top 5 EU leagues" option is literally `"7,8,9,13,16"`) |
| `search.time_frame` | season id |
| `count` | rows per call |
| `page` | 0, 1, 2 … until a short page comes back |

Then it stacks the pages into one CSV. Your filters, sort and columns are passed
through untouched.

League and season ids come from
`/api/v1/competitions/advanced_search.json?withSeasons=t`, so you write names,
not numbers.

Auth is `?token=…&groupId=…&subgroupId=…` on the URL, taken from the captured
request.

## Safeguards

**Row count is checked against the API.** After downloading, the script asks
`results.json` how many players match those filters and compares. If the league
scope silently failed to apply you would get every player in the database rather
than 644, and this is what catches it. You will see either `row count matches the
API's total (644)` or a loud mismatch line.

**No de-duplication.** Your file has three different players called `M. Schulz`
at Preußen Münster (ages 31, 30, 22). Any dedup on name+team deletes two real
players. Pages are disjoint slices of one ordered list, so duplicates cannot
occur in the first place and dedup would only ever destroy data.

**Column check between pages.** If a page returns different columns from the
first (display changed mid-run), it says so instead of silently producing a
ragged CSV.

**Page loop stops on a short page**, with `MAX_PAGES = 200` as a backstop so a
misbehaving API cannot spin forever.

**1 second between calls.** This is the difference between a normal client and
one that trips rate limiting. If you see HTTP 429 the script stops and tells you
to raise it rather than hammering on.

**No password anywhere.** You log in yourself in the browser window; the session
lives in `.browser_profile/`. Nothing to leak into git.

**`template.json` holds your session token.** It is gitignored. Treat it like a
password. Expiry is handled automatically — 401 deletes it and reopens the
browser.

## Validity check

```bash
python check_vs_sample.py
```

Runs the real pipeline against a fake Wyscout serving your
`data/sample500_bundesliga2.xlsx`, then diffs the CSV it produces against that
file cell by cell. Touches nothing on the network or your account.

```
1. competitions parsed: ['2. Bundesliga', '3. Liga']
2. 'Germany. 2. Bundesliga' + '2025/2026' -> competition=635, season=190685
3. league+season swapped; filters, columns and the template left intact
4. downloaded in 2 call(s) -> Germany_2_Bundesliga__2025_2026.csv

DIFF vs data/sample500_bundesliga2.xlsx
  rows       500 vs   500   OK
  columns    115 vs   115   OK
  cells    57500 compared, 0 different

IDENTICAL — output CSV matches the sample export exactly.

paged version: 2 calls of 300 rows
  rows     500 vs 500
  cells    57500 compared, 0 different
  all 3 'M. Schulz' still present

IDENTICAL across multiple pages too.
```

The second half forces the same 500 rows to arrive as two pages of 300, so the
merge path is proven too, not just the single-call path.

## Files

| file | |
|---|---|
| `fetch_wyscout.py` | the script — settings at the top |
| `check_vs_sample.py` | offline proof against your own export |
| `data/sample500_bundesliga2.xlsx` | reference file for that check |
| `template.json` | created on first run. gitignored, holds your token |
| `.browser_profile/` | created on first run, keeps you logged in |

## When something breaks

`no competition called '...'` — the name doesn't match Wyscout's. The script
prints near-matches; copy one of those.

`... has no season '2025/2026'` — it lists the seasons that league does have.

`token expired — recapturing` — normal, a browser will open.

`expected an xlsx, got application/json` — the request was rejected. Delete
`template.json` and run again.
