import argparse
import getpass
import logging

from instagrapi import Client
from instagrapi.exceptions import ChallengeRequired, TwoFactorRequired

from tracker.config import get_settings
from tracker.db import init_db, session_scope
from tracker.notifier.telegram import send_events
from tracker.snapshotter.client import InstagramClient
from tracker.snapshotter.service import fetch_with_client, run_snapshot


def login() -> None:
    settings = get_settings()
    username = settings.ig_username or input("Instagram username: ").strip()
    password = settings.ig_password or getpass.getpass("Instagram password: ")
    client = Client()
    client.delay_range = [2, 6]

    def challenge_code_handler(user, choice=None):
        return input(f"Challenge code for {user} ({choice}): ").strip()

    client.challenge_code_handler = challenge_code_handler
    try:
        client.login(username, password)
    except TwoFactorRequired:
        code = input("Two-factor code: ").strip()
        client.login(username, password, verification_code=code)
    except ChallengeRequired:
        print("Manual verification required; complete it in the Instagram app and retry.")
        raise
    session_path = settings.ig_session_path
    session_path.parent.mkdir(parents=True, exist_ok=True)
    client.dump_settings(session_path)
    print(f"Session saved to {session_path}")


def snapshot() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    init_db()
    client = InstagramClient(settings)
    with session_scope() as session:
        result = run_snapshot(
            session,
            lambda: fetch_with_client(client),
            lambda events: send_events(session, events),
        )
        print(
            f"Snapshot #{result.id} {result.status.value}: "
            f"followers={result.follower_count} following={result.following_count}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="tracker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("login", help="Interactively create an Instagram session file")
    subparsers.add_parser("snapshot", help="Run a single snapshot now")
    args = parser.parse_args()
    if args.command == "login":
        login()
    else:
        snapshot()


if __name__ == "__main__":
    main()
