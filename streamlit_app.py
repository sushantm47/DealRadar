"""DealRadar Streamlit dashboard.   Run:  streamlit run streamlit_app.py"""
import uuid

import pandas as pd
import streamlit as st

import db_manager
import pipeline
import rag
import scraper

st.set_page_config(page_title="DealRadar", page_icon="📉", layout="wide")
SID = st.session_state.setdefault("sid", str(uuid.uuid4())[:8])


# ---------------------------------------------------------------- login
def login():
    st.title("DealRadar")
    st.caption("Sign in to see your watchlist. Demo account: test / test@123")
    with st.form("login"):
        email = st.text_input("Email or username")
        pw = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in"):
            u = db_manager.run_query("SELECT uid, fname, pswd FROM Users WHERE email=%s", (email,))
            if not u.empty and str(u.iloc[0]["pswd"]) == pw:
                st.session_state.uid, st.session_state.name = int(u.iloc[0]["uid"]), u.iloc[0]["fname"]
                st.rerun()
            st.error("That email and password don't match an account.")


if "uid" not in st.session_state:
    login()
    st.stop()

UID = st.session_state.uid
with st.sidebar:
    st.write(f"Signed in as **{st.session_state.name}**")
    if st.button("Sign out"):
        st.session_state.clear()
        st.rerun()


@st.cache_data(ttl=30)
def watchlist(uid):
    df = db_manager.run_query("""
        SELECT c.cid, p.pid, p.pname, p.p_category AS category, c.cutoff AS target,
               (SELECT price FROM Seller_Prices sp WHERE sp.pid=p.pid ORDER BY price_dt DESC LIMIT 1) AS current,
               (SELECT price FROM Seller_Prices sp WHERE sp.pid=p.pid ORDER BY price_dt ASC  LIMIT 1) AS first,
               (SELECT MIN(price) FROM Seller_Prices sp WHERE sp.pid=p.pid) AS lowest,
               (SELECT COUNT(DISTINCT sid) FROM Seller_Prices sp WHERE sp.pid=p.pid) AS sellers
        FROM Cart c JOIN Product p ON p.pid=c.pid WHERE c.uid=%s ORDER BY p.pname""", (uid,))
    if not df.empty:
        df["change_%"] = ((df["current"] - df["first"]) / df["first"] * 100).round(1)
        df["deal"] = (df["current"] > 0) & (df["target"] > 0) & (df["current"] <= df["target"])
    return df


tab_watch, tab_hist, tab_pipe, tab_ask = st.tabs(["Watchlist", "Price history", "Data pipeline", "Ask DealRadar"])

# ---------------------------------------------------------------- watchlist
with tab_watch:
    with st.form("track", clear_on_submit=True):
        url = st.text_input("Track a product", placeholder="Paste an Amazon, Best Buy, Walmart, Target, Newegg or eBay product URL")
        if st.form_submit_button("Start tracking") and url:
            with st.spinner("Fetching product page..."):
                ok, msg = pipeline.track_product(UID, url, SID)
            (st.success if ok else st.error)(msg)
            watchlist.clear()

    df = watchlist(UID)
    if df.empty:
        st.info("Your watchlist is empty. Paste a product URL above to start tracking it.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Tracked products", len(df))
        c2.metric("At or below target", int(df["deal"].sum()))
        med = df["change_%"].median()
        c3.metric("Median change since tracking", "n/a" if pd.isna(med) else f"{med:+.1f}%")
        st.dataframe(df.drop(columns=["cid", "pid"]), use_container_width=True, hide_index=True,
                     column_config={c: st.column_config.NumberColumn(format="$%.2f") for c in ["target", "current", "first", "lowest"]})

        with st.expander("Set a target price"):
            pick = st.selectbox("Product", df["pname"], key="target_pick")
            row = df[df["pname"] == pick].iloc[0]
            new = st.number_input("Alert me at or below ($)", min_value=0.0, value=0.0 if pd.isna(row["target"]) else float(row["target"]), step=1.0)
            if st.button("Save target"):
                db_manager.update_cart_target(int(row["cid"]), new)
                watchlist.clear()
                st.success("Target saved.")

