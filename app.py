"""Lead CRM: Flask + SQLite backend serving a dashboard (HTML/CSS/JS)."""
import sqlite3
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request

BASE = Path(__file__).parent
DB = BASE / "crm.db"

STATUSES = ["New", "Contacted", "Qualified", "Proposal", "Won", "Lost"]
SOURCES = ["Website", "Referral", "LinkedIn", "Cold call", "Event", "Email campaign"]
CURRENCY = "$"  # change to your currency symbol
FIELDS = ("name", "company", "email", "phone", "source", "status", "value", "notes")

app = Flask(__name__)


# ---------- database ----------
def init_db():
    fresh = not DB.exists()
    con = sqlite3.connect(DB)
    con.executescript((BASE / "schema.sql").read_text())
    if fresh:  # sample data only on the very first run
        con.executescript((BASE / "seed.sql").read_text())
    con.commit()
    con.close()


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    con = g.pop("db", None)
    if con:
        con.close()


def clean(data, partial=False):
    """Validate and normalise incoming lead data. Only whitelisted fields pass."""
    out = {k: ("" if data[k] is None else str(data[k]).strip()) for k in FIELDS if k in data}
    if "value" in out:
        try:
            out["value"] = max(0.0, float(out["value"] or 0))
        except ValueError:
            raise ValueError("Value must be a number.")
    if "status" in out and out["status"] not in STATUSES:
        raise ValueError("Unknown status.")
    if "source" in out and out["source"] not in SOURCES:
        raise ValueError("Unknown source.")
    if ("name" in out and not out["name"]) or (not partial and not out.get("name")):
        raise ValueError("Name is required.")
    return out


def get_lead(lead_id):
    row = db().execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return dict(row) if row else None


# ---------- pages ----------
@app.get("/")
def index():
    return render_template("index.html", cfg={"statuses": STATUSES, "sources": SOURCES, "currency": CURRENCY})


# ---------- API ----------
@app.get("/api/leads")
def list_leads():
    rows = db().execute("SELECT * FROM leads ORDER BY created_at DESC, id DESC").fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/leads")
def create_lead():
    try:
        data = clean(request.get_json(force=True))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    cols = ", ".join(data)
    marks = ", ".join("?" * len(data))
    cur = db().execute(f"INSERT INTO leads ({cols}) VALUES ({marks})", list(data.values()))
    db().commit()
    return jsonify(get_lead(cur.lastrowid)), 201


@app.put("/api/leads/<int:lead_id>")
def update_lead(lead_id):
    try:
        data = clean(request.get_json(force=True), partial=True)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    if not data:
        return jsonify(error="Nothing to update."), 400
    sets = ", ".join(f"{k} = ?" for k in data)
    cur = db().execute(f"UPDATE leads SET {sets} WHERE id = ?", [*data.values(), lead_id])
    db().commit()
    if cur.rowcount == 0:
        return jsonify(error="Lead not found."), 404
    return jsonify(get_lead(lead_id))


@app.delete("/api/leads/<int:lead_id>")
def delete_lead(lead_id):
    cur = db().execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    db().commit()
    if cur.rowcount == 0:
        return jsonify(error="Lead not found."), 404
    return "", 204


@app.get("/api/stats")
def stats():
    con = db()
    found = {
        r["status"]: {"n": r["n"], "value": r["value"]}
        for r in con.execute("SELECT status, COUNT(*) n, SUM(value) value FROM leads GROUP BY status")
    }
    by_status = {s: found.get(s, {"n": 0, "value": 0}) for s in STATUSES}
    sources = [dict(r) for r in con.execute(
        "SELECT source, COUNT(*) n FROM leads GROUP BY source ORDER BY n DESC")]
    monthly = [dict(r) for r in con.execute(
        "SELECT strftime('%Y-%m', created_at) month, COUNT(*) n FROM leads GROUP BY month ORDER BY month")]

    won, lost = by_status["Won"], by_status["Lost"]
    closed = won["n"] + lost["n"]
    return jsonify(
        total=sum(v["n"] for v in by_status.values()),
        open_value=sum(v["value"] for s, v in by_status.items() if s not in ("Won", "Lost")),
        won_value=won["value"],
        win_rate=round(100 * won["n"] / closed) if closed else 0,
        by_status=by_status,
        sources=sources,
        monthly=monthly,
    )


init_db()

if __name__ == "__main__":
    app.run(debug=True)
