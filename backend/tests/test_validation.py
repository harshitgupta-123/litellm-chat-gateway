import pytest


@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "s-1"},                      # message missing
        {"message": "hello"},                       # session_id missing
        {"session_id": "s-1", "message": ""},       # empty message
        {"session_id": "s-1", "message": "   "},    # whitespace only
        {"session_id": "", "message": "hello"},     # empty session id
        {"session_id": "s" * 129, "message": "x"},  # session id too long
        {"session_id": "s-1", "message": "x" * 4001},  # message too long
        {"session_id": 42, "message": "hello"},     # wrong type
    ],
)
def test_invalid_payloads_return_422(client, payload):
    response = client.post("/chat", json=payload)
    assert response.status_code == 422
    assert "detail" in response.json()


def test_malformed_json_returns_422(client):
    response = client.post(
        "/chat",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert "detail" in response.json()


def test_failed_validation_is_not_stored(client):
    client.post("/chat", json={"session_id": "s-422", "message": ""})
    history = client.get("/sessions/s-422/messages")
    assert history.status_code == 404


def test_message_at_the_limit_is_accepted(client):
    response = client.post(
        "/chat", json={"session_id": "s-limit", "message": "x" * 4000}
    )
    assert response.status_code == 200
