def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.data == b"ok\n"


def test_index(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"zeitbuch" in r.data


def test_post_ohne_json_ist_415(client):
    r = client.post("/api/info", data="x", content_type="application/x-www-form-urlencoded")
    assert r.status_code == 415


def test_sicherheitsheader(client):
    r = client.get("/api/info")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["Cache-Control"] == "no-store"
