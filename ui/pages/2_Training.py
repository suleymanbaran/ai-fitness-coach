"""Training: today's session, the active program, generation/review, and workout logging."""

import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api_client import api_call, fail, require_user  # noqa: E402

st.set_page_config(page_title="Training", layout="wide")
user_id = require_user()


def parse_exercise_line(line: str) -> dict:
    """'Bench press: 3x8 @80 rpe8' -> {name, sets:[{reps, weight_kg, rpe} x3]}.
    Weight/RPE apply to every set on the line; unparsable lines log one empty set."""
    name, _, sets_part = line.partition(":")
    if not sets_part:
        name, sets_part = line, ""
    weight = re.search(r"@\s*(\d+(?:\.\d+)?)", sets_part)
    rpe = re.search(r"rpe\s*(\d+(?:\.\d+)?)", sets_part, re.IGNORECASE)
    sets = []
    for match in re.finditer(r"(\d+)\s*[x×]\s*(\d+)", sets_part):
        one = {"reps": int(match.group(2))}
        if weight:
            one["weight_kg"] = float(weight.group(1))
        if rpe:
            one["rpe"] = float(rpe.group(1))
        sets += [dict(one) for _ in range(int(match.group(1)))]
    return {"name": name.strip()[:120], "sets": sets or [{}]}


# ---------- today ----------

st.title("Training")
session = api_call("GET", f"/users/{user_id}/session/today")
if session.is_error:
    fail(session, "Load today's session")
else:
    st.subheader("Today's session")
    st.markdown(session.json()["text"])

st.divider()

# ---------- active program ----------

programs_response = api_call("GET", f"/users/{user_id}/programs")
programs = programs_response.json() if not programs_response.is_error else []
active = next((p for p in programs if p["status"] == "active" and p["kind"] == "training"), None)

if active:
    st.subheader(f"Active program: {active['name']} (v{active['version']})")
    st.caption(active["summary"])
    weeks = active["payload"].get("weeks", [])
    tabs = st.tabs([f"Week {i + 1}: {w.get('focus', '')}" for i, w in enumerate(weeks)])
    for tab, week in zip(tabs, weeks):
        with tab:
            day_cols = st.columns(max(len(week.get("days", [])), 1))
            for col, day in zip(day_cols, week.get("days", [])):
                with col:
                    st.markdown(f"**{day.get('name')}**  \n_{day.get('focus', '')}_")
                    for exercise in day.get("exercises", []):
                        guidance = exercise.get("weight_guidance", "")
                        rpe = f" (RPE {exercise['rpe_target']})" if exercise.get("rpe_target") else ""
                        st.markdown(
                            f"- {exercise.get('name')}: {exercise.get('sets')} x {exercise.get('reps')}"
                            + (f" @ {guidance}" if guidance else "") + rpe
                        )
    with st.expander("Why this program"):
        st.markdown(active.get("rationale") or "_No rationale stored._")
else:
    st.subheader("Active program")
    st.info("No active training program. Generate one below.")

# ---------- generate / versions ----------

with st.expander("Generate a new program"):
    brief = st.text_input(
        "Brief (optional)", placeholder="4 days/week, home gym with rack + dumbbells, focus on squat strength"
    )
    if st.button("Generate (plan model, ~30s)", type="primary"):
        with st.spinner("Designing your program..."):
            r = api_call("POST", f"/users/{user_id}/programs/generate", json={"kind": "training", "brief": brief or None})
        if r.is_error:
            fail(r, "Generation")
        else:
            program = r.json()
            st.session_state["flash_program"] = program
            st.rerun()

if draft := st.session_state.pop("flash_program", None):
    st.success(f"Generated draft: {draft['name']} (v{draft['version']}). {draft['summary']}")

st.subheader("Versions")
for program in programs:
    if program["kind"] != "training":
        continue
    cols = st.columns([6, 1, 1])
    cols[0].markdown(
        f"**v{program['version']} — {program['name']}** ({program['status']}, {program['created_at'][:10]})  \n"
        f"{program['summary']}"
    )
    if program["status"] == "draft" and cols[1].button("Activate", key=f"act_{program['id']}"):
        r = api_call("POST", f"/users/{user_id}/programs/{program['id']}/activate")
        if r.is_error:
            fail(r, "Activation")
        st.rerun()
    if program["status"] == "active":
        if cols[2].button("Review", key=f"rev_{program['id']}"):
            with st.spinner("Reviewing adherence..."):
                r = api_call("POST", f"/users/{user_id}/programs/{program['id']}/review", params={"regenerate": "false"})
            if r.is_error:
                fail(r, "Review")
            else:
                st.session_state["review_result"] = r.json()["assessment"]
                st.rerun()

if review := st.session_state.pop("review_result", None):
    st.subheader("Latest review")
    st.markdown(review)

st.divider()

# ---------- log a workout ----------

st.subheader("Log a workout")
with st.form("log_workout", clear_on_submit=True):
    day = st.date_input("Date", value=date.today(), max_value=date.today())
    session_name = st.text_input("Session name", placeholder="Lower A")
    exercises_text = st.text_area(
        "Exercises (one per line: Name: 3x8 @80 rpe8)",
        placeholder="Leg press: 4x10 @120 rpe7\nLeg curl: 3x12",
        height=120,
    )
    duration = st.number_input("Duration (minutes)", min_value=0, max_value=600, value=None)
    notes = st.text_input("Notes (optional)")
    if st.form_submit_button("Save workout"):
        lines = [line for line in exercises_text.splitlines() if line.strip()]
        if not session_name.strip() or not lines:
            st.error("Give the session a name and at least one exercise line.")
        else:
            payload = {
                "date": day.isoformat(),
                "session_name": session_name.strip(),
                "exercises": [parse_exercise_line(line) for line in lines],
                "duration_min": duration,
                "notes": notes or None,
            }
            r = api_call("POST", f"/users/{user_id}/workouts", json=payload)
            if r.is_error:
                fail(r, "Save")
            else:
                st.success(f"Workout for {day} saved.")
                st.rerun()

workouts = api_call("GET", f"/users/{user_id}/workouts", params={"days": 14})
if not workouts.is_error and workouts.json():
    st.subheader("Last 14 days")
    for workout in reversed(workouts.json()):
        detail = "; ".join(
            f"{e['name']} {len(e['sets'])}x"
            + (f"@{max((s.get('weight_kg') or 0) for s in e['sets']):g}kg" if any(s.get("weight_kg") for s in e["sets"]) else "")
            for e in workout["exercises"]
        )
        duration = f", {workout['duration_min']} min" if workout.get("duration_min") else ""
        st.markdown(f"- **{workout['date']}** — {workout['session_name']}{duration}: {detail}")
elif not workouts.is_error:
    st.caption("No workouts logged in the last 14 days.")
