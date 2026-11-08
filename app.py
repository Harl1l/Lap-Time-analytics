from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("LAP_DB_PATH", BASE_DIR / "laps.db"))

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-development-secret")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8 MB image upload limit


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            track TEXT NOT NULL DEFAULT '',
            discipline TEXT NOT NULL DEFAULT 'Other',
            session_date TEXT NOT NULL,
            conditions TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS laps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            driver TEXT NOT NULL,
            car TEXT NOT NULL DEFAULT '',
            lap_number INTEGER NOT NULL,
            lap_ms INTEGER NOT NULL,
            s1_ms INTEGER,
            s2_ms INTEGER,
            s3_ms INTEGER,
            fuel REAL,
            speed REAL,
            valid INTEGER NOT NULL DEFAULT 1,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_laps_session ON laps(session_id);
        CREATE INDEX IF NOT EXISTS idx_laps_driver ON laps(driver);
        """)


def parse_time_ms(value: Any) -> int | None:
    """Accept seconds (e.g. 91.234), M:SS.sss, or H:MM:SS.sss."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        if ":" not in s:
            seconds = float(s)
            return round(seconds * 1000) if seconds > 0 else None
        parts = s.split(":")
        if len(parts) == 2:
            minutes = int(parts[0])
            seconds = float(parts[1])
            total = minutes * 60 + seconds
        elif len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            total = hours * 3600 + minutes * 60 + seconds
        else:
            return None
        return round(total * 1000) if total > 0 else None
    except (TypeError, ValueError):
        return None


def fmt_ms(value: int | float | None) -> str:
    if value is None:
        return "—"
    ms = int(round(float(value)))
    minutes, rem = divmod(ms, 60000)
    seconds, milli = divmod(rem, 1000)
    return f"{minutes}:{seconds:02d}.{milli:03d}"


def row_to_lap(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["lap_time"] = fmt_ms(item.get("lap_ms"))
    for key in ("s1_ms", "s2_ms", "s3_ms"):
        item[key.replace("_ms", "") + "_time"] = fmt_ms(item.get(key))
    return item


def all_laps_query(where: str = "", params: tuple = ()) -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            """SELECT l.*, s.name AS session_name, s.track, s.discipline, s.session_date
               FROM laps l JOIN sessions s ON s.id=l.session_id """ + where +
            " ORDER BY s.session_date DESC, l.session_id DESC, l.lap_number ASC",
            params,
        ).fetchall()
    return [row_to_lap(r) for r in rows]


@app.template_filter("lap_time")
def lap_time_filter(value):
    return fmt_ms(value)


