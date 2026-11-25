"""Nutrition: today's meals, intake trend, and the active nutrition program."""

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api_client import api_call, fail, require_user  # noqa: E402

st.set_page_config(page_title="Nutrition", layout="wide")
user_id = require_user()

st.title("Nutrition")

# ---------- today's meals ----------

st.subheader("Today's meals")
meals_response = api_call("GET", f"/users/{user_id}/meals")
meals = meals_response.json() if not meals_response.is_error else []
if meals:
    rows = pd.DataFrame(meals)[["meal", "description", "kcal", "protein_g", "carbs_g", "fat_g", "source"]]
    st.dataframe(rows, hide_index=True, width="stretch")
    st.metric("Total today", f"{sum(m['kcal'] for m in meals)} kcal")
    for meal in meals:
        if st.button("Delete", key=f"del_meal_{meal['id']}"):
            r = api_call("DELETE", f"/users/{user_id}/meals/{meal['id']}")
            if r.is_error:
                fail(r, "Delete")
            st.rerun()
else:
    st.caption("Nothing logged today. Add a meal below, or tell the coach in chat / on Telegram.")

with st.form("add_meal", clear_on_submit=True):
    cols = st.columns([1, 1, 2, 1])
    day = cols[0].date_input("Date", value=date.today(), max_value=date.today())
    meal = cols[1].selectbox("Meal", ["breakfast", "lunch", "dinner", "snack"])
    description = cols[2].text_input("What did you eat?", placeholder="2 eggs + 1 simit")
    kcal = cols[3].number_input("kcal", min_value=0, max_value=20000, step=10)
    macro_cols = st.columns(3)
    protein = macro_cols[0].number_input("Protein (g)", min_value=0.0, max_value=1000.0, value=None)
    carbs = macro_cols[1].number_input("Carbs (g)", min_value=0.0, max_value=1000.0, value=None)
    fat = macro_cols[2].number_input("Fat (g)", min_value=0.0, max_value=1000.0, value=None)
    if st.form_submit_button("Log meal"):
        if not description.strip() or not kcal:
            st.error("Describe the meal and enter its calories.")
        else:
            payload = {
                "date": day.isoformat(), "meal": meal, "description": description.strip(), "kcal": kcal,
                "protein_g": protein, "carbs_g": carbs, "fat_g": fat,
            }
            r = api_call("POST", f"/users/{user_id}/meals", json=payload)
            if r.is_error:
                fail(r, "Log meal")
            else:
                st.rerun()

st.divider()

# ---------- intake trend ----------

st.subheader("Intake (last 14 days)")
intake_response = api_call("GET", f"/users/{user_id}/intake", params={"days": 14})
intake = intake_response.json() if not intake_response.is_error else []
programs = api_call("GET", f"/users/{user_id}/programs", params={"status": "active"}).json() or []
nutrition = next((p for p in programs if p["kind"] == "nutrition"), None)
target = nutrition["payload"].get("target_kcal") if nutrition else None

if intake:
    frame = pd.DataFrame(intake).set_index("date")
    st.bar_chart(frame[["kcal"]], height=220)
    if target:
        avg = frame["kcal"].mean()
        st.metric("Average vs estimated maintenance", f"{avg:.0f} kcal", delta=f"{avg - target:+.0f} vs {target} kcal", delta_color="off")
    protein_days = frame["protein_g"].dropna()
    if not protein_days.empty:
        st.caption(f"Average protein: {protein_days.mean():.0f} g/day")
else:
    st.caption("No intake logged in the last 14 days.")

st.divider()

# ---------- nutrition program ----------

if nutrition:
    st.subheader(f"Active plan: {nutrition['name']} (v{nutrition['version']})")
    payload = nutrition["payload"]
    cols = st.columns(4)
    cols[0].metric("Estimated maintenance", f"{payload.get('target_kcal') or '—'} kcal")
    cols[1].metric("Protein", f"{payload.get('protein_g')} g")
    cols[2].metric("Carbs", f"{payload.get('carbs_g')} g")
    cols[3].metric("Fat", f"{payload.get('fat_g')} g")
    meal_rows = pd.DataFrame(payload.get("meals", []))
    if not meal_rows.empty:
        st.dataframe(meal_rows, hide_index=True, width="stretch")
    for guideline in payload.get("guidelines", []):
        st.markdown(f"- {guideline}")
else:
    st.subheader("Nutrition plan")
    st.info("No active nutrition program.")

st.info("Use the React Plan page to review an energy estimate and generate a nutrition plan.")
