"""CoachClient unit tests against a SimpleNamespace fake OpenAI client (no network)."""

from types import SimpleNamespace

import pytest

from app.rag.generator import CoachClient, estimate_tokens

LONG_STABLE = "coach rules and memories. " * 400  # ~4800 chars -> ~1200 estimated tokens


def make_client(*responses, capture=None):
    """A fake OpenAI client whose responses.create replays `responses` in order."""
    def create(**kwargs):
        if capture is not None:
            capture.append(kwargs)
        return responses[len(capture) - 1 if capture else 0]

    return SimpleNamespace(responses=SimpleNamespace(create=create))


def text_response(text="Rest today.", input_tokens=100, cached=0, output_tokens=5):
    return SimpleNamespace(
        output=[],
        output_text=text,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_tokens_details=SimpleNamespace(cached_tokens=cached),
        ),
    )


def tool_response(name, arguments, call_id="call_1"):
    return SimpleNamespace(
        output=[SimpleNamespace(type="function_call", call_id=call_id, name=name, arguments=arguments)],
        output_text="",
        usage=SimpleNamespace(
            input_tokens=50, output_tokens=2, input_tokens_details=SimpleNamespace(cached_tokens=10)
        ),
    )


def coach(client, **kwargs) -> CoachClient:
    return CoachClient(client, fast_model="fast-model", plan_model="plan-model", **kwargs)


def test_long_stable_prefix_gets_explicit_breakpoint():
    calls = []
    client = make_client(text_response(), capture=calls)

    reply = coach(client).complete(LONG_STABLE, [], "question", cache_key="coach:u1")

    kwargs = calls[0]
    developer = kwargs["input"][0]
    assert developer["role"] == "developer"
    assert developer["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit", "ttl": "12h"}
    assert kwargs["prompt_cache_options"] == {"mode": "implicit", "ttl": "30m"}
    assert kwargs["prompt_cache_key"] == "coach:u1"
    assert kwargs["model"] == "fast-model"
    assert kwargs["reasoning"] == {"effort": "low"}
    assert reply.text == "Rest today."
    assert (reply.usage.input_tokens, reply.usage.cached_tokens, reply.usage.output_tokens) == (100, 0, 5)


def test_short_stable_prefix_omits_breakpoint():
    # Below the 1,024-token minimum, cache writes at 1.25x cost more than they save.
    calls = []
    client = make_client(text_response(), capture=calls)
    assert estimate_tokens("short") < 1024

    coach(client).complete("short stable", [], "question")

    assert "prompt_cache_breakpoint" not in calls[0]["input"][0]["content"][0]


def test_caching_disabled_sends_no_cache_options():
    calls = []
    client = make_client(text_response(), capture=calls)

    coach(client, enable_caching=False).complete(LONG_STABLE, [], "question")

    assert "prompt_cache_options" not in calls[0]
    assert "prompt_cache_breakpoint" not in calls[0]["input"][0]["content"][0]


def test_history_replay_uses_correct_part_types_and_order():
    calls = []
    client = make_client(text_response(), capture=calls)
    history = [{"role": "user", "content": "earlier q"}, {"role": "assistant", "content": "earlier a"}]

    coach(client).complete("stable", history, "volatile")

    items = calls[0]["input"]
    assert [item["role"] for item in items] == ["developer", "user", "assistant", "user"]
    assert items[1]["content"][0] == {"type": "input_text", "text": "earlier q"}
    assert items[2]["content"][0] == {"type": "output_text", "text": "earlier a"}
    assert items[3]["content"][0]["text"] == "volatile"


def test_plan_tier_routes_to_plan_model():
    calls = []
    client = make_client(text_response(), capture=calls)

    coach(client).complete("stable", [], "build my program", tier="plan", effort="medium")

    assert calls[0]["model"] == "plan-model"
    assert calls[0]["reasoning"] == {"effort": "medium"}


def test_json_schema_passed_as_strict_text_format():
    calls = []
    client = make_client(text_response(text='{"updates": []}'), capture=calls)
    schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}

    coach(client).complete("stable", [], "extract", json_schema=schema, schema_name="memory_updates")

    assert calls[0]["text"] == {
        "format": {"type": "json_schema", "name": "memory_updates", "schema": schema, "strict": True}
    }


