"""Cost: LLM spend and prompt-cache performance, from the llm_calls table."""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api_client import api_call, fail, require_user  # noqa: E402

st.set_page_config(page_title="Cost", layout="wide")
user_id = require_user()

# USD per 1M tokens: (input, cached input, output). Cache-write tokens are not tracked
# separately, so the real cost can be slightly higher on days the prefix is rewritten.
PRICES = {
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-6.1-terra": (1.00, 0.05, 5.00),
}

st.title("LLM cost & caching")

response = api_call("GET", f"/users/{user_id}/usage", params={"days": 30})
if response.is_error:
    fail(response, "Load usage")
    st.stop()
usage = response.json()
rows = usage["by_model_purpose"]
daily = usage["daily"]

if not rows:
    st.info("No LLM calls recorded yet for this profile.")
    st.stop()


def cost_usd(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    price = PRICES.get(model)
    if price is None:
        return 0.0
    fresh = max(input_tokens - cached_tokens, 0)
    return (fresh * price[0] + cached_tokens * price[1] + output_tokens * price[2]) / 1_000_000


total_in = sum(r["input_tokens"] for r in rows)
total_cached = sum(r["cached_tokens"] for r in rows)
total_out = sum(r["output_tokens"] for r in rows)
total_calls = sum(r["calls"] for r in rows)
total_cost = sum(cost_usd(r["model"], r["input_tokens"], r["cached_tokens"], r["output_tokens"]) for r in rows)

cols = st.columns(4)
cols[0].metric("Calls (30d)", total_calls)
cols[1].metric("Tokens in / out", f"{total_in:,} / {total_out:,}")
cols[2].metric("Cache hit rate", f"{(total_cached / total_in * 100):.0f}%" if total_in else "n/a")
cols[3].metric("Estimated cost", f"${total_cost:.2f}")

st.subheader("By model and purpose")
frame = pd.DataFrame(rows)
frame["est_usd"] = [
    round(cost_usd(r["model"], r["input_tokens"], r["cached_tokens"], r["output_tokens"]), 4) for r in rows
]
st.dataframe(frame, hide_index=True, width="stretch")

if daily:
    st.subheader("Per day")
    daily_frame = pd.DataFrame(daily).set_index("day")
    st.bar_chart(daily_frame[["input_tokens", "cached_tokens"]], height=220)

st.caption(
    "Estimates use list prices per 1M tokens: gpt-6-luna $0.10 in / $0.01 cached / $0.50 out; "
    "gpt-6.1-terra $1.00 / $0.05 / $5.00. The stable prefix (rules + profile + memories + program) "
    "is cached for 12h; sessions for 30m. A healthy hit rate after the first question of a day is 50%+."
)
