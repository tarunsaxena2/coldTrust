"""Tamper-evident record chain: each reading's hash = SHA-256(previous hash + this reading's data).

Changing any stored reading afterwards breaks its hash (and every link after it), so a storage
certificate can prove that the condition history was not edited. Anchoring the latest hash
outside the database (e.g. printing it on the certificate) makes silent rewriting of the
whole chain detectable too.
"""
import hashlib

GENESIS = "0" * 64


def payload(chamber_id, ts, temp, hum, door, comp, current=None) -> str:
    base = f"{int(chamber_id)}|{int(round(ts))}|{float(temp):.2f}|{float(hum):.1f}|{int(bool(door))}|{-1 if comp is None else int(bool(comp))}"
    return base if current is None else base + f"|{float(current):.2f}"      # old rows (no current) hash exactly as before


def compute_hash(prev_hash: str, data: str) -> str:
    return hashlib.sha256((prev_hash + data).encode()).hexdigest()


def verify_rows(rows: list[dict]) -> dict:
    """rows: reading dicts ordered by id. Returns {ok, checked, first_bad_id}."""
    prev = None
    for i, r in enumerate(rows):
        data = payload(r["chamber_id"], r["ts"], r["temp"], r["humidity"], r["door"], r["comp"], r.get("current_a"))
        if compute_hash(r["prev_hash"], data) != r["hash"]:
            return {"ok": False, "checked": i, "first_bad_id": r["id"], "reason": "reading data does not match its hash"}
        if prev is not None and r["prev_hash"] != prev:
            return {"ok": False, "checked": i, "first_bad_id": r["id"], "reason": "chain link broken"}
        prev = r["hash"]
    return {"ok": True, "checked": len(rows), "first_bad_id": None, "reason": ""}