def test_tool_loop_runs_handler_and_returns_final_text():
    calls = []
    client = make_client(
        tool_response("log_day", '{"date": "2026-01-02", "hrv_ms": 55}'),
        text_response(input_tokens=80, cached=60),
        capture=calls,
    )
    seen = []
    handlers = {"log_day": lambda args: seen.append(args) or "logged 2026-01-02"}
    tools = [{"type": "function", "name": "log_day", "parameters": {}}]

    reply = coach(client).complete("stable", [], "log it", tools=tools, tool_handlers=handlers)

    assert seen == [{"date": "2026-01-02", "hrv_ms": 55}]
    assert reply.text == "Rest today."
    # Usage accumulates across iterations; the second call replays the model's output + tool result.
    assert (reply.usage.input_tokens, reply.usage.cached_tokens) == (130, 70)
    second_items = calls[1]["input"]
    replayed_call = second_items[-2]  # the model's function_call output item, replayed as-is
    assert (replayed_call.type, replayed_call.name) == ("function_call", "log_day")
    assert second_items[-1] == {"type": "function_call_output", "call_id": "call_1", "output": "logged 2026-01-02"}


def test_builtin_tools_pass_through_without_handlers():
    calls = []
    client = make_client(text_response(), capture=calls)

    reply = coach(client).complete("stable", [], "how many kcal in a simit?", tools=[{"type": "web_search"}])

    assert calls[0]["tools"] == [{"type": "web_search"}]
    assert calls[0]["tool_choice"] == "auto"
    assert reply.text == "Rest today."
    assert len(calls) == 1  # built-in tools run inside OpenAI; no extra loop iteration


def test_non_function_output_items_end_the_loop():
    web_response = SimpleNamespace(
        output=[SimpleNamespace(type="web_search_call", id="ws_1", action=SimpleNamespace(query="simit kcal"))],
        output_text="A simit has ~280 kcal.",
        usage=SimpleNamespace(
            input_tokens=10, output_tokens=4, input_tokens_details=SimpleNamespace(cached_tokens=0)
        ),
    )
    calls = []
    client = make_client(web_response, capture=calls)

    reply = coach(client).complete("stable", [], "q", tools=[{"type": "web_search"}])

    assert reply.text == "A simit has ~280 kcal."
    assert len(calls) == 1


def test_failing_tool_returns_error_string_to_model():
    calls = []

    def boom(args):
        raise RuntimeError("db is down")

    client = make_client(tool_response("save_memory", "{}"), text_response(), capture=calls)
    handlers = {"save_memory": boom}

    coach(client).complete(
        "stable", [], "remember this",
        tools=[{"type": "function", "name": "save_memory", "parameters": {}}], tool_handlers=handlers,
    )

    assert calls[1]["input"][-1]["output"] == "error: db is down"


def test_unknown_tool_and_bad_arguments_do_not_raise():
    calls = []
    client = make_client(
        tool_response("nope", "{}", call_id="a"),
        tool_response("log_day", "{not json", call_id="b"),
        text_response(),
        capture=calls,
    )

    coach(client).complete(
        "stable", [], "x",
        tools=[{"type": "function", "name": "log_day", "parameters": {}}],
        tool_handlers={"log_day": lambda args: "ok"},
    )

    assert calls[1]["input"][-1]["output"] == "error: unknown tool nope"
    assert calls[2]["input"][-1]["output"] == "error: malformed tool arguments"


def test_exhausted_tool_budget_forces_final_answer_without_tools():
    responses = [tool_response("log_day", "{}", call_id=str(i)) for i in range(2)] + [text_response()]
    calls = []
    client = make_client(*responses, capture=calls)
    tools = [{"type": "function", "name": "log_day", "parameters": {}}]

    reply = coach(client, max_tool_iterations=2).complete(
        "stable", [], "x", tools=tools, tool_handlers={"log_day": lambda args: "ok"}
    )

    assert reply.text == "Rest today."
    # 2 iterations with tools + 1 final call without
    assert len(calls) == 3
    assert "tools" in calls[0] and "tools" in calls[1]
    assert "tools" not in calls[2] and "tool_choice" not in calls[2]


def test_missing_usage_details_defaults_to_zero():
    client = make_client(
        SimpleNamespace(
            output=[], output_text="ok",
            usage=SimpleNamespace(input_tokens=10, output_tokens=1, input_tokens_details=None),
        )
    )
    reply = coach(client).complete("stable", [], "x")
    assert reply.usage.cached_tokens == 0


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
