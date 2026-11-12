"""Google Health API (successor of the Fitbit Web API): daily HRV, resting heart rate and sleep.

Docs: https://developers.google.com/health
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode

import httpx

from app.integrations.common import IntegrationError, ReauthRequired, request_with_retry

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DATA_TYPES_URL = "https://health.googleapis.com/v4/users/me/dataTypes"
SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
]


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime


class GoogleHealthClient:
    provider = "google"

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, http: httpx.Client | None = None) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.http = http or httpx.Client(timeout=20)

    def authorization_url(self, state: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            # offline + consent: Google returns a refresh token, so syncing keeps working after the
            # one-hour access token expires.
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> OAuthTokens:
        return self._token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri})

    def refresh(self, refresh_token: str) -> OAuthTokens:
        tokens = self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})
        # Google usually omits the refresh token on refresh; the original one stays valid.
        return OAuthTokens(tokens.access_token, tokens.refresh_token or refresh_token, tokens.expires_at)

    def _token_request(self, data: dict) -> OAuthTokens:
        data |= {"client_id": self.client_id, "client_secret": self.client_secret}
        response = request_with_retry(self.http, "POST", TOKEN_URL, data=data)
        if response.status_code in (400, 401):
            # invalid_grant: the refresh token was revoked or expired (e.g. a test-mode app after 7 days).
            raise ReauthRequired("Google rejected the stored login. Connect Google Health again.")
        if response.is_error:
            raise IntegrationError(f"Google token request failed ({response.status_code})")
        body = response.json()
        return OAuthTokens(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            expires_at=datetime.now(UTC) + timedelta(seconds=int(body.get("expires_in", 3600))),
        )

    def fetch_days(self, access_token: str, start: date, end: date) -> dict[date, dict]:
        """Metrics per day from `start` to `end` inclusive, keyed by the user's local date."""
        after = (end + timedelta(days=1)).isoformat()
        hrv = self._list(
            access_token,
            "daily-heart-rate-variability",
            f'daily_heart_rate_variability.date >= "{start}" AND daily_heart_rate_variability.date < "{after}"',
        )
        rhr = self._list(
            access_token,
            "daily-resting-heart-rate",
            f'daily_resting_heart_rate.date >= "{start}" AND daily_resting_heart_rate.date < "{after}"',
        )
        sleep = self._list(
            access_token,
            "sleep",
            f'sleep.interval.civil_end_time >= "{start}" AND sleep.interval.civil_end_time < "{after}"',
        )

        days: dict[date, dict] = {}
        for day, value in parse_daily_hrv(hrv).items():
            days.setdefault(day, {})["hrv_ms"] = value
        for day, value in parse_resting_hr(rhr).items():
            days.setdefault(day, {})["resting_hr_bpm"] = value
        for day, value in parse_sleep_minutes(sleep).items():
            days.setdefault(day, {})["sleep_minutes"] = value
        for day, value in parse_sleep_stages(sleep).items():
            days.setdefault(day, {})["sleep_stages"] = value

        # Steps and active energy need the activity_and_fitness scope; users who connected
        # before that scope existed get a 403 here — degrade to the other metrics instead of
        # failing the whole sync.
        try:
            steps = self._daily_rollup(access_token, "steps", start, end)
            for day, value in parse_steps_rollup(steps).items():
                days.setdefault(day, {})["steps"] = value
            energy = self._daily_rollup(access_token, "active-energy-burned", start, end)
            for day, value in parse_active_energy_rollup(energy).items():
                days.setdefault(day, {})["active_kcal"] = value
            total = self._daily_rollup(access_token, "total-calories", start, end)
            for day, value in parse_total_calories_rollup(total).items():
                days.setdefault(day, {})["total_kcal"] = value
        except IntegrationError as exc:
            if "denied access" not in str(exc):
                raise

        return {day: values for day, values in days.items() if start <= day <= end}

    def fetch_exercises(self, access_token: str, start: date, end: date) -> list[dict]:
        """Exercise-session dataPoints between `start` and `end` (local dates). The exercise
        type accepts no server-side filter (400 INVALID_DATA_POINT_FILTER_DATA_TYPE_MEMBER),
        so pages walk the newest-first list and stop once they pass `start`."""
        points: list[dict] = []
        params: dict = {"pageSize": 100}
        while True:
            response = request_with_retry(
                self.http,
                "GET",
                f"{DATA_TYPES_URL}/exercise/dataPoints",
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if response.status_code == 401:
                raise ReauthRequired("Google rejected the access token. Connect Google Health again.")
            if response.status_code == 403:
                raise IntegrationError("Google denied access to exercise. Approve all requested permissions.")
            if response.is_error:
                raise IntegrationError(f"Google Health exercise request failed ({response.status_code})")
            body = response.json()
            batch = body.get("dataPoints", [])
            points.extend(batch)
            token = body.get("nextPageToken")
            if not token or not batch:
                break
            oldest = _interval_start_date((batch[-1].get("exercise") or {}).get("interval") or {})
            if oldest is not None and oldest < start:
                break
            params = {"pageSize": 100, "pageToken": token}
        return [
            p for p in points
            if (day := _interval_start_date((p.get("exercise") or {}).get("interval") or {})) is not None
            and start <= day <= end
        ]

    def _daily_rollup(self, access_token: str, data_type: str, start: date, end: date) -> list[dict]:
        """POST dataPoints:dailyRollUp — one aggregated point per civil day (newest first)."""

        def civil(day: date) -> dict:
            return {"date": {"year": day.year, "month": day.month, "day": day.day}, "time": {"hours": 0, "minutes": 0, "seconds": 0}}

        points: list[dict] = []
        body: dict = {
            "range": {"start": civil(start), "end": civil(end + timedelta(days=1))},
            "windowSizeDays": 1,
        }
        while True:
            response = request_with_retry(
                self.http,
                "POST",
                f"{DATA_TYPES_URL}/{data_type}/dataPoints:dailyRollUp",
                json=body,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if response.status_code == 401:
                raise ReauthRequired("Google rejected the access token. Connect Google Health again.")
            if response.status_code == 403:
                raise IntegrationError(f"Google denied access to {data_type}. Approve all requested permissions.")
            if response.is_error:
                raise IntegrationError(f"Google Health {data_type} rollup failed ({response.status_code})")
            payload = response.json()
            points.extend(payload.get("rollupDataPoints", []))
            if not payload.get("nextPageToken"):
                return points
            body = {**body, "pageToken": payload["nextPageToken"]}

    def fetch_measurements(self, access_token: str, start: date, end: date) -> dict[date, dict]:
        """Weight and body-fat samples per day (latest sample of each day wins).

        Only populated when the user weighs in via a Fitbit scale or the Fitbit/Google Health app;
        the band itself does not measure weight.
        """
        after = (end + timedelta(days=1)).isoformat()
        weight = self._list(
            access_token, "weight", f'weight.sample_time.civil_time >= "{start}" AND weight.sample_time.civil_time < "{after}"'
        )
        body_fat = self._list(
            access_token,
            "body-fat",
            f'body_fat.sample_time.civil_time >= "{start}" AND body_fat.sample_time.civil_time < "{after}"',
        )
        days: dict[date, dict] = {}
        for day, value in parse_weight(weight).items():
            days.setdefault(day, {})["weight_kg"] = value
        for day, value in parse_body_fat(body_fat).items():
            days.setdefault(day, {})["body_fat_pct"] = value
        return {day: values for day, values in days.items() if start <= day <= end}

    def _list(self, access_token: str, data_type: str, filter_expr: str) -> list[dict]:
        points: list[dict] = []
        params = {"filter": filter_expr}
        while True:
            response = request_with_retry(
                self.http,
                "GET",
                f"{DATA_TYPES_URL}/{data_type}/dataPoints",
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if response.status_code == 401:
                raise ReauthRequired("Google rejected the access token. Connect Google Health again.")
            if response.status_code == 403:
                raise IntegrationError(f"Google denied access to {data_type}. Approve all requested permissions.")
            if response.is_error:
                raise IntegrationError(f"Google Health {data_type} request failed ({response.status_code})")
            body = response.json()
            points.extend(body.get("dataPoints", []))
            if not body.get("nextPageToken"):
                return points
            params = {"filter": filter_expr, "pageToken": body["nextPageToken"]}


def _civil_date(value: dict) -> date:
    return date(value["year"], value["month"], value["day"])


def _sample_date(sample_time: dict) -> date | None:
    """Local date of a Sample-type data point: civil time when present, else the UTC date."""
    civil = sample_time.get("civilTime") or {}
    if civil.get("date"):
        return _civil_date(civil["date"])
    physical = sample_time.get("physicalTime")
    if physical:
        return date.fromisoformat(physical[:10])
    return None


def parse_weight(points: list[dict]) -> dict[date, float]:
    """Kilograms per local date; the latest sample of a day wins."""
    best: dict[date, tuple[str, float]] = {}
    for point in points:
        data = point.get("weight") or {}
        grams = data.get("weightGrams")
        sample_time = data.get("sampleTime") or {}
        day = _sample_date(sample_time)
        if day is None or grams is None:
            continue
        when = sample_time.get("physicalTime") or ""
        if day not in best or when >= best[day][0]:
            best[day] = (when, round(float(grams) / 1000, 1))
    return {day: kg for day, (_, kg) in best.items()}


def parse_body_fat(points: list[dict]) -> dict[date, float]:
    """Body-fat percentage per local date; the latest sample of a day wins."""
    best: dict[date, tuple[str, float]] = {}
    for point in points:
        data = point.get("bodyFat") or {}
        percentage = data.get("percentage")
        sample_time = data.get("sampleTime") or {}
        day = _sample_date(sample_time)
        if day is None or percentage is None:
            continue
        when = sample_time.get("physicalTime") or ""
        if day not in best or when >= best[day][0]:
            best[day] = (when, round(float(percentage), 1))
    return {day: pct for day, (_, pct) in best.items()}


def parse_daily_hrv(points: list[dict]) -> dict[date, float]:
    result = {}
    for point in points:
        data = point.get("dailyHeartRateVariability") or {}
        if "date" in data and data.get("averageHeartRateVariabilityMilliseconds") is not None:
            result[_civil_date(data["date"])] = round(float(data["averageHeartRateVariabilityMilliseconds"]), 1)
    return result


def parse_resting_hr(points: list[dict]) -> dict[date, int]:
    result = {}
    for point in points:
        data = point.get("dailyRestingHeartRate") or {}
        if "date" in data and data.get("beatsPerMinute") is not None:
            # 64-bit integers arrive as JSON strings.
            result[_civil_date(data["date"])] = int(data["beatsPerMinute"])
    return result


def _interval_end_date(interval: dict) -> date | None:
    """Local date an interval ends. Real responses carry endTime + endUtcOffset;
    civilEndTime is documented but not actually returned — support both."""
    civil = interval.get("civilEndTime") or {}
    if civil.get("date"):
        return _civil_date(civil["date"])
    end_time = interval.get("endTime")
    if not end_time:
        return None
    try:
        moment = datetime.fromisoformat(str(end_time).replace("Z", "+00:00"))
    except ValueError:
        return None
    offset_seconds = 0.0
    offset = interval.get("endUtcOffset") or ""
    if str(offset).endswith("s"):
        try:
            offset_seconds = float(str(offset)[:-1])
        except ValueError:
            pass
    return (moment + timedelta(seconds=offset_seconds)).date()


def _interval_start_date(interval: dict) -> date | None:
    """Local date an interval starts: startTime shifted by startUtcOffset."""
    start_time = interval.get("startTime")
    if not start_time:
        return None
    try:
        moment = datetime.fromisoformat(str(start_time).replace("Z", "+00:00"))
    except ValueError:
        return None
    offset_seconds = 0.0
    offset = interval.get("startUtcOffset") or ""
    if str(offset).endswith("s"):
        try:
            offset_seconds = float(str(offset)[:-1])
        except ValueError:
            pass
    return (moment + timedelta(seconds=offset_seconds)).date()


def _duration_seconds(value: str | None) -> float | None:
    """Google durations arrive as '1726s'."""
    if not value or not str(value).endswith("s"):
        return None
    try:
        return float(str(value)[:-1])
    except ValueError:
        return None


RUN_TYPES = {"RUNNING", "TREADMILL", "TRAIL_RUNNING", "TRACK_AND_FIELD"}
MAX_RUN_PACE_MIN_PER_KM = 10.0  # slower than this is a walk, not a run


def parse_runs(points: list[dict]) -> list[dict]:
    """Running sessions as run_log-ready dicts. Skips non-run exercise types, sessions
    without distance, and walk-pace sessions (Fitbit labels treadmill walks as TREADMILL)."""
    runs = []
    for point in points:
        exercise = point.get("exercise") or {}
        if exercise.get("exerciseType") not in RUN_TYPES:
            continue
        interval = exercise.get("interval") or {}
        day = _interval_start_date(interval)
        if day is None:
            continue
        metrics = exercise.get("metricsSummary") or {}
        millimeters = metrics.get("distanceMillimeters")
        if not millimeters:
            continue
        distance_km = float(millimeters) / 1_000_000
        if distance_km < 0.5:
            continue
        seconds = _duration_seconds(exercise.get("activeDuration"))
        if seconds is None:
            start_time = interval.get("startTime")
            end_time = interval.get("endTime")
            if not start_time or not end_time:
                continue
            seconds = (
                datetime.fromisoformat(str(end_time).replace("Z", "+00:00"))
                - datetime.fromisoformat(str(start_time).replace("Z", "+00:00"))
            ).total_seconds()
        duration_min = round(seconds / 60, 1)
        if duration_min <= 0 or duration_min / distance_km > MAX_RUN_PACE_MIN_PER_KM:
            continue
        hr = metrics.get("averageHeartRateBeatsPerMinute")
        runs.append({
            "day": day,
            "distance_km": round(distance_km, 2),
            "duration_min": duration_min,
            "avg_hr_bpm": int(hr) if hr else None,
            "external_id": str(point.get("name", ""))[:200] or None,
        })
    return runs


def _main_sleeps(points: list[dict]) -> dict[date, dict]:
    """The main sleep session per local end date (naps lose), with minutes and stage breakdown."""
    best: dict[date, tuple[bool, int, dict]] = {}
    for point in points:
        sleep = point.get("sleep") or {}
        summary = sleep.get("summary") or {}
        minutes = summary.get("minutesAsleep")
        day = _interval_end_date(sleep.get("interval") or {})
        if minutes is None or day is None:
            continue  # still processing, or no usable time available
        is_main = bool((sleep.get("metadata") or {}).get("mainSleep"))
        candidate = (is_main, int(minutes))
        if day in best and candidate <= best[day][:2]:
            continue
        stages = {
            str(stage.get("type", "")).lower(): int(stage["minutes"])
            for stage in summary.get("stagesSummary") or []
            if stage.get("type") and stage.get("minutes") is not None
        }
        best[day] = (is_main, int(minutes), stages)
    return {day: {"minutes": minutes, "stages": stages} for day, (_, minutes, stages) in best.items()}


def parse_sleep_minutes(points: list[dict]) -> dict[date, int]:
    """Minutes asleep in the main sleep of each night, keyed by the local date the sleep ended."""
    return {day: values["minutes"] for day, values in _main_sleeps(points).items()}


def parse_sleep_stages(points: list[dict]) -> dict[date, dict]:
    """Stage minutes (deep/rem/light/awake) of each night's main sleep, when Fitbit reported them."""
    return {day: values["stages"] for day, values in _main_sleeps(points).items() if values["stages"]}


def parse_steps_rollup(points: list[dict]) -> dict[date, int]:
    """Steps per civil day from dailyRollUp. Unworn days omit the field and are skipped;
    a worn-but-idle day reports countSum "0" and is stored as a real zero."""
    result = {}
    for point in points:
        data = point.get("steps") or {}
        civil = (point.get("civilStartTime") or {}).get("date")
        if civil and data.get("countSum") is not None:
            result[_civil_date(civil)] = int(data["countSum"])
    return result


def parse_active_energy_rollup(points: list[dict]) -> dict[date, int]:
    """Active (exercise) kilocalories burned per civil day from dailyRollUp."""
    result = {}
    for point in points:
        data = point.get("activeEnergyBurned") or {}
        civil = (point.get("civilStartTime") or {}).get("date")
        if civil and data.get("kcalSum") is not None:
            result[_civil_date(civil)] = int(round(float(data["kcalSum"])))
    return result


def parse_total_calories_rollup(points: list[dict]) -> dict[date, int]:
    """Total energy expenditure (BMR + activity, what the Fitbit app shows) per civil day."""
    result = {}
    for point in points:
        data = point.get("totalCalories") or {}
        civil = (point.get("civilStartTime") or {}).get("date")
        if civil and data.get("kcalSum") is not None:
            result[_civil_date(civil)] = int(round(float(data["kcalSum"])))
    return result
