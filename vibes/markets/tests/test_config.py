import pytest
from pydantic import ValidationError

from governor.config import BudgetPolicy, Settings, atomic


@pytest.mark.parametrize("value", ["0.01", "-1", "1e3", " 1", "01", True, 1.0, str(2**63)])
def test_money_rejects_ambiguous_values(value):
    with pytest.raises(ValueError):
        atomic(value)


def test_atomic_boundary():
    assert atomic("0") == 0
    assert atomic(str(2**63 - 1)) == 2**63 - 1
    with pytest.raises(ValidationError):
        BudgetPolicy(session_cap=True)


def test_secrets_are_not_in_settings_repr():
    settings = Settings.from_env({"GEMINI_API_KEY": "test-only-secret"})
    assert "test-only-secret" not in repr(settings)


def test_config_does_not_allow_live_payments():
    with pytest.raises(ValidationError):
        Settings.from_env({"GOVERNOR_PAYMENT_MODE": "mainnet"})


def test_credentials_checked_only_for_live_model():
    settings = Settings.from_env({})
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        settings.require_credentials()
    Settings.from_env(
        {"GEMINI_BACKEND": "vertex", "GOOGLE_CLOUD_PROJECT": "test"}
    ).require_credentials()
