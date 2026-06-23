"""Tests for the v2 GUI server (gui/server.py) -- all in-process over localhost.
No window is opened; this exercises the JSON API the frontend consumes."""
import sys, os, json, tempfile, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tuneassist.gui.server import start_server, STATIC_DIR

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
RIDE = os.path.abspath(os.path.join(FIX, "ride42.csv"))


def _client(url):
    def get(p):
        with urllib.request.urlopen(url + p) as r:
            return json.loads(r.read())

    def post(p, body=None, raw=None, headers=None):
        data = raw if raw is not None else json.dumps(body or {}).encode()
        req = urllib.request.Request(url + p, data=data,
                                     headers=headers or {"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    return get, post


def test_static_assets_exist():
    for rel in ("index.html", "css/app.css", "js/app.js", "vendor/echarts.min.js"):
        assert os.path.isfile(os.path.join(STATIC_DIR, rel)), rel


def test_api_end_to_end():
    with tempfile.TemporaryDirectory() as d:
        httpd, url, state = start_server(os.path.join(d, "g.json"))
        try:
            get, post = _client(url)

            # version + presets
            assert get("api/version")["version"]
            pr = get("api/presets")
            assert len(pr["journey"]) == 8 and pr["engines"] and pr["mods"]
            assert any(a["key"] == "gm_gen4_ls" for a in pr["architectures"])

            # garage roundtrip
            post("api/garage/upsert", {"name": "t1", "nickname": "Red",
                                       "platform": "gm", "stoich": 14.7})
            g = get("api/garage")
            assert [v["name"] for v in g["vehicles"]] == ["t1"]
            assert g["vehicles"][0]["platform_label"] == "HP Tuners"

            # analyze by path persists stage + history to the garage
            d2 = post("api/analyze", {"path": RIDE, "vehicle": "t1",
                                      "stoich": 14.7, "tune_spark": True})
            assert d2["stage"] == "TUNE_VE_SD" and d2["findings"]
            assert d2["timeseries"]["t"] and "rpm" in d2["timeseries"]["traces"]
            assert "correction" in d2["tsv"]
            g2 = get("api/garage")
            assert g2["vehicles"][0]["stage"] == "TUNE_VE_SD"
            assert len(g2["vehicles"][0]["history"]) == 1

            # analyze by upload (drag & drop path)
            raw = open(RIDE, "rb").read()
            d3 = post("api/analyze-upload", raw=raw,
                      headers={"Content-Type": "application/octet-stream",
                               "X-Filename": "ride42.csv",
                               "X-Opts": json.dumps({"stoich": 14.7})})
            assert d3["stage"] == "TUNE_VE_SD" and d3["log_name"] == "ride42.csv"

            # rename + delete
            post("api/garage/rename", {"name": "t1", "nickname": "Blue"})
            assert get("api/garage")["vehicles"][0]["nickname"] == "Blue"
            post("api/garage/delete", {"name": "t1"})
            assert get("api/garage")["vehicles"] == []

            # static index served with the token
            with urllib.request.urlopen(url) as r:
                assert b"tuneassist" in r.read()
        finally:
            httpd.shutdown()


def test_ve_axes_round_trip_through_gui():
    # the bug a real user hit: axes saved on upsert but _vehicle_record dropped
    # them, so the frontend never sent them to analyze and the grid stayed default.
    with tempfile.TemporaryDirectory() as d:
        httpd, url, state = start_server(os.path.join(d, "g.json"))
        try:
            get, post = _client(url)
            table = ("%\t400\t800\t1200\t1600\t2000\trpm\n"
                     "20\t1\t2\t3\t4\t5\n40\t1\t2\t3\t4\t5\n"
                     "60\t1\t2\t3\t4\t5\n80\t1\t2\t3\t4\t5\nkPa")
            v = post("api/garage/upsert", {"name": "axc", "platform": "gm",
                                           "stoich": 14.7, "ve_axes": {"table": table}})
            # the saved axes MUST come back to the frontend (this was the bug)
            assert v["vehicle"]["ve_axes"] == {"rpm": [400, 800, 1200, 1600, 2000],
                                               "map": [20, 40, 60, 80]}
            assert get("api/garage")["vehicles"][0]["ve_axes"]["rpm"][0] == 400
            # and analyzing with those axes bins the grid onto them (5 RPM cols)
            d2 = post("api/analyze", {"path": RIDE, "vehicle": "axc", "stoich": 14.7,
                                      "ve_axes": {"rpm": [400, 800, 1200, 1600, 2000],
                                                  "map": [20, 40, 60, 80]}})
            assert d2["ve_axes"]["rpm"] == [400, 800, 1200, 1600, 2000]
            rows = d2["tsv"]["correction"].split("\n")
            assert len(rows) == 4 and len(rows[0].split("\t")) == 5   # MAP rows x RPM cols
            # analyzing a saved car must NOT wipe its axes from the record
            assert get("api/garage")["vehicles"][0]["ve_axes"]["map"] == [20, 40, 60, 80]
        finally:
            httpd.shutdown()


def test_bad_token_is_rejected():
    with tempfile.TemporaryDirectory() as d:
        httpd, url, state = start_server(os.path.join(d, "g.json"))
        try:
            base = url.split("/")[0] + "//" + url.split("/")[2]
            try:
                urllib.request.urlopen(base + "/wrongtoken/api/version")
                assert False, "expected 403"
            except urllib.error.HTTPError as e:
                assert e.code == 403
        finally:
            httpd.shutdown()


def test_analyze_missing_file_is_clean_404():
    with tempfile.TemporaryDirectory() as d:
        httpd, url, state = start_server(os.path.join(d, "g.json"))
        try:
            get, post = _client(url)
            try:
                post("api/analyze", {"path": "C:/nope/missing.csv"})
                assert False, "expected 404"
            except urllib.error.HTTPError as e:
                assert e.code == 404
                assert "not found" in json.loads(e.read())["error"]
        finally:
            httpd.shutdown()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS  {name}")
    print("all gui tests passed")
