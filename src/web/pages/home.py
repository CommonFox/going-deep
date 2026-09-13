"""Landing page. Nothing but a pointer to what's coming — the shell is the point of this ticket.

Later pages (waiver board, lineup optimizer) drop their own file into `pages/` and app.py picks
them up without modification.
"""

import streamlit as st

st.title("going-deep")
st.write(
    "The waiver board and lineup optimizer will land here as separate pages. This is the shell "
    "they'll be added to."
)
