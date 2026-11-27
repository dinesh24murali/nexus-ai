"""Admin CLI: `python -m app.cli <command>` (on the VM: `docker compose exec api python -m ...`).

Admin actions are CLI-only by design (no admin API surface to attack): invites, password resets,
disabling users, kill switches.
"""

import argparse
import asyncio
import getpass
import re
import sys
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.runtime_flags import KNOWN_FLAGS, list_flags, set_flag
from app.core.security import generate_invite_code, hash_password, hash_token
from app.db.repositories import invites, refresh_tokens, users
from app.db.session import create_engine, create_session_factory

Factory = async_sessionmaker[AsyncSession]
Command = Callable[[Factory, argparse.Namespace], Awaitable[int]]

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128
_DURATION = re.compile(r"^(\d+)([mhd])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


def duration(text: str) -> timedelta:
    match = _DURATION.match(text)
    if not match or int(match.group(1)) < 1:
        raise argparse.ArgumentTypeError(f"invalid duration {text!r}; use e.g. 30m, 12h, 7d")
    return timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})


def invite_ref(text: str) -> uuid.UUID | str:
    """An invite code (nx_...) or the id shown by `invite list`."""
    if text.startswith("nx_"):
        return text
    try:
        return uuid.UUID(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is neither an invite code (nx_...) nor an invite id"
        ) from None


def _now() -> datetime:
    return datetime.now(UTC)


# ───────────────────────────── invites ─────────────────────────────


async def invite_create(factory: Factory, args: argparse.Namespace) -> int:
    code = generate_invite_code()
    expires_at = _now() + args.expires
    async with factory() as session, session.begin():
        await invites.create(
            session, code_hash=hash_token(code), expires_at=expires_at, note=args.note
        )
    print(f"Invite created (expires {expires_at:%Y-%m-%d %H:%M} UTC)")
    print(f"  code:  {code}")
    print(f"  link:  /register?invite={code}   (prefix with your site address)")
    print("The code is shown once; only its hash is stored.")
    return 0


async def invite_list(factory: Factory, args: argparse.Namespace) -> int:
    now = _now()
    async with factory() as session:
        rows = await invites.list_all(session)
    print(f"{'ID':36}  {'STATUS':8}  {'EXPIRES (UTC)':16}  NOTE")
    for inv in rows:
        if inv.used_at:
            status = "used"
        elif inv.revoked_at:
            status = "revoked"
        elif inv.expires_at <= now:
            status = "expired"
        else:
            status = "active"
        print(f"{inv.id!s:36}  {status:8}  {inv.expires_at:%Y-%m-%d %H:%M}  {inv.note or ''}")
    return 0


async def invite_revoke(factory: Factory, args: argparse.Namespace) -> int:
    target = args.invite
    async with factory() as session, session.begin():
        if isinstance(target, uuid.UUID):
            revoked = await invites.revoke(session, _now(), invite_id=target)
        else:
            revoked = await invites.revoke(session, _now(), code_hash=hash_token(target))
    if not revoked:
        print("Invite not found, or already used or revoked.")
        return 1
    print("Invite revoked.")
    return 0


# ───────────────────────────── users ─────────────────────────────


def _read_new_password(from_stdin: bool) -> str | None:
    if from_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("New password: ")
        if getpass.getpass("Repeat: ") != password:
            print("Passwords do not match.")
            return None
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        print(f"Password must be {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} characters.")
        return None
    return password


async def reset_password(factory: Factory, args: argparse.Namespace) -> int:
    async with factory() as session:
        user = await users.get_by_email(session, args.email)
    if user is None:
        print("User not found.")
        return 1
    password = _read_new_password(args.password_stdin)
    if password is None:
        return 2
    new_hash = hash_password(password)
    async with factory() as session, session.begin():
        await users.set_password_hash(session, user.id, new_hash)
        await refresh_tokens.revoke_all_for_user(session, user.id, _now())
    print(f"Password reset for {user.email}; all sessions signed out.")
    return 0


