import sqlite3
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import time
import json
import os


DB = "/config/home-assistant_v2.db"


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


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
        return

    filename = OUTPUT_DIR / f"statistics_{day}.parquet"

    df.to_parquet(
        filename,
        compression="zstd"
    )

    log(
        f"Successfully exported {len(df)} rows to {filename}"
    )


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
