"""FINN push-side pipeline.

  python pipeline.py fetch              # prices + announcements -> SQLite cache + data/*.json
  python pipeline.py digest --hours 72  # ranked digest for any window
  python pipeline.py alerts --vol 2 --move 3 --types results,management_change

Design: plumbing is cheap and cached. Rules classify; an LLM is the fallback, never the default.
Every LLM output is stored by hash(url) so a filing is processed exactly once."""
import argparse, hashlib, json, math, pathlib, re, sqlite3, time, datetime as dt
import requests
PORTFOLIO = ("RELIANCE TCS HDFCBANK INFY ITC SUNPHARMA TATAMOTORS LT BHARTIARTL ASIANPAINT "
             "MARUTI ADANIPORTS TITAN NESTLEIND ZOMATO").split()
DATA = pathlib.Path("data"); DATA.mkdir(exist_ok=True)
db = sqlite3.connect(DATA / "cache.db")
db.execute("create table if not exists kv(k text primary key, v text)")
db.execute("create table if not exists filings(id text primary key, t text, cat text, subject text, ts integer, url text, extra text)")

# ---------- cache ----------
def cached(key, fn):
    h = hashlib.sha1(key.encode()).hexdigest(); r = db.execute("select v from kv where k=?", (h,)).fetchone()
    if r: return json.loads(r[0])
    v = fn(); db.execute("insert or replace into kv values(?,?)", (h, json.dumps(v))); db.commit(); return v

# ---------- classification (rules first, model only for the unsure) ----------
RULES = [("results", r"financial result|quarterly result|outcome of board|unaudited.*result|results presentation"),
 ("management_change", r"appoint|resign|cessation|key managerial|change in director|chief (financial|executive)"),
 ("M&A", r"acqui|merger|amalgam|demerger|joint venture|subsidiary|stake"),
 ("fund_raise", r"\bqip\b|rights issue|preferential|buyback|buy-back|commercial paper|debenture|fund raising"),
 ("litigation", r"penalty|fine imposed|show cause|insolvency|litigation|tribunal|court|sebi order"),
 ("credit_rating", r"credit rating|crisil|icra|care ratings|india ratings"),
 ("dividend", r"dividend"), ("bonus_split", r"bonus issue|stock split|sub-division"),
 ("capex", r"capacity expansion|new plant|capex|greenfield|brownfield"),
 ("earnings_call", r"conference call|earnings call|concall|transcript|audio recording"),
 ("investor_meetings", r"analyst.*meet|investor meet|institutional investor"),
 ("pledging", r"pledge|encumbrance|invocation"),
 ("insider_trading", r"regulation 7|insider|sast|disclosure under regulation 29|trading window"),
 ("postal_ballot", r"postal ballot"), ("agm_egm", r"\bagm\b|\begm\b|annual general meeting"),
 ("press_release", r"press release|order win|bagged|partnership|clarification")]
CATS = [c for c, _ in RULES] + ["unclassified"]
def classify(text, llm=None):
    t = text.lower()
    for cat, rx in RULES:
        if re.search(rx, t): return cat, "rule"
    if llm and len(t) > 40:
        try:
            out = cached("cls:" + t, lambda: llm(f"Classify this exchange filing into one of {CATS}. JSON only: "
                                                f'{{"category":str}}\n\n{text[:600]}'))
            if out.get("category") in CATS: return out["category"], "llm"
        except Exception: pass
    return "unclassified", "rule"

# ---------- end-of-day data ----------
def eod(tickers=PORTFOLIO, lookback=20):
    import yfinance as yf
    out = {}
    for t in tickers:
        for attempt in range(3):
            try:
                h = yf.Ticker(t + ".NS").history(period="3mo"); break
            except Exception: time.sleep(1.5 * (attempt + 1))
        else: continue
        if len(h) < lookback + 2: continue
        last, prev = h.iloc[-1], h.iloc[-2]
        avg = h.Volume.iloc[-lookback - 1:-1].mean()
        out[t] = {"ltp": round(float(last.Close), 2), "chg": round(float((last.Close / prev.Close - 1) * 100), 2),
                  "volx": round(float(last.Volume / avg), 2) if avg else 0, "series": [round(float(x), 2) for x in h.Close.tail(90)]}
    return out

# ---------- announcements ----------
def nse_session():
    s = requests.Session(); s.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Referer": "https://www.nseindia.com/"})
    s.get("https://www.nseindia.com", timeout=15); return s  # the API only answers once the cookie is set
def fetch_filings(tickers=PORTFOLIO, llm=None):
    s = nse_session(); new = 0
    for t in tickers:
        try:
            r = s.get("https://www.nseindia.com/api/corporate-announcements", params={"index": "equities", "symbol": t}, timeout=15)
            rows = r.json()
        except Exception as e:
            print("skip", t, e); continue
        for a in rows[:40]:
            subj = (a.get("desc", "") + ". " + (a.get("attchmntText") or "")).strip()
            ts = int(dt.datetime.strptime(a["an_dt"], "%d-%b-%Y %H:%M:%S").timestamp()) if a.get("an_dt") else int(time.time())
            fid = hashlib.sha1((t + str(a.get("attchmntFile")) + subj).encode()).hexdigest()[:16]
            if db.execute("select 1 from filings where id=?", (fid,)).fetchone(): continue
            cat, how = classify(subj, llm)
            db.execute("insert into filings values(?,?,?,?,?,?,?)", (fid, t, cat, subj, ts, a.get("attchmntFile"), json.dumps({"how": how}))); new += 1
        time.sleep(0.4)  # be polite to the exchange
    db.commit(); return new

