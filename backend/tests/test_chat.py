def post(client, session_id, message, **kwargs):
    payload = {"session_id": session_id, "message": message}
    payload.update(kwargs)
    return client.post("/chat", json=payload)


def test_live_inference_metadata_contract(client):
    response = post(client, "s-1", "What is the capital of France?")
    assert response.status_code == 200
    body = response.json()

    assert body["session_id"] == "s-1"
    assert body["reply"]

    meta = body["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["model"] == "gemini/gemini-2.0-flash"
    assert isinstance(meta["latency_ms"], int) and meta["latency_ms"] >= 0
    assert meta["input_tokens"] > 0
    assert meta["output_tokens"] > 0
    assert meta["key_id"] == "key_1"
    assert meta["fallback_attempts"] == 0


def test_api_key_is_never_leaked(client):
    body = post(client, "s-secret", "hello").text
    assert "test-key-one" not in body and "test-key-two" not in body


def test_multi_turn_replays_history_to_the_model(client, fake_llm):
    post(client, "s-2", "My name is Harshit.")
    post(client, "s-2", "What is my name?")

    sent = fake_llm.last_messages
    assert sent[0]["role"] == "system"
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[1]["content"] == "My name is Harshit."
    assert sent[-1]["content"] == "What is my name?"


def test_sessions_are_independent(client, fake_llm):
    post(client, "s-a", "first session message")
    post(client, "s-b", "second session message")
    post(client, "s-b", "another one in b")

    assert len(client.get("/sessions/s-a/messages").json()["messages"]) == 2
    assert len(client.get("/sessions/s-b/messages").json()["messages"]) == 4


def test_tokens_are_per_request_not_cumulative(client):
    first = post(client, "s-3", "aaaa bbbb cccc dddd").json()["metadata"]
    second = post(client, "s-3", "eeee ffff gggg hhhh").json()["metadata"]

    # the second request carries history, so its prompt is larger ...
    assert second["input_tokens"] > first["input_tokens"]
    # ... but output tokens describe this reply only, never a running total
    assert first["output_tokens"] == second["output_tokens"]


def test_equal_length_first_messages_have_similar_input_tokens(client):
    one = post(client, "s-4", "tell me about cats").json()["metadata"]
    two = post(client, "s-5", "tell me about dogs").json()["metadata"]
    assert abs(one["input_tokens"] - two["input_tokens"]) <= 2


def test_system_prompt_overrides_and_sticks_to_the_session(client, fake_llm):
    post(client, "s-6", "hello", system_prompt="You are a pirate.")
    assert fake_llm.last_messages[0]["content"] == "You are a pirate."

    post(client, "s-6", "again")
    assert fake_llm.last_messages[0]["content"] == "You are a pirate."


def test_default_system_prompt_is_used_when_none_given(client, fake_llm):
    post(client, "s-7", "hello")
    assert fake_llm.last_messages[0]["content"] == "You are a test assistant."
