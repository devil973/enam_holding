import sys, pathlib, time; sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pipeline as p, research as r
def test_rules(): assert p.classify("Outcome of Board Meeting - Financial Results Q2")[0] == "results"; assert p.classify("Appointment of CFO")[0] == "management_change"; assert p.classify("ESOP allotment")[0] == "unclassified"
def test_deltas():
    q = {"curr": {"revenue": 110, "ebitda": 30, "pbt": 20, "pat": 15}, "prev": {"revenue": 100, "ebitda": 30, "pbt": 20, "pat": 10}, "yago": {"revenue": 55, "ebitda": 15, "pbt": 10, "pat": 15}}
    d = {x["metric"]: x for x in p.deltas(q)}; assert d["revenue"]["qoq"] == 10.0 and d["revenue"]["yoy"] == 100.0 and d["pat"]["qoq"] == 50.0 and d["pat"]["yoy"] == 0.0; assert p.sane(q)
def test_sane_rejects_mixup(): assert not p.sane({"curr": {"revenue": 1, "ebitda": 5, "pbt": 9, "pat": 3}})
def test_priority_boost():
    f = {"t": "X", "cat": "results", "ts": time.time() - 3600}
    assert p.priority(f, {"X": {"chg": 5, "volx": 3}}) > p.priority(f, {"X": {"chg": 0.1, "volx": 1}})
def test_unusual(): assert p.is_unusual({"chg": -4, "volx": 1}) and p.is_unusual({"chg": 0, "volx": 2.5}) and not p.is_unusual({"chg": 1, "volx": 1})
def test_bm25_finds_page():
    i = r.Index([{"src": "a p.1", "text": "attrition rose to fourteen percent this quarter"}, {"src": "a p.2", "text": "dividend declared by the board"}]); assert i.top("attrition")[0]["src"] == "a p.1"
def test_validate_drops_uncited(): assert len(r.validate({"items": [{"x": 1, "src": "a p.1"}, {"x": 2}]})["items"]) == 1
def test_pair(): assert r.pair({"items": [{"metric": "Margin", "promised": "20%"}]}, {"items": [{"metric": "margin", "actual": "21%", "src": "q p.2"}]})[0]["actual"] == "21%"
