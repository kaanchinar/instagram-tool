import pytest

from tracker.config import Settings
from tracker.worker import cli

ENCODED_SID = "123456789%3Aabcdefghijklmnop%3A12%3AAYzzzzzzzzzzzzzzzzzzzz"
DECODED_SID = "123456789:abcdefghijklmnop:12:AYzzzzzzzzzzzzzzzzzzzz"


class FakeClient:
    def __init__(self, state):
        self.state = state
        self.delay_range = None
        self.calls = []
        self.dumped_to = None
        self.username = "me"

    def login_by_sessionid(self, sessionid):
        self.calls.append(sessionid)
        if self.state["failures_left"] > 0:
            self.state["failures_left"] -= 1
            raise RuntimeError("login_required")
        return True

    def dump_settings(self, path):
        self.dumped_to = path


def make_settings(tmp_path, sessionid=""):
    return Settings(
        ig_sessionid=sessionid,
        ig_session_path=tmp_path / "session.json",
        database_url="sqlite://",
        _env_file=None,
        _env_prefix="IGTRACKER_TEST_",
    )


@pytest.fixture()
def fake_client_factory(monkeypatch):
    created = []
    state = {"failures_left": 0}

    def factory(failures_before_success=0):
        state["failures_left"] = failures_before_success

        def build():
            client = FakeClient(state)
            created.append(client)
            return client

        monkeypatch.setattr(cli, "Client", build)
        return created

    return factory


def all_calls(created):
    return [sessionid for client in created for sessionid in client.calls]


def test_login_sessionid_from_prompt_saves_session(tmp_path, monkeypatch, fake_client_factory, capsys):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: f'  "{ENCODED_SID}"  ')
    created = fake_client_factory()

    cli.login_sessionid()

    assert all_calls(created) == [ENCODED_SID]
    assert created[0].dumped_to == settings.ig_session_path
    assert "Session saved" in capsys.readouterr().out


def test_login_sessionid_prefers_env_value_over_prompt(tmp_path, monkeypatch, fake_client_factory):
    settings = make_settings(tmp_path, sessionid=ENCODED_SID)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    def fail_prompt(prompt):
        raise AssertionError("prompt should not be used when IG_SESSIONID is set")

    monkeypatch.setattr(cli.getpass, "getpass", fail_prompt)
    created = fake_client_factory()

    cli.login_sessionid()

    assert all_calls(created) == [ENCODED_SID]


def test_login_sessionid_retries_decoded_form(tmp_path, monkeypatch, fake_client_factory):
    settings = make_settings(tmp_path, sessionid=ENCODED_SID)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    created = fake_client_factory(failures_before_success=1)

    cli.login_sessionid()

    assert all_calls(created) == [ENCODED_SID, DECODED_SID]
    assert created[-1].dumped_to == settings.ig_session_path


def test_login_sessionid_exits_after_both_forms_fail(tmp_path, monkeypatch, fake_client_factory, capsys):
    settings = make_settings(tmp_path, sessionid=ENCODED_SID)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    created = fake_client_factory(failures_before_success=2)

    with pytest.raises(SystemExit) as exc_info:
        cli.login_sessionid()

    assert exc_info.value.code == 1
    assert all_calls(created) == [ENCODED_SID, DECODED_SID]
    assert all(client.dumped_to is None for client in created)
    assert "rejected the sessionid" in capsys.readouterr().out


def test_login_sessionid_plain_value_not_duplicated(tmp_path, monkeypatch, fake_client_factory):
    settings = make_settings(tmp_path, sessionid=DECODED_SID)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    created = fake_client_factory()

    cli.login_sessionid()

    assert all_calls(created) == [DECODED_SID]
