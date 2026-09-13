"""Entry point for the going-deep web app: `streamlit run src/web/app.py`.

Pure scaffolding — no feature content lives here. The waiver board (#85) and the lineup optimizer
land as files under `pages/`, discovered at runtime, so adding one is a new file rather than a
change to this one. The warehouse-freshness banner below runs before `st.navigation`'s page, which
Streamlit re-executes on every navigation, so it's the one place that check needs to live to show
up on every page.

The freshness check only stats the warehouse file, so it doesn't need a connection at all. Pages
under `pages/` that query actual tables must go through `src.query`'s `q()`/`tables()` instead of
`duckdb.connect()` — same rule notebooks follow, and for the same reason: a connection held open
across a `streamlit run` session takes the file lock DuckDB uses to keep one writer and many readers
from colliding, and `scripts/build_warehouse.sh` would fail with an error that never mentions this
app.
"""

from datetime import datetime
from pathlib import Path

import streamlit as st

from src.web.warehouse_status import staleness_warning, warehouse_built_at

st.set_page_config(page_title="going-deep", layout="wide")

built_at = warehouse_built_at()
st.sidebar.caption(f"Warehouse built {built_at:%Y-%m-%d %H:%M}")
warning = staleness_warning(built_at, datetime.now())
if warning:
    st.sidebar.warning(warning)

pages_dir = Path(__file__).parent / "pages"
pages = [
    st.Page(path, title=path.stem.replace("_", " ").title())
    for path in sorted(pages_dir.glob("*.py"))
]
st.navigation(pages).run()
