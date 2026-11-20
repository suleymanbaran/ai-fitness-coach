from datetime import date

from app.data_access.users import DailyMetrics, UserProfile
from app.rag.prompt import format_user_context


def test_historical_total_burn_uses_current_discount_without_mutating_raw_values():
    history = [
        DailyMetrics(date=date(2026, 1, 1), active_kcal=600, total_kcal=2639),
        DailyMetrics(date=date(2026, 1, 2), active_kcal=610, total_kcal=2639),
    ]
    profile = UserProfile(user_id="u1", name="Test", goal="fitness", days=history)

    context = format_user_context(profile)

    assert "TOTAL energy burned (Fitbit x0.9, incl. BMR) 2375 kcal" in context
    assert context.count("total burn 2375 kcal") == 2
    assert [day.total_kcal for day in history] == [2639, 2639]
