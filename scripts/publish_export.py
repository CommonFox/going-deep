"""Publishes `data/export/` to the private Vercel Blob store the deployed SPA reads from (#130).

Deliberately not part of `src/export/`: that package's own contract (see `build.py`'s docstring)
is that nothing in it touches the network. Publishing is a distinct step for the same reason the
ticket treats it as one — the warehouse rebuild and the export write are local and reproducible
from the raw archive alone; handing the result to Vercel is a separate, network-dependent action
that can fail or be skipped without threatening that guarantee.

Uploads every file under `data/export/` to the same relative path in the Blob store, so the
pathname scheme the SPA already fetches by (`<table>/<league_key>/<season>-<week>.json`,
`manifest.json`, `current_week.json`) carries over unchanged — the deployed `api/data.ts` function
and this script agree on paths without either naming the other's convention. `overwrite=True`
because every file here is a snapshot of "the current export", not an archive — the whole point is
that the SPA always reads the latest build, matching `manifest.json`'s own `built_at` contract.

Requires `BLOB_READ_WRITE_TOKEN` (see `.env.example`) because this runs off Vercel, on whichever
machine just rebuilt the warehouse.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from src import console
from src.export.build import EXPORT_PATH

load_dotenv()

BLOB_READ_WRITE_TOKEN = os.environ.get("BLOB_READ_WRITE_TOKEN")


def publish_export(export_path: Path = EXPORT_PATH) -> int:
    if not BLOB_READ_WRITE_TOKEN:
        raise RuntimeError(
            "BLOB_READ_WRITE_TOKEN must be set to publish the export — see .env.example and the "
            "Vercel Blob store setup in README.md."
        )
    if not export_path.exists():
        raise RuntimeError(f"{export_path} does not exist — run `python -m src.export.build` first.")

    from vercel.blob import put as blob_put

    published = 0
    for file_path in sorted(export_path.rglob("*.json")):
        pathname = file_path.relative_to(export_path).as_posix()
        blob_put(
            pathname,
            file_path.read_bytes(),
            access="private",
            content_type="application/json",
            overwrite=True,
            token=BLOB_READ_WRITE_TOKEN,
        )
        console.archived(Path(pathname))
        published += 1

    console.table("blob_export", published, detail="files published to Vercel Blob")
    return published


if __name__ == "__main__":
    publish_export()
