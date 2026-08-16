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

A browser opens and the script does the rest by itself: opens Advanced Search,
clicks Export once, saves that request as `template.json`, closes the browser.

The only thing it may ask you for is the login, and only when the saved session
has expired (weeks, not runs).

To skip even that, create `C:\Users\<you>\.wyscout\.env` (same folder as the
token — outside the repo):

```
WYSCOUT_EMAIL=you@example.com
WYSCOUT_PASSWORD=yourpassword
```

That's it. `python fetch_wyscout.py --where` tells you the exact path and whether
it found them. Real environment variables of the same name override the file.

Leave it out if you'd rather type the password yourself — the session persists
either way, so it's rare. And if your account uses SSO or 2FA, auto-login won't
work; log in by hand once and the session carries you.

Every run after the first uses `template.json` and opens no browser at all.

### How it opens Advanced Search without clicking

Advanced Search isn't a URL in the platform — it's an app the shell loads in an
iframe. The shell's own app registry gives the recipe:

```json
{"id": "advanced_search",
 "js_command": "ae.getCmp('app').showAdvancedSearchPopUp(arguments[0])",
 "root": "https://wyscout-apps.hudl.com/advanced-search/"}
```

So the script calls that JS, waits for the iframe to appear, reads its `src`
(which carries `access_token`, `groupId`, `subgroupId`), and then loads that URL
as an ordinary page. No clicking, no frame juggling.

### Why it clicks Export once

`COMP_SCOPE` and `TIMEFRAME` never go through the UI — they're set on the
request. The single Export click exists only to capture the `columns` block,
which the page builds from your display preset. Wyscout's catalogue has 51
column definitions that the server expands into your 115 output columns (one
"Duels" definition becomes both "Duels per 90" and "Duels won, %"), so
rebuilding it by hand would not reproduce your export. Copying the real one
does, exactly — and it only happens once.

The layout active on that first run is the layout every CSV gets. To change it:
delete `template.json`, set the layout in Wyscout, run again.

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

## Where your login is stored

Playwright stores nothing itself. No cloud, no Playwright-managed account data.
The only thing it owns is the Chromium binary (`%LOCALAPPDATA%\ms-playwright`),
which holds no user data. Your login goes wherever the script tells Chromium to
put its profile, and that is one setting:

```python
AUTH_DIR = Path(os.environ.get("WYSCOUT_HOME") or Path.home() / ".wyscout")
```

Default is `C:\Users\<you>\.wyscout`, outside the repo. Three things land there:

| | what |
|---|---|
| `browser_profile/` | Chromium profile. Cookies live in `Default\Network\Cookies`; on Windows their values are encrypted with DPAPI tied to your Windows account, so copying the folder to another machine or user won't decrypt them |
| `template.json` | the captured export request. **Plaintext session token** — this is the one that actually matters |
| `.env` | optional, only if you want auto-login. **Plaintext password** |

To put it somewhere else:

```cmd
set WYSCOUT_HOME=C:\Users\hasht\Desktop\wyscout-auth
python fetch_wyscout.py
```

Permanently, so you don't retype it:

```cmd
setx WYSCOUT_HOME "C:\Users\hasht\Desktop\wyscout-auth"
```

PowerShell: `$env:WYSCOUT_HOME = "C:\Users\hasht\Desktop\wyscout-auth"`

The script prints the path it's using on every run, and warns if you point it
back inside the working directory.

One thing about Desktop specifically: on a lot of Windows setups Desktop is
redirected into OneDrive, which would sync your token to the cloud. Check
whether yours is (`echo %USERPROFILE%\Desktop` vs where OneDrive claims it) — if
it is, `C:\Users\hasht\.wyscout` or somewhere under `%LOCALAPPDATA%` is the
better home.

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

**The password is yours to place.** By default the script never handles one —
you type it in the browser window, and it attaches its network listener only
*after* that, so it isn't watching while you type. If you put credentials in
`AUTH_DIR\.env` it will fill the form for you; that's a real trade (plaintext
password on disk) and it's opt-in for that reason. Since the session survives
for weeks, leaving it out costs you a login every so often.

**Nothing secret is written inside the repo.** Both the profile and the token
live in `AUTH_DIR` (see above), outside the working directory. The `Cookie`
header is deliberately not saved into `template.json` — auth here travels in the
query string, so there was no reason to store both.

**Token expiry is automatic.** A 401 deletes `template.json` and reopens the
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

Created on first run in `AUTH_DIR` (default `C:\Users\<you>\.wyscout`), not here:
`template.json` and `browser_profile/`.

## When something breaks

`no competition called '...'` — the name doesn't match Wyscout's. The script
prints near-matches; copy one of those.

`... has no season '2025/2026'` — it lists the seasons that league does have.

`token expired — recapturing` — normal, a browser will open.

`expected an xlsx, got application/json` — the request was rejected. Delete
`template.json` and run again.
