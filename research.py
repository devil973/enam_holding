"""FINN pull-side research engine. Works for any NSE ticker that has a screener.in page.

  python research.py INFY            # build data/research/INFY.json
  python research.py INFY --ask "why did attrition rise?"

PRIMARY SOURCES ONLY: annual reports + concall transcripts. No news, broker notes or the model's own knowledge.
Every claim must carry a document and page; uncited claims are dropped by validate()."""
import argparse, hashlib, json, math, pathlib, re, collections, requests
UA = {"User-Agent": "Mozilla/5.0"}
CACHE = pathlib.Path("cache"); CACHE.mkdir(exist_ok=True)
OUT = pathlib.Path("data/research"); OUT.mkdir(parents=True, exist_ok=True)

# ---------- 1. find the primary documents ----------
def screener_docs(ticker):
    from bs4 import BeautifulSoup
    for path in ("consolidated/", ""):
        r = requests.get(f"https://www.screener.in/company/{ticker}/{path}", headers=UA, timeout=20)
        if r.ok: break
    else: raise RuntimeError(f"{ticker}: no screener.in page")
    soup = BeautifulSoup(r.text, "html.parser"); docs = {"annual": [], "concall": []}
    for a in soup.select("a[href]"):
        h, t = a["href"], a.get_text(" ", strip=True).lower()
        if not h.startswith("http"): continue
        if "annual" in t or re.search(r"annual.?report", h, re.I): docs["annual"].append({"url": h, "label": a.get_text(" ", strip=True)})
        elif "transcript" in t or "concall" in t or "call" in t and h.endswith(".pdf"): docs["concall"].append({"url": h, "label": a.get_text(" ", strip=True)})
    docs["annual"] = docs["annual"][:2]; docs["concall"] = docs["concall"][:4]  # latest AR + last four calls
    if not docs["concall"] and not docs["annual"]: raise RuntimeError("no documents found; try the company IR site")
    return docs, soup

# ---------- 2. text per page, cached by URL hash ----------
def pages(url):
    f = CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".json")
    if f.exists(): return json.loads(f.read_text())
    import fitz
    pdf = requests.get(url, headers=UA, timeout=60).content
    p = [pg.get_text() for pg in fitz.open(stream=pdf, filetype="pdf")]
    f.write_text(json.dumps(p)); return p

# ---------- 3. retrieval: BM25 over page chunks, per output section ----------
TOK = lambda s: re.findall(r"[a-z0-9]+", s.lower())
class Index:
    def __init__(self, chunks):  # chunks: [{"src": "Q2 concall p.4", "text": str}]
        self.c = chunks; self.tf = [collections.Counter(TOK(c["text"])) for c in chunks]
        self.len = [sum(t.values()) or 1 for t in self.tf]; self.avg = sum(self.len) / max(len(self.len), 1)
        self.df = collections.Counter(w for t in self.tf for w in t)
    def top(self, query, k=6, k1=1.4, b=0.75):
        N = len(self.c); sc = []
        for i, t in enumerate(self.tf):
            s = 0
            for w in set(TOK(query)):
                if w not in t: continue
                idf = math.log(1 + (N - self.df[w] + .5) / (self.df[w] + .5))
                s += idf * t[w] * (k1 + 1) / (t[w] + k1 * (1 - b + b * self.len[i] / self.avg))
            sc.append((s, i))
        return [self.c[i] for s, i in sorted(sc, reverse=True)[:k] if s > 0]
def build_index(docs):
    ch = []
    for kind in ("concall", "annual"):
        for d in docs[kind]:
            try: ps = pages(d["url"])
            except Exception as e: print("skip", d["label"], e); continue
            for i, t in enumerate(ps):
                if len(t) > 200: ch.append({"src": f"{d['label']} p.{i+1}", "text": t[:3000], "kind": kind})
    return Index(ch)