def insert_lap(db, session_id, driver, car, lap_number, lap_ms, s1_ms=None, s2_ms=None,
               s3_ms=None, fuel=None, speed=None, valid=True, notes=""):
    db.execute(
        """INSERT INTO laps
           (session_id, driver, car, lap_number, lap_ms, s1_ms, s2_ms, s3_ms,
            fuel, speed, valid, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, driver.strip(), car.strip(), lap_number, lap_ms, s1_ms, s2_ms,
         s3_ms, fuel, speed, 1 if valid else 0, notes.strip(),
         datetime.now().isoformat(timespec="seconds")),
    )


@app.route("/")
def dashboard():
    with get_db() as db:
        session_count = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        lap_count = db.execute("SELECT COUNT(*) FROM laps").fetchone()[0]
        driver_count = db.execute("SELECT COUNT(DISTINCT driver) FROM laps").fetchone()[0]
        best = db.execute("SELECT MIN(lap_ms) FROM laps WHERE valid=1").fetchone()[0]
        recent_sessions = db.execute(
            """SELECT s.*, COUNT(l.id) AS lap_count, MIN(CASE WHEN l.valid=1 THEN l.lap_ms END) AS best_ms
               FROM sessions s LEFT JOIN laps l ON l.session_id=s.id
               GROUP BY s.id ORDER BY s.session_date DESC, s.id DESC LIMIT 6"""
        ).fetchall()
        recent_laps = db.execute(
            """SELECT l.*, s.name AS session_name, s.track, s.session_date
               FROM laps l JOIN sessions s ON s.id=l.session_id
               ORDER BY l.id DESC LIMIT 8"""
        ).fetchall()
        trend = db.execute(
            """SELECT s.session_date, s.name, MIN(l.lap_ms) AS best_ms
               FROM sessions s JOIN laps l ON l.session_id=s.id
               WHERE l.valid=1 GROUP BY s.id ORDER BY s.session_date, s.id"""
        ).fetchall()
    return render_template(
        "dashboard.html",
        session_count=session_count,
        lap_count=lap_count,
        driver_count=driver_count,
        best=fmt_ms(best),
        recent_sessions=[dict(r, best_time=fmt_ms(r["best_ms"])) for r in recent_sessions],
        recent_laps=[row_to_lap(r) for r in recent_laps],
        trend=[{"label": f'{r["session_date"]} · {r["name"]}', "ms": r["best_ms"]} for r in trend],
    )


@app.route("/sessions")
def sessions():
    with get_db() as db:
        rows = db.execute(
            """SELECT s.*, COUNT(l.id) AS lap_count, COUNT(DISTINCT l.driver) AS drivers,
                      MIN(CASE WHEN l.valid=1 THEN l.lap_ms END) AS best_ms
               FROM sessions s LEFT JOIN laps l ON l.session_id=s.id
               GROUP BY s.id ORDER BY s.session_date DESC, s.id DESC"""
        ).fetchall()
    data = [dict(r, best_time=fmt_ms(r["best_ms"])) for r in rows]
    return render_template("sessions.html", sessions=data)


@app.route("/sessions/new", methods=["GET", "POST"])
def new_session():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        track = request.form.get("track", "").strip()
        discipline = request.form.get("discipline", "Other").strip()
        session_date = request.form.get("session_date", "").strip() or datetime.now().date().isoformat()
        conditions = request.form.get("conditions", "").strip()
        notes = request.form.get("notes", "").strip()
        if not name:
            flash("Please give the session a name.", "error")
            return render_template("new_session.html")
        with get_db() as db:
            cur = db.execute(
                """INSERT INTO sessions (name, track, discipline, session_date, conditions, notes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, track, discipline, session_date, conditions, notes,
                 datetime.now().isoformat(timespec="seconds")),
            )
            session_id = cur.lastrowid
        flash("Session created. Add laps whenever you're ready.", "success")
        return redirect(url_for("session_detail", session_id=session_id))
    return render_template("new_session.html", today=datetime.now().date().isoformat())


@app.route("/sessions/<int:session_id>")
def session_detail(session_id):
    with get_db() as db:
        session = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not session:
            flash("Session not found.", "error")
            return redirect(url_for("sessions"))
        rows = db.execute(
            "SELECT * FROM laps WHERE session_id=? ORDER BY driver, lap_number, id",
            (session_id,),
        ).fetchall()
        drivers = db.execute(
            "SELECT DISTINCT driver FROM laps WHERE session_id=? ORDER BY driver", (session_id,)
        ).fetchall()
        best_by_driver = db.execute(
            """SELECT driver, MIN(lap_ms) AS best_ms, AVG(lap_ms) AS avg_ms, COUNT(*) AS laps
               FROM laps WHERE session_id=? AND valid=1 GROUP BY driver ORDER BY best_ms""",
            (session_id,),
        ).fetchall()
    laps = [row_to_lap(r) for r in rows]
    return render_template(
        "session_detail.html",
        session=dict(session),
        laps=laps,
        drivers=[r["driver"] for r in drivers],
        best_by_driver=[dict(r, best_time=fmt_ms(r["best_ms"]), avg_time=fmt_ms(r["avg_ms"])) for r in best_by_driver],
    )


