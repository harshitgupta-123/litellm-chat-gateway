"""Unit tests for the pieces that do not need the HTTP layer."""

from app.config import Settings, load_settings, sqlite_path_from_url
from app.llm import is_fallback_error, parse_simulation_header


class _Err(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        if status_code is not None:
            self.status_code = status_code
        super().__init__(message)


def test_parse_simulation_header():
    assert parse_simulation_header("key_1=429") == {"key_1": 429}
    assert parse_simulation_header("key_1=429,key_2=500") == {"key_1": 429, "key_2": 500}
    assert parse_simulation_header("*=429") == {"*": 429}
    assert parse_simulation_header(" KEY_1 = 503 ") == {"key_1": 503}


def test_parse_simulation_header_ignores_rubbish():
    assert parse_simulation_header(None) == {}
    assert parse_simulation_header("") == {}
    assert parse_simulation_header("nonsense") == {}
    assert parse_simulation_header("key_1=418") == {}  # unsupported status
    assert parse_simulation_header("key_1=abc") == {}


def test_fallback_classification():
    assert is_fallback_error(_Err("boom", 429)) is True
    assert is_fallback_error(_Err("boom", 500)) is True
    assert is_fallback_error(_Err("boom", 503)) is True
    assert is_fallback_error(_Err("invalid api key", 401)) is True
    assert is_fallback_error(_Err("You exceeded your current quota")) is True
    assert is_fallback_error(_Err("resource_exhausted")) is True
    assert is_fallback_error(_Err("unsupported parameter", 400)) is False


def test_database_url_parsing():
    assert sqlite_path_from_url("sqlite:////data/app.db") == "/data/app.db"
    assert sqlite_path_from_url("/data/app.db") == "/data/app.db"
    assert sqlite_path_from_url("sqlite:///./app.db").endswith("app.db")


def test_key_pool_ids_follow_env_order():
    settings = load_settings({"LLM_API_KEYS": "a,b,c", "LLM_MODELS": "m"})
    assert [p.key_id for p in settings.providers] == ["key_1", "key_2", "key_3"]
    assert {p.model for p in settings.providers} == {"m"}


def test_keys_can_be_paired_with_different_models():
    settings = load_settings({"LLM_API_KEYS": "a,b", "LLM_MODELS": "m1,m2"})
    assert [(p.key_id, p.model) for p in settings.providers] == [
        ("key_1", "m1"),
        ("key_2", "m2"),
    ]


def test_cache_key_depends_on_model_and_messages():
    from app.llm import LLMGateway

    gateway = LLMGateway(
        Settings(api_keys=["a"], models=["m"], cache_type="memory"),
        completion_fn=lambda **kwargs: None,
    )
    messages = [{"role": "user", "content": "hi"}]
    same = gateway.cache_key("m", messages)
    assert same == gateway.cache_key("m", [{"role": "user", "content": "hi"}])
    assert same != gateway.cache_key("m2", messages)
    assert same != gateway.cache_key("m", [{"role": "user", "content": "hello"}])


def test_keys_are_masked_for_logging():
    provider = load_settings({"LLM_API_KEYS": "sk-super-secret-value"}).providers[0]
    assert "super" not in provider.masked_key()
