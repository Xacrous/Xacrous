import keyring.errors
import pytest

from chartpilot.settings.config_store import ConfigStore, Preferences


class _FakeKeyring:
    errors = keyring.errors

    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def get_password(self, service, username):
        return self._store.get((service, username))

    def delete_password(self, service, username):
        if (service, username) not in self._store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self._store[(service, username)]


@pytest.fixture
def store(tmp_path, monkeypatch):
    fake = _FakeKeyring()
    monkeypatch.setattr("chartpilot.settings.config_store.keyring", fake, raising=False)
    return ConfigStore(tmp_path / "config")


def test_load_returns_defaults_when_no_file(store):
    prefs = store.load()
    assert prefs == Preferences()


def test_save_then_load_round_trips(store):
    prefs = store.load()
    prefs.symbol = "ETHUSDT"
    prefs.timeframe = "1d"
    prefs.disclaimer_acknowledged = True
    store.save(prefs)

    reloaded = store.load()
    assert reloaded.symbol == "ETHUSDT"
    assert reloaded.timeframe == "1d"
    assert reloaded.disclaimer_acknowledged is True


def test_load_ignores_unknown_keys_and_corrupt_file(store):
    store.config_path.write_text('{"symbol": "SOLUSDT", "bogus_key": 123}')
    prefs = store.load()
    assert prefs.symbol == "SOLUSDT"

    store.config_path.write_text("not json{{{")
    assert store.load() == Preferences()


def test_api_key_round_trip(store):
    assert store.get_api_key() is None
    store.set_api_key("key123", "secret456")
    assert store.get_api_key() == ("key123", "secret456")


def test_clear_api_key_removes_saved_credentials(store):
    store.set_api_key("key123", "secret456")
    store.clear_api_key()
    assert store.get_api_key() is None


def test_clear_api_key_is_a_noop_when_nothing_saved(store):
    store.clear_api_key()  # should not raise
    assert store.get_api_key() is None
