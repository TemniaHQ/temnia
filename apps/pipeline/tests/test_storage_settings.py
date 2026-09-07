"""What the object store's settings refuse to guess.

`StorageSettings.from_env` falls back to the dev-only Garage key, which is the
right behaviour on a laptop and the wrong one inside a Modal container, where
the values come from a secret that can be missing a key.
"""

from __future__ import annotations

import pytest

from temnia_pipeline.settings import REQUIRED_STORAGE_VARIABLES, StorageSettings

SOURCE = "the Modal Secret 'temnia-r2'"
COMPLETE = {
    "STORAGE_ENDPOINT": "https://account.r2.cloudflarestorage.com",
    "STORAGE_REGION": "auto",
    "STORAGE_BUCKET": "temnia-staging-media",
    "STORAGE_ACCESS_KEY_ID": "key-id",
    "STORAGE_SECRET_ACCESS_KEY": "key-secret",
}


@pytest.fixture
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]  # a pytest fixture is called by name, not by reference
    for name, value in COMPLETE.items():
        monkeypatch.setenv(name, value)


@pytest.mark.usefixtures("_configured")
def test_a_complete_secret_is_read_as_it_is() -> None:
    settings = StorageSettings.require_env(SOURCE)
    assert settings.endpoint == COMPLETE["STORAGE_ENDPOINT"]
    assert settings.region == "auto"
    assert settings.bucket == COMPLETE["STORAGE_BUCKET"]
    assert settings.access_key_id == COMPLETE["STORAGE_ACCESS_KEY_ID"]
    assert settings.secret_access_key == COMPLETE["STORAGE_SECRET_ACCESS_KEY"]


@pytest.mark.usefixtures("_configured")
def test_the_region_may_default_because_it_is_not_a_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STORAGE_REGION")
    assert StorageSettings.require_env(SOURCE).region == "garage"


@pytest.mark.usefixtures("_configured")
@pytest.mark.parametrize("missing", REQUIRED_STORAGE_VARIABLES)
def test_a_missing_variable_is_named_along_with_the_secret_that_sets_it(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """The alternative is a container that quietly dials its own localhost."""
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError) as caught:
        StorageSettings.require_env(SOURCE)

    message = str(caught.value)
    assert missing in message
    assert "temnia-r2" in message
    assert "is missing or empty" in message


@pytest.mark.usefixtures("_configured")
def test_an_empty_value_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A secret key set to the empty string is a misconfiguration, not a choice."""
    monkeypatch.setenv("STORAGE_ACCESS_KEY_ID", "")
    with pytest.raises(RuntimeError, match="STORAGE_ACCESS_KEY_ID"):
        StorageSettings.require_env(SOURCE)


def test_every_missing_variable_is_listed_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """One deploy, one fix: a reader should not learn the keys one restart at a time."""
    for name in REQUIRED_STORAGE_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError) as caught:
        StorageSettings.require_env(SOURCE)

    message = str(caught.value)
    assert all(name in message for name in REQUIRED_STORAGE_VARIABLES)
    assert "are missing or empty" in message
