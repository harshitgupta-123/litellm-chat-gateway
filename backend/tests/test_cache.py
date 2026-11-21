def post(client, session_id, message, **kwargs):
    payload = {"session_id": session_id, "message": message}
    payload.update(kwargs)
    return client.post("/chat", json=payload)


QUESTION = "What is the capital of France?"


def test_second_identical_first_message_comes_from_cache(client):
    first = post(client, "cache-1", QUESTION).json()
    second = post(client, "cache-2", QUESTION).json()

    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert second["metadata"]["resolution_source"] == "LLM_CACHE"
    # identical reply text
    assert second["reply"] == first["reply"]
    # cached responses carry the stored usage and no key
    assert second["metadata"]["input_tokens"] == first["metadata"]["input_tokens"]
    assert second["metadata"]["output_tokens"] == first["metadata"]["output_tokens"]
    assert second["metadata"]["key_id"] is None
    assert second["metadata"]["fallback_attempts"] == 0


def test_cache_hit_is_faster_than_live_inference(client):
    live = post(client, "cache-3", QUESTION).json()["metadata"]
    cached = post(client, "cache-4", QUESTION).json()["metadata"]
    assert cached["resolution_source"] == "LLM_CACHE"
    assert cached["latency_ms"] < live["latency_ms"]


def test_cache_hit_does_not_call_the_provider(client, fake_llm):
    post(client, "cache-5", QUESTION)
    calls_after_live = len(fake_llm.calls)
    post(client, "cache-6", QUESTION)
    assert len(fake_llm.calls) == calls_after_live


def test_a_different_message_is_never_served_from_cache(client):
    post(client, "cache-7", QUESTION)
    other = post(client, "cache-8", "What is the capital of Italy?").json()
    assert other["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_history_makes_the_cache_key_different(client):
    post(client, "cache-9", QUESTION)
    # same text, but now sent as a second turn -> different message list
    follow_up = post(client, "cache-9", QUESTION).json()
    assert follow_up["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_cached_turns_are_still_stored_in_history(client):
    post(client, "cache-10", QUESTION)
    post(client, "cache-11", QUESTION)
    history = client.get("/sessions/cache-11/messages").json()["messages"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["metadata"]["resolution_source"] == "LLM_CACHE"
