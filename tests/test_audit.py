from src.audit_chain import GENESIS, compute_hash, payload, verify_rows


def make_rows(n=10):
    rows, prev = [], GENESIS
    for i in range(n):
        r = {"id": i + 1, "chamber_id": 1, "ts": 1000.0 + 60 * i, "temp": 3.0 + i * 0.01, "humidity": 90.0, "door": 0, "comp": 1, "prev_hash": prev}
        r["hash"] = compute_hash(prev, payload(1, r["ts"], r["temp"], r["humidity"], r["door"], r["comp"]))
        prev = r["hash"]
        rows.append(r)
    return rows


def test_valid_chain():
    assert verify_rows(make_rows())["ok"]


def test_edited_reading_detected():
    rows = make_rows()
    rows[4]["temp"] += 2.0
    res = verify_rows(rows)
    assert not res["ok"] and res["first_bad_id"] == 5


def test_removed_reading_detected():
    rows = make_rows()
    del rows[3]
    assert not verify_rows(rows)["ok"]