async def _set_user_status(factory: Factory, email: str, *, enable: bool) -> int:
    async with factory() as session, session.begin():
        user = await users.get_by_email(session, email)
        if user is None:
            print("User not found.")
            return 1
        if enable and user.status == "pending_deletion":
            print("Account is pending deletion and cannot be re-enabled.")
            return 1
        await users.set_status(session, user.id, "active" if enable else "disabled")
        if not enable:
            await refresh_tokens.revoke_all_for_user(session, user.id, _now())
    print(f"{user.email} {'enabled' if enable else 'disabled; all sessions signed out'}.")
    return 0


async def disable_user(factory: Factory, args: argparse.Namespace) -> int:
    return await _set_user_status(factory, args.email, enable=False)


async def enable_user(factory: Factory, args: argparse.Namespace) -> int:
    return await _set_user_status(factory, args.email, enable=True)


# ───────────────────────────── flags ─────────────────────────────


async def flags_set(factory: Factory, args: argparse.Namespace) -> int:
    pairs: list[tuple[str, str]] = []
    for item in args.assignments:
        name, sep, value = item.partition("=")
        if not sep:
            print(f"Expected NAME=VALUE, got {item!r}.")
            return 2
        pairs.append((name.strip(), value))
    who = getpass.getuser()
    from app.core.runtime_flags import validate

    try:
        for name, value in pairs:  # validate everything before writing anything
            validate(name, value)
    except ValueError as exc:
        print(exc)
        return 2
    for name, value in pairs:
        stored = await set_flag(factory, name, value, updated_by=who)
        print(f"{name} = {stored!r}  (takes effect within 60 s, no restart)")
    return 0


async def flags_list(factory: Factory, args: argparse.Namespace) -> int:
    defaults: dict[str, str] = args.flag_defaults  # boot defaults, attached by main()
    rows = await list_flags(factory)
    print(f"{'FLAG':24}  {'VALUE':20}  SOURCE")
    for name in KNOWN_FLAGS:
        if name in rows:
            value, updated_at, by = rows[name]
            print(f"{name:24}  {value!r:20}  db ({by or '?'}, {updated_at:%Y-%m-%d %H:%M} UTC)")
        else:
            print(f"{name:24}  {defaults.get(name, '')!r:20}  boot default")
    return 0


# ───────────────────────────── entry point ─────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Nexus AI admin CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    invite = sub.add_parser("invite", help="manage invite codes").add_subparsers(
        dest="action", required=True
    )
    create = invite.add_parser("create", help="create an invite code")
    create.add_argument("--expires", type=duration, default=timedelta(days=7), help="e.g. 12h, 7d")
    create.add_argument("--note", help="who it is for (shown in `invite list`)")
    create.set_defaults(run=invite_create)
    invite.add_parser("list", help="list invites").set_defaults(run=invite_list)
    revoke = invite.add_parser("revoke", help="revoke an unused invite, by code or by id")
    revoke.add_argument("invite", type=invite_ref, metavar="CODE_OR_ID")
    revoke.set_defaults(run=invite_revoke)

    reset = sub.add_parser("reset-password", help="set a new password and sign the user out")
    reset.add_argument("email")
    reset.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    reset.set_defaults(run=reset_password)

    for name, fn, text in (
        ("disable-user", disable_user, "block login and revoke all sessions"),
        ("enable-user", enable_user, "re-enable a disabled user"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("email")
        p.set_defaults(run=fn)

    flags = sub.add_parser("flags", help="runtime kill switches").add_subparsers(
        dest="action", required=True
    )
    fset = flags.add_parser("set", help="set one or more NAME=VALUE flags")
    fset.add_argument("assignments", nargs="+", metavar="NAME=VALUE")
    fset.set_defaults(run=flags_set)
    flags.add_parser("list", help="show every flag and where its value comes from").set_defaults(
        run=flags_list
    )
    return parser


def main(argv: list[str] | None = None, *, settings: Settings | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse exits on bad input; report it as a return code
        return int(exc.code) if isinstance(exc.code, int) else 2

    settings = settings or get_settings()
    args.flag_defaults = settings.flag_defaults()

    async def run() -> int:
        engine = create_engine(settings.database_url)
        try:
            factory = create_session_factory(engine)
            code: int = await args.run(factory, args)
            return code
        finally:
            await engine.dispose()

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