# ---------------------------------------------------------------- history
with tab_hist:
    df = watchlist(UID)
    if df.empty:
        st.info("Track a product to see its price history.")
    else:
        pick = st.selectbox("Product", df["pname"], key="hist_pick")
        pid = int(df[df["pname"] == pick].iloc[0]["pid"])
        hist = db_manager.run_query("""
            SELECT sp.price_dt, s.sname AS seller, sp.price FROM Seller_Prices sp
            JOIN Sellers s ON s.sid=sp.sid WHERE sp.pid=%s ORDER BY sp.price_dt""", (pid,))
        if hist.empty:
            st.info("No prices recorded for this product yet. Run a refresh on the Data pipeline tab.")
        else:
            wide = hist.pivot_table(index="price_dt", columns="seller", values="price", aggfunc="last")
            st.line_chart(wide)
            st.dataframe(hist.groupby("seller")["price"].agg(["count", "min", "max", "mean", "last"]).round(2),
                         use_container_width=True)

# ---------------------------------------------------------------- pipeline
with tab_pipe:
    left, right = st.columns(2)
    with left:
        st.subheader("Refresh from stores")
        st.caption("Re-scrapes every tracked product across all supported stores (about 2 s per product).")
        if st.button("Refresh prices"):
            with st.spinner("Scraping..."):
                s = scraper.refresh_all(SID)
            st.success(f"Received {s['received']}, stored {s['inserted']}, rejected {s['rejected']}.")
            if s["reasons"]:
                st.json(s["reasons"])
            watchlist.clear()
    with right:
        st.subheader("Import a price feed")
        st.caption("CSV with columns: product_url, seller, price (optional: product_name, currency).")
        up = st.file_uploader("CSV file", type="csv")
        if up and st.button("Import feed"):
            try:
                s = pipeline.ingest_csv(up, source=f"csv:{up.name}")
                st.success(f"Received {s['received']}, stored {s['inserted']}, rejected {s['rejected']}.")
                if s["reasons"]:
                    st.json(s["reasons"])
                watchlist.clear()
            except ValueError as e:
                st.error(str(e))

    st.subheader("Rejected records")
    rej = db_manager.run_query("""SELECT reason, COUNT(*) AS n FROM Rejected_Records GROUP BY reason ORDER BY n DESC""")
    if rej.empty:
        st.caption("Nothing has been rejected yet.")
    else:
        st.bar_chart(rej.set_index("reason"))
        recent = db_manager.run_query("""SELECT rejected_at, source, reason, raw_record FROM Rejected_Records
                                         ORDER BY rejected_at DESC LIMIT 50""")
        st.dataframe(recent, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------- assistant
with tab_ask:
    st.caption("Answers come only from DealRadar's stored prices and news. "
               "Answers that cite data not in the database are rejected before you see them.")
    history = st.session_state.setdefault("chat", [])
    for turn in history:
        with st.chat_message(turn["role"]):
            st.write(turn["content"])
            if turn.get("meta"):
                st.caption(turn["meta"])

    if q := st.chat_input("e.g. Is my Sony headset cheaper at Best Buy or Amazon?"):
        history.append({"role": "user", "content": q})
        with st.chat_message("user"):
            st.write(q)
        with st.chat_message("assistant"):
            with st.spinner("Checking price history..."):
                r = rag.answer_question(q, uid=UID)
            st.write(r["answer"])
            meta = (f"Grounded. Sources: {', '.join(r['citations'])}" if r["grounded"]
                    else f"Grounding check blocked the answer ({r['reason']})")
            st.caption(meta)
            if r["sources"]:
                with st.expander("Retrieved context"):
                    for d in r["sources"]:
                        st.text(d["text"])
        history.append({"role": "assistant", "content": r["answer"], "meta": meta})
