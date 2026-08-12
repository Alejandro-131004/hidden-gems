"""
Runs the real pipeline against a fake Wyscout that serves data/sample500_bundesliga2.xlsx,
then diffs the CSV it produces against that same file, cell by cell.

    python check_vs_sample.py

Nothing here touches the network or your account.
"""
import io, json, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import pandas as pd
import fetch_wyscout as F

SAMPLE = next((p for p in (Path("data/sample500_bundesliga2.xlsx"),
                           Path("../data/sample500_bundesliga2.xlsx"))
               if p.exists()), None)
if SAMPLE is None:
    raise SystemExit("can't find sample500_bundesliga2.xlsx in ./data or ../data")
src = pd.read_excel(SAMPLE, engine="openpyxl")
print(f"comparing against {SAMPLE}")
TOKEN = "TESTTOKEN"
calls = []

class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _guard(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if q.get("token", [None])[0] != TOKEN:
            self.send_response(401); self.end_headers(); self.wfile.write(b"x"); return None
        return q
    def do_GET(self):
        if self._guard() is None: return
        if "competitions" in self.path:
            body = {"areas": [{"name": "Germany", "competitions": [
                {"id": 635, "name": "2. Bundesliga",
                 "seasons": [{"id": 190685, "name": "2025/2026"},
                             {"id": 189000, "name": "2024/2025"}]},
                {"id": 636, "name": "3. Liga", "seasons": [{"id": 190999, "name": "2025/2026"}]}]}]}
        else:
            body = {"paging": {"total_items": len(src)}}
        data = json.dumps(body).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_POST(self):
        if self._guard() is None: return
        b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        calls.append(b)
        size = min(b["count"], 500)
        start = b["page"] * size
        chunk = src.iloc[start:start + size]
        buf = io.BytesIO(); chunk.to_excel(buf, index=False); data = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

srv = HTTPServer(("127.0.0.1", 8799), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:8799"
F.EXPORT_URL = BASE + "/api/v1/search/export.xlsx"
F.RESULTS_URL = BASE + "/api/v1/search/results.json"
F.COMPS_URL = BASE + "/api/v1/competitions/advanced_search.json"
F.PAUSE = 0
F.OUT_DIR = Path("/tmp/check_out")
F.TIMEFRAME = "2025/2026"

# a template shaped like a real captured export request
F.TEMPLATE = Path("/tmp/template_test.json")
F.TEMPLATE.write_text(json.dumps({
    "url": F.EXPORT_URL,
    "query": {"token": TOKEN, "groupId": "42", "subgroupId": "7"},
    "headers": {"Accept": "*/*"},
    "body": {"search": {"competition": "senior", "age": {"min": "15", "max": "45"},
                        "women_mode": False},
             "count": 500, "page": 0, "sort": "market_value_desc",
             "columns": {"0": {"id": "name", "label": "Player"}}, "language": "en"},
}))

t = F.load_template()
s = F.requests.Session()
comps = F.get_competitions(t, s)
assert [c["name"] for c in comps] == ["2. Bundesliga", "3. Liga"], comps
assert comps[0]["area"] == "Germany"
print(f"1. competitions parsed: {[c['name'] for c in comps]}")

cid, season = F.resolve("Germany. 2. Bundesliga", "2025/2026", comps)
assert (cid, season) == (635, 190685), (cid, season)
print(f"2. 'Germany. 2. Bundesliga' + '2025/2026' -> competition={cid}, season={season}")

body = F.build_body(t, cid, season)
assert body["search"]["competition"] == "635"
assert body["search"]["time_frame"] == "190685"
assert body["search"]["youth_stats"] == "false"
assert body["search"]["age"] == {"min": "15", "max": "45"}, "other filters lost"
assert body["columns"] == t["body"]["columns"], "columns lost"
assert t["body"]["search"]["competition"] == "senior", "template mutated"
print("3. league+season swapped; filters, columns and the template left intact")

calls.clear()
assert F.one(t, s, "Germany. 2. Bundesliga", comps)
out = F.OUT_DIR / "Germany_2_Bundesliga__2025_2026.csv"
got = pd.read_csv(out)
print(f"4. downloaded in {len(calls)} call(s) -> {out.name}")

# ---- the actual comparison ----
print()
print(f"DIFF vs {SAMPLE}")
print(f"  rows     {len(got):>5} vs {len(src):>5}   {'OK' if len(got)==len(src) else 'MISMATCH'}")
print(f"  columns  {len(got.columns):>5} vs {len(src.columns):>5}   "
      f"{'OK' if list(got.columns)==list(src.columns) else 'MISMATCH'}")
assert list(got.columns) == list(src.columns)

a = got.reset_index(drop=True)
b = src.reset_index(drop=True)
diff = 0
for c in b.columns:
    x, y = a[c], b[c]
    if pd.api.types.is_numeric_dtype(y) and pd.api.types.is_numeric_dtype(x):
        bad = ~(((x - y).abs() < 1e-9) | (x.isna() & y.isna()))
    else:
        bad = ~((x.astype(str) == y.astype(str)) | (x.isna() & y.isna()))
    n = int(bad.sum())
    if n:
        diff += n
        print(f"  column {c!r}: {n} differing cells")
print(f"  cells    {a.size} compared, {diff} different")
assert diff == 0, f"{diff} cells differ"

srv.shutdown()
print()
print("IDENTICAL — output CSV matches the sample export exactly.")

# ---- and again, forced across multiple pages ----
# Same data, but handed over 300 rows at a time, so the CSV is a merge of 2
# pages instead of 1. Result must still be identical.
srv2 = HTTPServer(("127.0.0.1", 8800), Fake)
threading.Thread(target=srv2.serve_forever, daemon=True).start()
F.EXPORT_URL = "http://127.0.0.1:8800/api/v1/search/export.xlsx"
F.RESULTS_URL = "http://127.0.0.1:8800/api/v1/search/results.json"
t["url"] = F.EXPORT_URL
F.PAGE_SIZE = 300
calls.clear()
F.one(t, s, "Germany. 2. Bundesliga", comps)
multi = pd.read_csv(F.OUT_DIR / "Germany_2_Bundesliga__2025_2026.csv")

print()
print(f"paged version: {len(calls)} calls of {F.PAGE_SIZE} rows")
print(f"  rows     {len(multi)} vs {len(src)}")
assert list(multi.columns) == list(src.columns)
d2 = 0
for c in src.columns:
    x, y = multi[c], src[c]
    if pd.api.types.is_numeric_dtype(y) and pd.api.types.is_numeric_dtype(x):
        bad = ~(((x - y).abs() < 1e-9) | (x.isna() & y.isna()))
    else:
        bad = ~((x.astype(str) == y.astype(str)) | (x.isna() & y.isna()))
    d2 += int(bad.sum())
print(f"  cells    {multi.size} compared, {d2} different")
assert d2 == 0
assert multi["Player"].value_counts().get("M. Schulz", 0) == 3, "duplicate-name players lost"
print("  all 3 'M. Schulz' still present")
srv2.shutdown()
print()
print("IDENTICAL across multiple pages too.")
