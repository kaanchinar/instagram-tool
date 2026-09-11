class SnapshotError(Exception):
    pass


class RateLimited(SnapshotError):
    pass


class ChallengeRequired(SnapshotError):
    pass


class LoginFailed(SnapshotError):
    pass


class FetchFailed(SnapshotError):
    pass