# ---------- results extraction: cheap model, only the table pages ----------
KEYS = ("revenue from operations", "profit before tax", "total income", "profit for the period", "depreciation")
def results_pages(pdf_bytes):
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = [(i, p.get_text()) for i, p in enumerate(doc)]
    hits = [(i, t) for i, t in pages if sum(k in t.lower() for k in KEYS) >= 2]
    return hits[:3]
def extract_results(url, llm):
    def go():
        pdf = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"}).content
        ctx = "\n\n".join(f"[page {i+1}]\n{t[:3500]}" for i, t in results_pages(pdf))
        return llm('From these exchange-filed results pages return JSON only: {"unit":"crore|lakh|million","quarters":'
                   '{"curr":{"label":str,"revenue":num,"ebitda":num,"pbt":num,"pat":num},"prev":{...same},"yago":{...same}},"basis":"standalone|consolidated"}. '
                   "EBITDA = PBT + finance cost + depreciation - other income if not stated. Use null if absent.\n\n" + ctx)
    return cached("res:" + url, go)
def deltas(q):
    """QoQ and YoY percentage change for the four headline numbers; validates before showing."""
    rows = []
    for k in ("revenue", "ebitda", "pbt", "pat"):
        c, p, y = (q[x].get(k) for x in ("curr", "prev", "yago"))
        pct = lambda a, b: round((a / b - 1) * 100, 1) if a is not None and b not in (None, 0) else None
        rows.append({"metric": k, "value": c, "qoq": pct(c, p), "yoy": pct(c, y)})
    return rows
def sane(q):
    """Reject extractions where PAT > PBT or EBITDA < PBT (usually a column mix-up)."""
    c = q["curr"]; return all(v is not None for v in (c["pbt"], c["pat"], c["ebitda"])) and c["pat"] <= c["pbt"] <= c["ebitda"]

# ---------- management change: who is the person? ----------
def bio(subject, llm):
    return cached("bio:" + subject, lambda: llm('Extract JSON only: {"name":str,"role":str,"effective":str}. Then, using public professional '
        'information only, add "background": one sentence on prior roles and education, or "unknown" if you cannot verify.\n\n' + subject))

# ---------- priority + alerts ----------
WEIGHT = {"results": 10, "M&A": 9, "management_change": 8, "fund_raise": 8, "litigation": 8, "pledging": 7, "credit_rating": 6, "capex": 6,
          "dividend": 5, "bonus_split": 5, "insider_trading": 4, "earnings_call": 4, "investor_meetings": 3, "press_release": 3,
          "postal_ballot": 2, "agm_egm": 2, "unclassified": 0}
def is_unusual(m, vol=2.0, move=3.0): return bool(m) and (m["volx"] >= vol or abs(m["chg"]) >= move)
def priority(f, market, watched_types=(), watched=(), now=None, vol=2.0, move=3.0):
    """category weight x recency decay + boost when the stock also moved + boost for user-chosen types/stocks."""
    age_h = ((now or time.time()) - f["ts"]) / 3600
    s = WEIGHT.get(f["cat"], 0) * (0.5 + 0.5 * math.exp(-age_h / 48))
    return s + (4 if is_unusual(market.get(f["t"]), vol, move) else 0) + (3 if f["cat"] in watched_types else 0) + (3 if f["t"] in watched else 0)
def digest(hours=24, watched_types=(), watched=(), vol=2.0, move=3.0):
    market = json.load(open(DATA / "eod.json")) if (DATA / "eod.json").exists() else {}
    cut = time.time() - hours * 3600
    rows = [dict(zip(("id", "t", "cat", "subject", "ts", "url", "extra"), r)) for r in db.execute("select * from filings where ts>=?", (cut,))]
    for f in rows: f["score"] = round(priority(f, market, watched_types, watched, vol=vol, move=move), 2); f["routine"] = WEIGHT.get(f["cat"], 0) <= 2
    rows.sort(key=lambda f: -f["score"]); return {"window_hours": hours, "key": [f for f in rows if not f["routine"]], "routine_count": sum(f["routine"] for f in rows)}
def alerts(vol=2.0, move=3.0, types=()):
    market = json.load(open(DATA / "eod.json"))
    hits = [{"t": t, "chg": m["chg"], "volx": m["volx"]} for t, m in market.items() if is_unusual(m, vol, move)]
    fl = [dict(zip(("id", "t", "cat", "subject", "ts", "url", "extra"), r)) for r in db.execute("select * from filings where ts>=?", (time.time() - 86400,))]
    return {"market": hits, "filings": [f for f in fl if f["cat"] in types]}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["fetch", "digest", "alerts"]); ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--vol", type=float, default=2.0); ap.add_argument("--move", type=float, default=3.0); ap.add_argument("--types", default="results,management_change,M&A,fund_raise,litigation")
    a = ap.parse_args()
    if a.cmd == "fetch":
        try:
            import llm; cheap = llm.cheap
        except Exception: cheap = None
        json.dump(eod(), open(DATA / "eod.json", "w")); print("new filings:", fetch_filings(llm=cheap))
    elif a.cmd == "digest": print(json.dumps(digest(a.hours, a.types.split(",")), indent=1)[:4000])
    else: print(json.dumps(alerts(a.vol, a.move, a.types.split(",")), indent=1))
