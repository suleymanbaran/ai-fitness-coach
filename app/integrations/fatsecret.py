"""FatSecret Platform API: daily calories from the user's food diary, food search and nutrition.

Reading a member's diary requires 3-legged OAuth 1.0 (FatSecret's OAuth 2.0 cannot act for a user).
The free Basic tier (5,000 calls/day, US dataset) covers food_entries.get.v2, foods.search v1,
food.get v6 and food_entry.create; foods.search v6 is Premier-only and gated behind `premier`.
Docs: https://platform.fatsecret.com/docs/guides/authentication/oauth1/three-legged
"""

from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import parse_qsl, urlencode

import httpx
from oauthlib.oauth1 import SIGNATURE_TYPE_BODY, SIGNATURE_TYPE_QUERY, Client

from app.integrations.common import IntegrationError, ReauthRequired, request_with_retry

REQUEST_TOKEN_URL = "https://authentication.fatsecret.com/oauth/request_token"
AUTHORIZE_URL = "https://authentication.fatsecret.com/oauth/authorize"
ACCESS_TOKEN_URL = "https://authentication.fatsecret.com/oauth/access_token"
FOOD_ENTRIES_URL = "https://platform.fatsecret.com/rest/food-entries/v2"
FOOD_ENTRY_CREATE_URL = "https://platform.fatsecret.com/rest/food-entries/v1"
FOOD_SEARCH_URL_V1 = "https://platform.fatsecret.com/rest/foods/search/v1"
FOOD_SEARCH_URL_V6 = "https://platform.fatsecret.com/rest/foods/search/v6"
FOOD_GET_URL = "https://platform.fatsecret.com/rest/food/v6"
EPOCH = date(1970, 1, 1)
# FatSecret error codes meaning the stored access token is no longer valid.
INVALID_TOKEN_CODES = {9, 13}
# FatSecret diary meal slots; our "snack" maps to "other".
FATSECRET_MEALS = {"breakfast": "breakfast", "lunch": "lunch", "dinner": "dinner", "snack": "other"}


@dataclass(frozen=True)
class FoodHit:
    food_id: str
    name: str
    food_type: str
    description: str  # e.g. "Per 1 serving - Calories: 300kcal | Fat: 13.00g | ..."


@dataclass(frozen=True)
class FoodServing:
    serving_id: str
    description: str
    calories: int
    carbohydrate_g: float | None
    protein_g: float | None
    fat_g: float | None
    fiber_g: float | None
    metric_amount: float | None
    metric_unit: str | None


@dataclass(frozen=True)
class FoodDetail:
    food_id: str
    name: str
    brand: str | None
    servings: list[FoodServing]


