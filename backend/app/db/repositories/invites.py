import uuid
from datetime import datetime

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Invite


async def create(
    session: AsyncSession, *, code_hash: str, expires_at: datetime, note: str | None
) -> Invite:
    invite = Invite(code_hash=code_hash, expires_at=expires_at, note=note)
    session.add(invite)
    await session.flush()
    return invite


async def lock_valid(session: AsyncSession, code_hash: str, now: datetime) -> Invite | None:
    """The usable invite for this code, row-locked until the transaction ends.

    Unknown, used, revoked and expired codes are all simply "no row", so callers cannot tell them
    apart. FOR UPDATE makes concurrent registrations with one code serialise: the loser re-checks
    `used_at IS NULL` after the winner commits and finds nothing.
    """
    stmt = (
        select(Invite)
        .where(
            Invite.code_hash == code_hash,
            Invite.used_at.is_(None),
            Invite.revoked_at.is_(None),
            Invite.expires_at > now,
        )
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def mark_used(
    session: AsyncSession, invite: Invite, user_id: uuid.UUID, now: datetime
) -> None:
    invite.used_at = now
    invite.used_by = user_id
    await session.flush()


async def list_all(session: AsyncSession) -> list[Invite]:
    return list(
        (await session.execute(select(Invite).order_by(Invite.created_at.desc()))).scalars()
    )


async def revoke(
    session: AsyncSession,
    now: datetime,
    *,
    invite_id: uuid.UUID | None = None,
    code_hash: str | None = None,
) -> bool:
    """Revoke an unused invite, identified by id or by the hash of its code."""
    if (invite_id is None) == (code_hash is None):
        raise ValueError("pass exactly one of invite_id or code_hash")
    match = Invite.id == invite_id if invite_id is not None else Invite.code_hash == code_hash
    result = await session.execute(
        update(Invite)
        .where(match, Invite.revoked_at.is_(None), Invite.used_at.is_(None))
        .values(revoked_at=now)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def delete_dead(session: AsyncSession, cutoff: datetime) -> int:
    """Remove UNUSED invites that expired or were revoked before `cutoff`. Used invites stay: they
    record who joined with which code."""
    result = await session.execute(
        delete(Invite).where(
            Invite.used_at.is_(None),
            or_(Invite.expires_at < cutoff, Invite.revoked_at < cutoff),
        )
    )
    return int(result.rowcount)  # type: ignore[attr-defined]
