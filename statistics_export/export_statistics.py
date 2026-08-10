import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import time
import json
import os


DB = "/homeassistant/home-assistant_v2.db"

def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)

log(f"Database path: {DB}")
log(f"Database exists: {os.path.exists(DB)}")
log(f"Database readable: {os.access(DB, os.R_OK)}")
log(f"Database writable: {os.access(DB, os.W_OK)}")

log(f"Root directories: {os.listdir('/')}")
log(f"Config exists: {os.path.exists('/config')}")
log(f"Data exists: {os.path.exists('/data')}")
log(f"Share exists: {os.path.exists('/share')}")

for root, dirs, files in os.walk("/"):
    if "home-assistant_v2.db" in files:
        log(f"FOUND DATABASE: {os.path.join(root, 'home-assistant_v2.db')}")

log("=== MOUNTS ===")

with open("/proc/mounts", "r") as f:
    for line in f:
        log(line.strip())

log("=== DIRECTORIES ===")

for path in ["/config", "/homeassistant", "/share", "/data"]:
    log(
        f"{path}: "
        f"exists={os.path.exists(path)}, "
        f"readable={os.access(path, os.R_OK)}"
    )

def export_missing_days():

    log("Checking for missing exports...")

    exported = 0

    conn = sqlite3.connect(DB)

    row = conn.execute(
        "SELECT MIN(start_ts), MAX(start_ts) FROM statistics_short_term"
    ).fetchone()

    conn.close()

    if row[0] is None:
        log("Database contains no statistics")
        return

    first_day = datetime.fromtimestamp(row[0]).date()
    last_day = min(
        datetime.fromtimestamp(row[1]).date(),
        datetime.now().date() - timedelta(days=1)
    )

    format = options.get("output_format", "parquet")

    day = first_day

    while day <= last_day:

        filename = OUTPUT_DIR / f"statistics_{day}.{format}"

        if filename.exists():
            day += timedelta(days=1)
            continue

        if export_day(day):
            exported += 1

        day += timedelta(days=1)

    log(f"Finished. Exported {exported} missing days.")


# HA Add-on Optionen
with open("/data/options.json") as f:
    options = json.load(f)


EXPORT_HOUR = options.get("export_hour", 1)
EXPORT_MINUTE = options.get("export_minute", 0)
KEEP_DAYS = options.get("keep_days", 28)
OUTPUT_DIR = Path("/share") / options.get("output_dir", "export")


OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


log("Statistics Export started")
log(f"Output directory: {OUTPUT_DIR}")
log(f"Schedule: every day at {EXPORT_HOUR:02d}:{EXPORT_MINUTE:02d}")

def export_day(day):

    log(f"Starting export for {day}")

    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)

    conn = sqlite3.connect(DB)

    sql = """
    SELECT
        st.start_ts,
        sm.statistic_id,
        st.mean,
        st.min,
        st.max,
        st.state,
        st.sum

    FROM statistics_short_term st
    JOIN statistics_meta sm
      ON st.metadata_id = sm.id

    WHERE st.start_ts >= ?
      AND st.start_ts < ?
    """

    df = pd.read_sql_query(
        sql,
        conn,
        params=(
            start.timestamp(),
            end.timestamp()
        )
    )

    conn.close()

    if df.empty:
        log("No data found")
        return False

    format = options.get("output_format", "parquet")

    filename = OUTPUT_DIR / f"statistics_{day}.{format}"

    if format == "parquet":
        df.to_parquet(
            filename,
            compression="zstd"
        )
    elif format == "csv":
        df.to_csv(
            filename,
            index=False
        )
    elif format == "sql":
        with open(filename, "w") as f:
            for _, row in df.iterrows():
                f.write(
                    "INSERT INTO statistics "
                    "(start_ts, statistic_id, mean, min, max, state, sum) "
                    f"VALUES "
                    f"('{row.start_ts}', "
                    f"'{row.statistic_id}', "
                    f"{row.mean}, "
                    f"{row.min}, "
                    f"{row.max}, "
                    f"{row.state}, "
                    f"{row['sum']});\n"
                )
    else:
        raise ValueError(f"Unsupported format: {format}")

    log(f"Successfully exported {len(df)} rows to {filename}")
    return True

export_missing_days()


def cleanup():

    limit = datetime.now() - timedelta(days=KEEP_DAYS)

    for f in OUTPUT_DIR.glob("*.parquet"):

        if datetime.fromtimestamp(f.stat().st_mtime) < limit:
            f.unlink()
            log(f"Deleted old file {f}")


def next_run():

    now = datetime.now()

    target = now.replace(
        hour=EXPORT_HOUR,
        minute=EXPORT_MINUTE,
        second=0,
        microsecond=0
    )

    if target <= now:
        target += timedelta(days=1)

    return target


log(f"Next run scheduled at {next_run()}")


last_run = None

while True:

    now = datetime.now()

    if (
        now.hour == EXPORT_HOUR
        and now.minute == EXPORT_MINUTE
        and last_run != now.date()
    ):

        yesterday = now.date() - timedelta(days=1)

        export_day(yesterday)
        cleanup()

        last_run = now.date()

        log(f"Next run scheduled at {next_run()}")

    time.sleep(30)