# ---------- 4. the eight outputs, each with its own retrieval query ----------
SECTIONS = {
 "business":  ("what the company does segments revenue mix business overview", "cheap"),
 "financials":("revenue EBITDA margin profit after tax ROCE key ratios", "cheap"),
 "trajectory":("five year growth revenue margin trend compound annual", "cheap"),
 "balance_sheet": ("borrowings debt equity net worth working capital receivable days inventory", "cheap"),
 "cash_quality":  ("cash flow from operations free cash flow capex cash conversion", "cheap"),
 "guidance":  ("we expect guidance outlook target growth margin capex next year aim", "strong"),
 "delivered": ("achieved reported actual quarter growth margin delivered", "strong"),
 "narrative_vs_numbers": ("management commentary demand margin outlook versus reported results", "strong"),
}
RULES = ("Use ONLY the excerpts. Every item needs a 'src' copied exactly from the excerpt header. "
         "If the excerpts do not support a claim, leave it out. Do not use outside knowledge. JSON only.")
SHAPES = {"business": '{"summary":str,"segments":[{"name":str,"pct":num,"src":str}]}',
 "financials": '{"items":[{"label":str,"value":str,"src":str}]}',
 "trajectory": '{"years":[str],"revenue":[num],"ebitda_margin":[num],"read":str,"src":str}',
 "balance_sheet": '{"items":[{"label":str,"value":str,"tone":"good|watch","src":str}]}',
 "cash_quality": '{"years":[{"fy":str,"cfo":num,"pat":num}],"read":str,"src":str}',
 "guidance": '{"items":[{"quarter":str,"metric":str,"promised":str,"quote":str,"src":str}]}',
 "delivered": '{"items":[{"quarter":str,"metric":str,"actual":str,"src":str}]}',
 "narrative_vs_numbers": '{"items":[{"claim":str,"agrees":bool,"evidence":str,"src":str}]}'}
def validate(obj):
    """Drop any item with no source: an uncited claim is worse than a missing one."""
    if isinstance(obj, dict):
        if "items" in obj: obj["items"] = [i for i in obj["items"] if i.get("src")]
        if "segments" in obj: obj["segments"] = [i for i in obj["segments"] if i.get("src")]
    return obj
def pair(guid, deliv):
    """Join guided metrics with delivered ones by metric name; the front-end's 'promised vs delivered' table."""
    d = {x["metric"].lower(): x for x in deliv.get("items", [])}; rows = []
    for g in guid.get("items", []):
        a = d.get(g["metric"].lower()); rows.append({**g, "actual": a["actual"] if a else None, "actual_src": a["src"] if a else None})
    return rows
def run(ticker, force=False):
    import llm
    f = OUT / f"{ticker}.json"
    if f.exists() and not force: return json.loads(f.read_text())
    docs, _ = screener_docs(ticker); idx = build_index(docs); res = {"ticker": ticker, "docs": docs}
    for name, (q, tier) in SECTIONS.items():
        ex = "\n\n".join(f"[{c['src']}]\n{c['text']}" for c in idx.top(q))
        model = llm.cheap if tier == "cheap" else llm.strong
        res[name] = validate(model(f"Section: {name}. {RULES}\nReturn {SHAPES[name]}\n\nEXCERPTS:\n{ex}"))
    res["promised_vs_delivered"] = pair(res["guidance"], res["delivered"])
    ev = "\n".join(f"{x['metric']}: promised {x['promised']}, actual {x['actual']}" for x in res["promised_vs_delivered"] if x["actual"])
    res["bull_bear"] = validate(llm.strong(f"Using ONLY these findings write a tight bull and bear case, each 2-3 points, each with a src. "
        f'JSON only: {{"bull":[{{"t":str,"src":str}}],"bear":[{{"t":str,"src":str}}]}}\n\n{ev}\n{json.dumps(res["narrative_vs_numbers"])[:3000]}'))
    f.write_text(json.dumps(res, indent=1)); return res
def ask(ticker, question):
    """Chat grounded in the same index. Refuses when the documents are silent."""
    import llm
    idx = build_index(screener_docs(ticker)[0]); ex = idx.top(question, 5)
    if not ex: return {"answer": "Not covered in the cached documents.", "src": []}
    return llm.cheap(f"Answer using ONLY these excerpts, one or two sentences, cite src. If not answerable say so. "
                     f'JSON only: {{"answer":str,"src":[str]}}\nQ: {question}\n\n' + "\n\n".join(f"[{c['src']}]\n{c['text']}" for c in ex))
if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("ticker"); ap.add_argument("--ask"); ap.add_argument("--force", action="store_true"); a = ap.parse_args()
    print(json.dumps(ask(a.ticker.upper(), a.ask) if a.ask else run(a.ticker.upper(), a.force), indent=1)[:5000])
