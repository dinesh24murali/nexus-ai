from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.net import client_ip
from app.core.security import InvalidTokenError, TokenExpiredError, decode_access_token
from app.db.models import User
from app.db.repositories import users
from app.services.runs import RunService

_bearer = HTTPBearer(auto_error=False, description="15-minute access token from /auth/login")


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


def get_runs(request: Request) -> RunService:
    runs: RunService = request.app.state.runs
    return runs


def get_client_ip(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> str:
    return client_ip(request, trusted_proxy=settings.trusted_proxy)


SettingsDep = Annotated[Settings, Depends(get_settings)]
FactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
ClientIpDep = Annotated[str, Depends(get_client_ip)]
RunsDep = Annotated[RunService, Depends(get_runs)]


@dataclass(frozen=True)
class CurrentUser:
    user: User
    family_id: UUID  # the refresh-token family (session) behind this access token

    @property
    def id(self) -> UUID:
        return self.user.id


def _unauthorized(
    code: str = "UNAUTHORIZED", message: str = "Authentication required."
) -> ApiError:
    return ApiError(401, code, message, headers={"WWW-Authenticate": "Bearer"})


async def current_user(
    settings: SettingsDep,
    factory: FactoryDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    """Authenticate the request. Owner scoping starts here: use `current.id` in every query."""
    if credentials is None:
        raise _unauthorized()
    try:
        claims = decode_access_token(credentials.credentials, settings)
    except TokenExpiredError:
        raise _unauthorized("TOKEN_EXPIRED", "Access token expired.") from None
    except InvalidTokenError:
        raise _unauthorized() from None

    async with factory() as session:
        user = await users.get_by_id(session, claims.user_id)
    if user is None or user.status != "active":
        raise _unauthorized()
    return CurrentUser(user=user, family_id=claims.family_id)


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]
