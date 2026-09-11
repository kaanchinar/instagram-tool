from instagrapi import Client
from instagrapi.exceptions import (
    BadCredentials,
    BadPassword,
    ChallengeRequired as InstagramChallengeRequired,
    ClientThrottledError,
    LoginRequired,
    PleaseWaitFewMinutes,
    RateLimitError,
    TwoFactorRequired,
)

from tracker.config import Settings, get_settings
from tracker.core.diff import UserRecord
from tracker.snapshotter.errors import (
    ChallengeRequired,
    FetchFailed,
    LoginFailed,
    RateLimited,
)

RATE_LIMIT_ERRORS = (PleaseWaitFewMinutes, RateLimitError, ClientThrottledError)


def _wrap_error(exc: Exception) -> Exception:
    if isinstance(exc, RATE_LIMIT_ERRORS):
        return RateLimited(str(exc))
    if isinstance(exc, (InstagramChallengeRequired, TwoFactorRequired)):
        return ChallengeRequired(str(exc))
    if isinstance(exc, (BadPassword, BadCredentials, LoginRequired)):
        return LoginFailed(str(exc))
    return FetchFailed(str(exc))


def _to_records(users: dict) -> dict[int, UserRecord]:
    records: dict[int, UserRecord] = {}
    for key, user in users.items():
        user_id = int(getattr(user, "pk", key))
        records[user_id] = UserRecord(
            ig_user_id=user_id,
            username=getattr(user, "username", ""),
            full_name=getattr(user, "full_name", None),
        )
    return records


class InstagramClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client: Client | None = None
        self._own_user_id: int | None = None

    def connect(self) -> None:
        if self._client is not None:
            return
        client = Client()
        client.delay_range = [2, 6]
        session_path = self.settings.ig_session_path
        if session_path.exists():
            try:
                client.load_settings(session_path)
                client.account_info()
                self._client = client
                return
            except Exception:
                client = Client()
                client.delay_range = [2, 6]
        try:
            client.login(self.settings.ig_username, self.settings.ig_password)
            client.account_info()
        except Exception as exc:
            raise _wrap_error(exc) from exc
        session_path.parent.mkdir(parents=True, exist_ok=True)
        client.dump_settings(session_path)
        self._client = client

    def _own_id(self) -> int:
        self.connect()
        if self._own_user_id is None:
            try:
                info = self._client.user_info_by_username(self.settings.ig_username)
            except Exception as exc:
                raise _wrap_error(exc) from exc
            self._own_user_id = int(info.pk)
        return self._own_user_id

    def _fetch(self, method: str) -> dict[int, UserRecord]:
        self.connect()
        try:
            users = getattr(self._client, method)(self._own_id(), use_cache=False)
        except Exception as exc:
            raise _wrap_error(exc) from exc
        return _to_records(users)

    def fetch_followers(self) -> dict[int, UserRecord]:
        return self._fetch("user_followers")

    def fetch_following(self) -> dict[int, UserRecord]:
        return self._fetch("user_following")
