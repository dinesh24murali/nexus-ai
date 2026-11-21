import pytest
from app.core.config import Settings
from pydantic import ValidationError

SECRET = "x" * 40


def make(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "auth_secret": SECRET,
        "app_secret": SECRET,
    }
    return Settings(**(base | overrides))  # type: ignore[arg-type]


def test_defaults_match_env_example() -> None:
    s = make()
    assert s.app_env == "development"
    assert s.registration_mode == "invite"
    assert s.llm_mode == "real"
    assert s.database_url.startswith("postgresql+psycopg://")


def test_production_refuses_fake_llm() -> None:
    with pytest.raises(ValidationError, match="LLM_MODE=fake"):
        make(app_env="production", llm_mode="fake")


def test_fake_llm_allowed_outside_production() -> None:
    assert make(app_env="test", llm_mode="fake").llm_mode == "fake"


@pytest.mark.parametrize("field", ["auth_secret", "app_secret"])
@pytest.mark.parametrize("bad", ["", "changeme", "short"])
def test_production_refuses_weak_secrets(field: str, bad: str) -> None:
    with pytest.raises(ValidationError, match=field.upper()):
        make(app_env="production", **{field: bad})


def test_production_accepts_strong_secrets() -> None:
    assert make(app_env="production").app_env == "production"


def test_unknown_env_vars_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOMETHING_ELSE_ENTIRELY", "1")
    make()


def test_invalid_registration_mode_rejected() -> None:
    with pytest.raises(ValidationError):
        make(registration_mode="anyone")


def test_secrets_not_in_repr() -> None:
    assert SECRET not in repr(make())
