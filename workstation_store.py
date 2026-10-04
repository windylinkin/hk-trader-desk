import json
import sqlite3
import threading
from pathlib import Path
from contextlib import contextmanager
from runtime_config import ROOT, DATA_DIR, prepare_data_dir


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path else prepare_data_dir() / "workstation.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS alerts(id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, code TEXT NOT NULL, pattern TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS alerts_time ON alerts(timestamp);
            CREATE INDEX IF NOT EXISTS alerts_code ON alerts(code);
            CREATE TABLE IF NOT EXISTS notes(alert_id TEXT PRIMARY KEY, tag TEXT NOT NULL, note TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS outcomes(alert_id TEXT NOT NULL, horizon INTEGER NOT NULL, price REAL, actual_seconds REAL, return_pct REAL, PRIMARY KEY(alert_id,horizon));
            CREATE TABLE IF NOT EXISTS deliveries(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, channel TEXT, success INTEGER, detail TEXT);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES (?,?)",
                (key, json.dumps(value, ensure_ascii=False, allow_nan=False)),
            )

    def add_alert(self, alert):
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO alerts VALUES (?,?,?,?,?)",
                (
                    alert["id"],
                    alert["timestamp"],
                    alert["code"],
                    alert.get("pattern_id", "legacy"),
                    json.dumps(alert, ensure_ascii=False, allow_nan=False),
                ),
            )

    def alerts(self, limit=200, code="", date="", pattern=""):
        clauses, args = [], []
        for clause, value in [("code=?", code), ("substr(timestamp,1,10)=?", date), ("pattern=?", pattern)]:
            if value:
                clauses.append(clause)
                args.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM alerts" + where + " ORDER BY timestamp DESC,id DESC LIMIT ?", args + [limit]
            ).fetchall()
            result = [json.loads(r[0]) for r in rows]
            for a in result:
                note = db.execute("SELECT tag,note,updated FROM notes WHERE alert_id=?", (a["id"],)).fetchone()
                a["review"] = dict(note) if note else {"tag": "待复盘", "note": ""}
                a["outcomes"] = {
                    str(r["horizon"]): dict(r)
                    for r in db.execute("SELECT * FROM outcomes WHERE alert_id=?", (a["id"],))
                }
        return result

    def count(self):
        with self.connect() as db:
            return db.execute("SELECT count(*) FROM alerts").fetchone()[0]

    def note(self, alert_id, tag, note, now):
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM alerts WHERE id=?", (alert_id,)).fetchone():
                raise ValueError("告警不存在")
            db.execute("INSERT OR REPLACE INTO notes VALUES (?,?,?,?)", (alert_id, tag, note, now))

    def outcome(self, alert_id, horizon, price, elapsed, change):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO outcomes VALUES (?,?,?,?,?)", (alert_id, horizon, price, elapsed, change))

    def delivery(self, timestamp, channel, success, detail):
        with self.connect() as db:
            db.execute(
                "INSERT INTO deliveries(timestamp,channel,success,detail) VALUES (?,?,?,?)",
                (timestamp, channel, int(success), detail),
            )
            db.execute("DELETE FROM deliveries WHERE id NOT IN (SELECT id FROM deliveries ORDER BY id DESC LIMIT 500)")

    def deliveries(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM deliveries ORDER BY id DESC LIMIT 30")]


store = Store()