class FatSecretClient:
    provider = "fatsecret"

    def __init__(
        self, consumer_key: str, consumer_secret: str, http: httpx.Client | None = None, premier: bool = False
    ) -> None:
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.http = http or httpx.Client(timeout=20)
        self.premier = premier

    def start_authorization(self, callback_url: str) -> tuple[str, str, str]:
        """Step 1-2: get a request token. Returns (URL to send the user to, request token, request secret)."""
        # FatSecret verifies this POST against the form body. The same signature in the
        # query string is rejected as invalid.
        client = Client(
            self.consumer_key,
            client_secret=self.consumer_secret,
            callback_uri=callback_url,
            signature_type=SIGNATURE_TYPE_BODY,
        )
        uri, _, body = client.sign(
            REQUEST_TOKEN_URL,
            http_method="POST",
            body="",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = request_with_retry(
            self.http,
            "POST",
            uri,
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.is_error:
            raise IntegrationError(f"FatSecret request token failed ({response.status_code}): {response.text[:120]}")
        values = dict(parse_qsl(response.text))
        token, secret = values["oauth_token"], values["oauth_token_secret"]
        return f"{AUTHORIZE_URL}?{urlencode({'oauth_token': token})}", token, secret

    def finish_authorization(self, request_token: str, request_secret: str, verifier: str) -> tuple[str, str]:
        """Step 3: exchange the approved request token for the member's access token and secret."""
        client = Client(
            self.consumer_key,
            client_secret=self.consumer_secret,
            resource_owner_key=request_token,
            resource_owner_secret=request_secret,
            verifier=verifier,
            signature_type=SIGNATURE_TYPE_QUERY,
        )
        uri, _, _ = client.sign(ACCESS_TOKEN_URL, http_method="GET")
        response = request_with_retry(self.http, "GET", uri)
        if response.is_error:
            raise IntegrationError(f"FatSecret access token failed ({response.status_code}): {response.text[:120]}")
        values = dict(parse_qsl(response.text))
        return values["oauth_token"], values["oauth_token_secret"]

    def fetch_days(self, token: str, secret: str, start: date, end: date) -> dict[date, dict]:
        """Total calories per day. Days with no diary entries are left out: not logged is not zero eaten."""
        days = {}
        day = start
        while day <= end:
            calories = self._day_calories(token, secret, day)
            if calories is not None:
                days[day] = {"calories_kcal": calories}
            day += timedelta(days=1)
        return days

    def _day_calories(self, token: str, secret: str, day: date) -> int | None:
        query = urlencode({"date": (day - EPOCH).days, "format": "json"})
        response = self._signed_get(f"{FOOD_ENTRIES_URL}?{query}", token, secret)
        return parse_food_entries(response.json())

    def search_foods(
        self, token: str | None, secret: str | None, query: str, max_results: int = 5
    ) -> list[FoodHit]:
        """Public food search. Works app-only (token/secret None) on a free Basic key;
        the user's 3-legged token is only needed for diary endpoints."""
        url = FOOD_SEARCH_URL_V6 if self.premier else FOOD_SEARCH_URL_V1
        params = urlencode({"search_expression": query, "max_results": max_results, "format": "json"})
        response = self._signed_get(f"{url}?{params}", token, secret)
        return parse_food_search(response.json())

    def get_food(self, token: str | None, secret: str | None, food_id: str) -> FoodDetail:
        query = urlencode({"food_id": food_id, "format": "json"})
        response = self._signed_get(f"{FOOD_GET_URL}?{query}", token, secret)
        return parse_food_detail(response.json())

    def create_food_entry(
        self, token: str, secret: str, *, food_id: str, serving_id: str, name: str, meal: str, day: date,
        units: float = 1.0,
    ) -> str:
        """Write a meal into the user's real FatSecret diary. Returns the new food_entry_id."""
        params = {
            "food_id": food_id,
            "food_entry_name": name[:200],
            "serving_id": serving_id,
            "number_of_units": units,
            "meal": FATSECRET_MEALS.get(meal, "other"),
            "date": (day - EPOCH).days,
            "format": "json",
        }
        # FatSecret verifies this POST against the form body, like the request-token call.
        client = Client(
            self.consumer_key,
            client_secret=self.consumer_secret,
            resource_owner_key=token,
            resource_owner_secret=secret,
            signature_type=SIGNATURE_TYPE_BODY,
        )
        uri, _, body = client.sign(
            FOOD_ENTRY_CREATE_URL,
            http_method="POST",
            body=urlencode(params),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = request_with_retry(
            self.http, "POST", uri, content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.is_error:
            raise IntegrationError(f"FatSecret food entry create failed ({response.status_code})")
        return parse_food_entry_id(response.json())

    def delete_food_entry(self, token: str, secret: str, entry_id: str) -> bool:
        """Remove a diary entry written by create_food_entry. Free Basic tier."""
        query = urlencode({"food_entry_id": entry_id, "format": "json"})
        response = self._signed_delete(f"{FOOD_ENTRY_CREATE_URL}?{query}", token, secret)
        body = response.json()
        _raise_for_error(body)
        success = body.get("success")
        return str(success.get("value") if isinstance(success, dict) else success) == "1"

    def _signed_get(self, url_with_query: str, token: str | None = None, secret: str | None = None) -> httpx.Response:
        client = Client(
            self.consumer_key,
            client_secret=self.consumer_secret,
            resource_owner_key=token,
            resource_owner_secret=secret,
            signature_type=SIGNATURE_TYPE_QUERY,
        )
        uri, _, _ = client.sign(url_with_query, http_method="GET")
        response = request_with_retry(self.http, "GET", uri)
        if response.is_error:
            raise IntegrationError(f"FatSecret request failed ({response.status_code})")
        return response

    def _signed_delete(self, url_with_query: str, token: str, secret: str) -> httpx.Response:
        client = Client(
            self.consumer_key,
            client_secret=self.consumer_secret,
            resource_owner_key=token,
            resource_owner_secret=secret,
            signature_type=SIGNATURE_TYPE_QUERY,
        )
        uri, _, _ = client.sign(url_with_query, http_method="DELETE")
        response = request_with_retry(self.http, "DELETE", uri)
        if response.is_error:
            raise IntegrationError(f"FatSecret delete failed ({response.status_code})")
        return response


def _raise_for_error(body: dict) -> None:
    # FatSecret reports errors with HTTP 200 and an "error" object.
    if error := body.get("error"):
        code, message = int(error.get("code", 0)), error.get("message", "unknown error")
        if code in INVALID_TOKEN_CODES:
            raise ReauthRequired("FatSecret rejected the stored login. Connect FatSecret again.")
        raise IntegrationError(f"FatSecret error {code}: {message}")


def _as_list(value) -> list[dict]:
    # FatSecret returns a single object where a list is expected.
    if value is None:
        return []
    return [value] if isinstance(value, dict) else list(value)


def parse_food_entries(body: dict) -> int | None:
    _raise_for_error(body)
    entries = _as_list((body.get("food_entries") or {}).get("food_entry"))
    if not entries:
        return None
    return round(sum(float(e.get("calories") or 0) for e in entries))


def parse_food_search(body: dict) -> list[FoodHit]:
    _raise_for_error(body)
    foods = _as_list((body.get("foods") or {}).get("food"))
    return [
        FoodHit(
            food_id=str(f.get("food_id", "")),
            name=str(f.get("food_name", "")),
            food_type=str(f.get("food_type", "")),
            description=str(f.get("food_description", "")),
        )
        for f in foods
        if f.get("food_id")
    ]


def parse_food_detail(body: dict) -> FoodDetail:
    _raise_for_error(body)
    food = body.get("food") or {}
    servings = _as_list((food.get("servings") or {}).get("serving"))
    return FoodDetail(
        food_id=str(food.get("food_id", "")),
        name=str(food.get("food_name", "")),
        brand=food.get("brand_name"),
        servings=[
            FoodServing(
                serving_id=str(s.get("serving_id", "")),
                description=str(s.get("serving_description", "")),
                calories=round(float(s.get("calories") or 0)),
                carbohydrate_g=_float_or_none(s.get("carbohydrate")),
                protein_g=_float_or_none(s.get("protein")),
                fat_g=_float_or_none(s.get("fat")),
                fiber_g=_float_or_none(s.get("fiber")),
                metric_amount=_float_or_none(s.get("metric_serving_amount")),
                metric_unit=s.get("metric_serving_unit"),
            )
            for s in servings
        ],
    )


def parse_food_entry_id(body: dict) -> str:
    _raise_for_error(body)
    value = body.get("food_entry_id")
    if isinstance(value, dict):  # JSON shape: {"food_entry_id": {"value": "111"}}
        return str(value.get("value", ""))
    return str(value or "")


def _float_or_none(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
