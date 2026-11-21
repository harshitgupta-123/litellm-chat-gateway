def post(client, session_id, message):
    return client.post("/chat", json={"session_id": session_id, "message": message})


def test_history_is_returned_oldest_first_without_the_system_prompt(client):
    post(client, "h-1", "first question")
    post(client, "h-1", "second question")

    body = client.get("/sessions/h-1/messages").json()
    assert body["session_id"] == "h-1"
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert body["messages"][0]["content"] == "first question"
    assert "system" not in roles


def test_assistant_messages_carry_metadata(client):
    post(client, "h-2", "hello there")
    message = client.get("/sessions/h-2/messages").json()["messages"][1]
    meta = message["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["input_tokens"] > 0
    assert meta["output_tokens"] > 0


def test_user_messages_have_no_metadata(client):
    post(client, "h-3", "hello there")
    message = client.get("/sessions/h-3/messages").json()["messages"][0]
    assert message.get("metadata") is None


def test_unknown_session_returns_404(client):
    response = client.get("/sessions/does-not-exist/messages")
    assert response.status_code == 404
    assert response.json() == {"detail": "session not found"}


def test_history_survives_a_new_database_connection(client):
    post(client, "h-4", "persist me")
    from app import main

    rows = main.db.get_messages("h-4")
    assert len(rows) == 2
    assert rows[0]["created_at"].endswith("Z")
