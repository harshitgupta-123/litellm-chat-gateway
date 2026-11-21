def test_health_reports_key_pool_and_cache(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["keys_configured"] == 2
    assert body["cache_enabled"] is True


def test_root_is_json(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "endpoints" in response.json()
