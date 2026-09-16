"""THROWAWAY — spike for issue #125. Do not port into src/export/ (#126); it exists to answer a
go/no-go question and to produce one real payload for the vite scaffold in this same directory to
load. Findings live in docs/export-format-spike.md on the feat/export-format-spike branch.

Run from the repo root with .venv activated:

    python3 spikes/125-export-format/measure_and_export.py
"""
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

from src.query import q

HERE = Path(__file__).parent
# Written straight into the scaffold's public dir — gitignored repo-wide like everything else
# under data/, so `npm run dev`/`build` need this run first. One source of truth, no copy step.
DATA_DIR = HERE / "spa" / "public" / "data"


def size_of(df) -> tuple[int, int]:
    raw = df.to_json(orient="records", date_format="iso").encode("utf-8")
    return len(raw), len(gzip.compress(raw, compresslevel=9))


def fmt(n: int) -> str:
    return f"{n/1024:.1f} KB" if n < 1024 * 1024 else f"{n/1024/1024:.2f} MB"


def report(label: str, df) -> None:
    raw, gz = size_of(df)
    print(f"{label:38s} rows={len(df):6d}  raw={fmt(raw):>10s}  gz={fmt(gz):>10s}")


def write_json(df, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(df.to_json(orient="records", date_format="iso"))


print("=== Whole-table exports (current warehouse contents) ===")
whole_tables = [
    "optimal_lineup", "optimal_lineup_bench", "my_roster", "ros_points",
    "waiver_rankings", "weekly_projections",
]
total_raw = total_gz = 0
for t in whole_tables:
    df = q(f"SELECT * FROM {t}")
    raw, gz = size_of(df)
    total_raw += raw
    total_gz += gz
    report(t, df)
print(f"{'TOTAL (all six, whole tables)':38s} raw={fmt(total_raw):>10s}  gz={fmt(total_gz):>10s}")

print()
print("=== Real per-(league, season, week) slices — sleeper, 2026, week 2 ===")
lineup = q("SELECT * FROM optimal_lineup WHERE league_key='sleeper' AND season=2026 AND week=2")
bench = q("SELECT * FROM optimal_lineup_bench WHERE league_key='sleeper' AND season=2026 AND week=2")
waivers_sleeper = q("SELECT * FROM waiver_rankings WHERE league_key='sleeper' AND season=2026 AND week=2")
waivers_espn = q("SELECT * FROM waiver_rankings WHERE league_key='espn' AND season=2026 AND week=2")
report("optimal_lineup (sleeper/2026/wk2)", lineup)
report("optimal_lineup_bench (sleeper/2026/wk2)", bench)
report("waiver_rankings (sleeper/2026/wk2)", waivers_sleeper)
report("waiver_rankings (espn/2026/wk2, smaller pool)", waivers_espn)

lr, lg = size_of(lineup)
br, bg = size_of(bench)
print(f"{'Lineup-optimizer page total':38s} raw={fmt(lr+br):>10s}  gz={fmt(lg+bg):>10s}")

wr, wg = size_of(waivers_sleeper)
print(f"{'Waiver-board page total (worst league)':38s} raw={fmt(wr):>10s}  gz={fmt(wg):>10s}")

print()
print("=== weekly_projections: per-week slice, not currently read by any page ===")
wp = q("SELECT * FROM weekly_projections WHERE season=2026 AND week=2")
report("weekly_projections (2026/wk2, both scoring bases)", wp)

# --- Write the real files this exercise's vite scaffold loads, plus a manifest. ---
DATA_DIR.mkdir(exist_ok=True)
write_json(lineup, DATA_DIR / "optimal_lineup" / "sleeper" / "2026-2.json")
write_json(bench, DATA_DIR / "optimal_lineup_bench" / "sleeper" / "2026-2.json")
write_json(waivers_sleeper, DATA_DIR / "waiver_rankings" / "sleeper" / "2026-2.json")

manifest = {
    "built_at": datetime.now(timezone.utc).isoformat(),
    "schema_version": 1,
    "available": [
        {"table": "optimal_lineup", "league_key": "sleeper", "season": 2026, "week": 2},
        {"table": "optimal_lineup_bench", "league_key": "sleeper", "season": 2026, "week": 2},
        {"table": "waiver_rankings", "league_key": "sleeper", "season": 2026, "week": 2},
    ],
}
(DATA_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
print()
print(f"Wrote real payload + manifest to {DATA_DIR}")
