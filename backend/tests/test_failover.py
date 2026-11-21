from .conftest import ProviderError


def post(client, session_id, message, headers=None):
    return client.post(
        "/chat",
        json={"session_id": session_id, "message": message},
        headers=headers or {},
    )


def test_simulated_429_on_key_1_falls_over_to_key_2(client):
    body = post(client, "f-1", "hello", {"X-Simulate-Failure": "key_1=429"}).json()
    assert body["metadata"]["key_id"] == "key_2"
    assert body["metadata"]["fallback_attempts"] == 1
    assert body["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert body["reply"]


def test_simulated_500_on_key_1_falls_over_to_key_2(client):
    response = post(client, "f-2", "hello", {"X-Simulate-Failure": "key_1=500"})
    assert response.status_code == 200
    assert response.json()["metadata"]["key_id"] == "key_2"


def test_all_keys_failing_returns_a_clean_503(client):
    response = post(client, "f-3", "hello", {"X-Simulate-Failure": "*=429"})
    assert response.status_code == 503
    assert response.json() == {"detail": "all providers unavailable"}
    # nothing was added to the session history
    assert client.get("/sessions/f-3/messages").status_code == 404


def test_simulation_bypasses_the_cache(client):
    post(client, "f-4", "shared question")
    response = post(client, "f-5", "shared question", {"X-Simulate-Failure": "key_1=429"})
    body = response.json()
    assert body["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert body["metadata"]["key_id"] == "key_2"


def test_listing_every_key_explicitly_also_returns_503(client):
    response = post(client, "f-6", "hello", {"X-Simulate-Failure": "key_1=429,key_2=503"})
    assert response.status_code == 503


def test_malformed_simulation_header_is_ignored(client):
    response = post(client, "f-7", "hello", {"X-Simulate-Failure": "garbage;;"})
    assert response.status_code == 200
    assert response.json()["metadata"]["key_id"] == "key_1"


def test_real_rate_limit_error_falls_over(client, fake_llm):
    fake_llm.fail["test-key-one"] = ProviderError("rate limit exceeded", 429)
    body = post(client, "f-8", "hello").json()
    assert body["metadata"]["key_id"] == "key_2"
    assert body["metadata"]["fallback_attempts"] == 1


def test_real_quota_error_falls_over(client, fake_llm):
    fake_llm.fail["test-key-one"] = ProviderError("You exceeded your current quota", 400)
    assert post(client, "f-9", "hello").json()["metadata"]["key_id"] == "key_2"


def test_real_5xx_error_falls_over(client, fake_llm):
    fake_llm.fail["test-key-one"] = ProviderError("internal server error", 500)
    assert post(client, "f-10", "hello").json()["metadata"]["key_id"] == "key_2"


def test_every_real_key_failing_returns_503_without_a_stack_trace(client, fake_llm):
    fake_llm.fail["test-key-one"] = ProviderError("rate limit", 429)
    fake_llm.fail["test-key-two"] = ProviderError("service unavailable", 503)
    response = post(client, "f-11", "hello")
    assert response.status_code == 503
    assert response.json() == {"detail": "all providers unavailable"}
    assert "Traceback" not in response.text


def test_empty_completion_is_treated_as_a_failure(client, fake_llm):
    """A 200 with no text must not reach the user - the pool moves on."""
    fake_llm.empty.add("test-key-one")
    body = post(client, "f-12", "hello").json()
    assert body["metadata"]["key_id"] == "key_2"
    assert body["reply"].strip()