@app.route("/sessions/<int:session_id>/laps", methods=["POST"])
def add_lap(session_id):
    driver = request.form.get("driver", "").strip()
    car = request.form.get("car", "").strip()
    lap_ms = parse_time_ms(request.form.get("lap_time"))
    if not driver or not lap_ms:
        flash("Driver and a valid lap time are required. Use 91.234 or 1:31.234.", "error")
        return redirect(url_for("session_detail", session_id=session_id))
    try:
        lap_number = max(1, int(request.form.get("lap_number") or 1))
        s1_ms = parse_time_ms(request.form.get("s1"))
        s2_ms = parse_time_ms(request.form.get("s2"))
        s3_ms = parse_time_ms(request.form.get("s3"))
        fuel = float(request.form["fuel"]) if request.form.get("fuel", "").strip() else None
        speed = float(request.form["speed"]) if request.form.get("speed", "").strip() else None
    except ValueError:
        flash("Check lap number, fuel and speed values.", "error")
        return redirect(url_for("session_detail", session_id=session_id))
    valid = request.form.get("valid") == "on"
    notes = request.form.get("notes", "")
    with get_db() as db:
        exists = db.execute("SELECT id FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not exists:
            flash("Session not found.", "error")
            return redirect(url_for("sessions"))
        insert_lap(db, session_id, driver, car, lap_number, lap_ms, s1_ms, s2_ms, s3_ms,
                   fuel, speed, valid, notes)
    flash("Lap saved.", "success")
    return redirect(url_for("session_detail", session_id=session_id))


@app.route("/laps/<int:lap_id>/delete", methods=["POST"])
def delete_lap(lap_id):
    with get_db() as db:
        row = db.execute("SELECT session_id FROM laps WHERE id=?", (lap_id,)).fetchone()
        if row:
            session_id = row["session_id"]
            db.execute("DELETE FROM laps WHERE id=?", (lap_id,))
            flash("Lap deleted.", "success")
            return redirect(url_for("session_detail", session_id=session_id))
    flash("Lap not found.", "error")
    return redirect(url_for("sessions"))


@app.route("/sessions/<int:session_id>/delete", methods=["POST"])
def delete_session(session_id):
    with get_db() as db:
        db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    flash("Session and its laps deleted.", "success")
    return redirect(url_for("sessions"))


@app.route("/import", methods=["GET", "POST"])
def import_csv():
    if request.method == "POST":
        uploaded = request.files.get("file")
        session_id = request.form.get("session_id", "").strip()
        create_session = request.form.get("create_session") == "on"
        if not uploaded or not uploaded.filename:
            flash("Choose a CSV file to import.", "error")
            return redirect(url_for("import_csv"))
        try:
            content = uploaded.stream.read().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(content))
            if not reader.fieldnames:
                raise ValueError("The CSV needs a header row.")
            headers = {h.strip().lower().replace(" ", "_"): h for h in reader.fieldnames if h}
            def val(row, *names):
                for name in names:
                    key = headers.get(name)
                    if key and row.get(key) is not None:
                        return row[key].strip()
                return ""
            rows = list(reader)
            if not rows:
                raise ValueError("No data rows were found.")
            with get_db() as db:
                if create_session:
                    name = request.form.get("new_session_name", "").strip() or Path(uploaded.filename).stem
                    cur = db.execute(
                        """INSERT INTO sessions (name, track, discipline, session_date, conditions, notes, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (name, request.form.get("track", "").strip(),
                         request.form.get("discipline", "Other"),
                         request.form.get("session_date", "").strip() or datetime.now().date().isoformat(),
                         request.form.get("conditions", "").strip(), "Imported from CSV",
                         datetime.now().isoformat(timespec="seconds")),
                    )
                    target_id = cur.lastrowid
                else:
                    target_id = int(session_id)
                    if not db.execute("SELECT id FROM sessions WHERE id=?", (target_id,)).fetchone():
                        raise ValueError("Select an existing session or create a new one.")
                added = 0
                next_lap_by_driver = {}
                for row in rows:
                    driver = val(row, "driver", "driver_name", "racer") or "Imported Driver"
                    lap_ms = parse_time_ms(val(row, "lap_time", "laptime", "lap", "time"))
                    if not lap_ms:
                        continue
                    lap_num_raw = val(row, "lap_number", "lap_no", "lap")
                    if lap_num_raw and not parse_time_ms(lap_num_raw):
                        try:
                            lap_num = int(lap_num_raw)
                        except ValueError:
                            lap_num = next_lap_by_driver.get(driver, 0) + 1
                    else:
                        lap_num = next_lap_by_driver.get(driver, 0) + 1
                    next_lap_by_driver[driver] = max(lap_num, next_lap_by_driver.get(driver, 0))
                    insert_lap(
                        db, target_id, driver, val(row, "car", "kart", "vehicle"),
                        max(1, lap_num), lap_ms,
                        parse_time_ms(val(row, "s1", "sector_1", "sector1", "sector_1_time")),
                        parse_time_ms(val(row, "s2", "sector_2", "sector2", "sector_2_time")),
                        parse_time_ms(val(row, "s3", "sector_3", "sector3", "sector_3_time")),
                        float(val(row, "fuel")) if val(row, "fuel") else None,
                        float(val(row, "speed", "max_speed")) if val(row, "speed", "max_speed") else None,
                        val(row, "valid", "is_valid").lower() not in ("false", "0", "no", "invalid"),
                        val(row, "notes", "comment"),
                    )
                    added += 1
                if added == 0:
                    raise ValueError("No valid lap times were found. Check the CSV columns and time format.")
            flash(f"Imported {added} lap(s) successfully.", "success")
            return redirect(url_for("session_detail", session_id=target_id))
        except (UnicodeDecodeError, csv.Error, ValueError, sqlite3.Error) as exc:
            flash(f"Import failed: {exc}", "error")
            return redirect(url_for("import_csv"))
    with get_db() as db:
        sessions_list = db.execute("SELECT id, name, track, session_date FROM sessions ORDER BY session_date DESC, id DESC").fetchall()
    return render_template("import.html", sessions=[dict(s) for s in sessions_list], today=datetime.now().date().isoformat())



@app.errorhandler(413)
def upload_too_large(_error):
    flash("That upload is too large. Please use an image smaller than 8 MB.", "error")
    return redirect(url_for("import_image"))


# ---------------------- Timing-sheet image OCR ----------------------
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
TIMING_RE = re.compile(r"\b(?:\d{1,2}:)?\d{1,2}\.\d{2,3}\b")


def extract_timing_rows(image_bytes: bytes) -> tuple[str, list[dict[str, str]]]:
    """OCR a timing sheet and create editable candidate rows. OCR is never auto-saved."""
    try:
        from PIL import Image, ImageEnhance, ImageOps
        import pytesseract
    except ImportError as exc:
        raise RuntimeError(
            "Image OCR packages are missing. Run: pip install Pillow pytesseract"
        ) from exc

    try:
        image = Image.open(io.BytesIO(image_bytes))
        image = ImageOps.exif_transpose(image).convert("RGB")
        # Upscale modestly for small timing-sheet text, then improve contrast.
        if image.width < 1800:
            scale = min(2.0, 1800 / max(image.width, 1))
            image = image.resize((int(image.width * scale), int(image.height * scale)))
        image = ImageOps.grayscale(image)
        image = ImageEnhance.Contrast(image).enhance(1.8)
        text = pytesseract.image_to_string(image, config="--psm 6")
    except Exception as exc:
        raise RuntimeError(
            "Could not read this image. Try a clearer, straight-on JPG or PNG photo."
        ) from exc

    candidates: list[dict[str, str]] = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if not line:
            continue
        times = TIMING_RE.findall(line)
        if not times:
            continue

        # The first detected time is treated as the lap/result time. The rest are
        # deliberately not guessed as sectors; the review screen lets the user edit.
        first_time = times[0]
        prefix = line[:line.find(first_time)].strip(" |;:-")
        # Remove common position / row-number prefixes such as "1 Alex".
        prefix = re.sub(r"^\s*#?\d{1,3}\s+", "", prefix)
        # Avoid treating an empty or purely numeric cell as a driver name.
        driver = prefix.strip(" |;:-") or "Check driver name"
        candidates.append({
            "driver": driver[:80],
            "car": "",
            "lap_number": "1",
            "lap_time": first_time,
            "s1": "",
            "s2": "",
            "s3": "",
            "valid": "on",
        })

    # Deduplicate identical OCR rows while preserving order.
    unique = []
    seen = set()
    for row in candidates:
        key = (row["driver"].lower(), row["lap_time"])
        if key not in seen:
            unique.append(row)
            seen.add(key)
    return text, unique


@app.route("/import/image", methods=["GET", "POST"])
def import_image():
    if request.method == "POST":
        uploaded = request.files.get("image")
        if not uploaded or not uploaded.filename:
            flash("Choose a timing-sheet image first.", "error")
            return redirect(url_for("import_image"))
        if Path(uploaded.filename).suffix.lower() not in ALLOWED_IMAGE_EXTENSIONS:
            flash("Use a PNG, JPG, JPEG, WEBP or BMP image.", "error")
            return redirect(url_for("import_image"))
        try:
            image_bytes = uploaded.stream.read()
            if not image_bytes:
                raise ValueError("The uploaded image is empty.")
            if len(image_bytes) > 8 * 1024 * 1024:
                raise ValueError("Image is larger than the 8 MB upload limit.")
            raw_text, rows = extract_timing_rows(image_bytes)
            if not rows:
                raise ValueError(
                    "No lap times were detected. Try a sharper image with the timing columns clearly visible."
                )
            with get_db() as db:
                existing_sessions = db.execute(
                    "SELECT id, name, track, session_date FROM sessions ORDER BY session_date DESC, id DESC"
                ).fetchall()
            return render_template(
                "review_image_results.html",
                rows=rows,
                raw_text=raw_text,
                sessions=[dict(s) for s in existing_sessions],
                today=datetime.now().date().isoformat(),
                source_name=Path(uploaded.filename).name,
            )
        except (ValueError, RuntimeError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("import_image"))
    with get_db() as db:
        sessions_list = db.execute(
            "SELECT id, name, track, session_date FROM sessions ORDER BY session_date DESC, id DESC"
        ).fetchall()
    return render_template(
        "import_image.html",
        sessions=[dict(s) for s in sessions_list],
        today=datetime.now().date().isoformat(),
    )


@app.route("/import/image/save", methods=["POST"])
def save_image_results():
    create_session = request.form.get("create_session") == "on"
    try:
        drivers = request.form.getlist("driver[]")
        lap_times = request.form.getlist("lap_time[]")
        lap_numbers = request.form.getlist("lap_number[]")
        cars = request.form.getlist("car[]")
        s1_values = request.form.getlist("s1[]")
        s2_values = request.form.getlist("s2[]")
        s3_values = request.form.getlist("s3[]")
        valid_values = request.form.getlist("valid[]")

        if not drivers or not (len(drivers) == len(lap_times) == len(lap_numbers)):
            raise ValueError("The review form was incomplete. Please upload the image again.")
        prepared = []
        for index, (driver, lap_time) in enumerate(zip(drivers, lap_times)):
            driver = driver.strip()
            lap_ms = parse_time_ms(lap_time)
            if not driver or not lap_ms:
                continue
            try:
                lap_number = max(1, int(lap_numbers[index] or 1))
                s1_ms = parse_time_ms(s1_values[index] if index < len(s1_values) else "")
                s2_ms = parse_time_ms(s2_values[index] if index < len(s2_values) else "")
                s3_ms = parse_time_ms(s3_values[index] if index < len(s3_values) else "")
            except ValueError:
                continue
            prepared.append((
                driver, cars[index].strip() if index < len(cars) else "",
                lap_number, lap_ms, s1_ms, s2_ms, s3_ms,
                (valid_values[index] == "on") if index < len(valid_values) else True
            ))
        if not prepared:
            raise ValueError("No valid lap rows remain. Check driver names and lap-time formats.")

        with get_db() as db:
            if create_session:
                name = request.form.get("new_session_name", "").strip() or "Imported timing sheet"
                cur = db.execute(
                    """INSERT INTO sessions (name, track, discipline, session_date, conditions, notes, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (name, request.form.get("track", "").strip(),
                     request.form.get("discipline", "Karting").strip(),
                     request.form.get("session_date", "").strip() or datetime.now().date().isoformat(),
                     request.form.get("conditions", "").strip(),
                     "Imported from timing-sheet image (OCR reviewed by user)",
                     datetime.now().isoformat(timespec="seconds")),
                )
                target_id = cur.lastrowid
            else:
                raw_id = request.form.get("session_id", "").strip()
                if not raw_id:
                    raise ValueError("Choose an existing session or create a new session.")
                target_id = int(raw_id)
                if not db.execute("SELECT id FROM sessions WHERE id=?", (target_id,)).fetchone():
                    raise ValueError("The selected session no longer exists.")
            for row in prepared:
                insert_lap(db, target_id, *row[:7], valid=row[7],
                           notes="Imported from timing-sheet image; OCR reviewed")
        flash(f"Saved {len(prepared)} reviewed result row(s). Please check the session log.", "success")
        return redirect(url_for("session_detail", session_id=target_id))
    except (ValueError, sqlite3.Error) as exc:
        flash(f"Could not save image results: {exc}", "error")
        return redirect(url_for("import_image"))


@app.route("/analytics")
def analytics():
    with get_db() as db:
        drivers = [r[0] for r in db.execute("SELECT DISTINCT driver FROM laps ORDER BY driver").fetchall()]
        tracks = [r[0] for r in db.execute("SELECT DISTINCT track FROM sessions WHERE track<>'' ORDER BY track").fetchall()]
    return render_template("analytics.html", drivers=drivers, tracks=tracks)


@app.route("/api/analytics")
def analytics_api():
    driver_filter = request.args.get("driver", "").strip()
    track_filter = request.args.get("track", "").strip()
    query = """SELECT l.*, s.name AS session_name, s.track, s.discipline, s.session_date
               FROM laps l JOIN sessions s ON s.id=l.session_id WHERE 1=1"""
    params: list[Any] = []
    if driver_filter:
        query += " AND l.driver=?"
        params.append(driver_filter)
    if track_filter:
        query += " AND s.track=?"
        params.append(track_filter)
    query += " ORDER BY s.session_date, s.id, l.lap_number"
    with get_db() as db:
        rows = db.execute(query, params).fetchall()
        personal_bests = db.execute(
            """SELECT driver, MIN(lap_ms) AS best_ms, COUNT(*) AS laps
               FROM laps WHERE valid=1 GROUP BY driver ORDER BY best_ms"""
        ).fetchall()
        sector_bests = db.execute(
            """SELECT MIN(s1_ms), MIN(s2_ms), MIN(s3_ms) FROM laps
               WHERE valid=1 AND s1_ms IS NOT NULL AND s2_ms IS NOT NULL AND s3_ms IS NOT NULL"""
        ).fetchone()
    laps = [row_to_lap(r) for r in rows]
    valid = [l for l in laps if l["valid"]]
    best_ms = min((l["lap_ms"] for l in valid), default=None)
    avg_ms = round(sum(l["lap_ms"] for l in valid) / len(valid)) if valid else None
    theoretical = sum(v for v in sector_bests if v is not None) if all(sector_bests) else None
    by_driver = {}
    for lap in valid:
        item = by_driver.setdefault(lap["driver"], {"driver": lap["driver"], "laps": 0, "best_ms": None, "sum_ms": 0, "times": []})
        item["laps"] += 1
        item["sum_ms"] += lap["lap_ms"]
        item["times"].append(lap["lap_ms"])
        item["best_ms"] = lap["lap_ms"] if item["best_ms"] is None else min(item["best_ms"], lap["lap_ms"])
    comparison = []
    for item in by_driver.values():
        item["avg_ms"] = round(item["sum_ms"] / item["laps"])
        item["consistency_ms"] = round((sum((x-item["avg_ms"])**2 for x in item["times"]) / len(item["times"])) ** 0.5) if item["times"] else None
        item["best_time"] = fmt_ms(item["best_ms"])
        item["avg_time"] = fmt_ms(item["avg_ms"])
        item["consistency_time"] = fmt_ms(item["consistency_ms"])
        comparison.append(item)
    comparison.sort(key=lambda x: x["best_ms"])
    return jsonify({
        "laps": laps,
        "valid_laps": len(valid),
        "best_ms": best_ms,
        "best_time": fmt_ms(best_ms),
        "avg_ms": avg_ms,
        "avg_time": fmt_ms(avg_ms),
        "theoretical_ms": theoretical,
        "theoretical_time": fmt_ms(theoretical),
        "personal_bests": [{"driver": r["driver"], "best_ms": r["best_ms"], "best_time": fmt_ms(r["best_ms"]), "laps": r["laps"]} for r in personal_bests],
        "comparison": comparison,
    })


@app.route("/export.csv")
def export_csv():
    rows = all_laps_query()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["session_date", "session_name", "track", "discipline", "driver", "car", "lap_number",
                     "lap_time", "sector_1", "sector_2", "sector_3", "fuel", "speed", "valid", "notes"])
    for lap in rows:
        writer.writerow([lap["session_date"], lap["session_name"], lap["track"], lap["discipline"],
                         lap["driver"], lap["car"], lap["lap_number"], lap["lap_time"],
                         lap["s1_time"], lap["s2_time"], lap["s3_time"], lap["fuel"], lap["speed"],
                         bool(lap["valid"]), lap["notes"]])
    from flask import Response
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=apex-lap-data.csv"})


@app.route("/health")
def health():
    return jsonify({"status": "ok", "database": str(DB_PATH)})


init_db()

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
