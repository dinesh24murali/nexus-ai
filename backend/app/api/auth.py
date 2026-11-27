from typing import Annotated

from fastapi import APIRouter, Cookie, Response

from app.api.deps import ClientIpDep, CurrentUserDep, FactoryDep, SettingsDep
from app.api.schemas import AuthResponse, LoginRequest, RegisterRequest, UserOut
from app.core.errors import ApiError
from app.core.security import ACCESS_TOKEN_TTL_S, REFRESH_TOKEN_TTL_S
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "nexus_refresh"
_COOKIE_ATTRS = {"path": "/api/v1/auth", "httponly": True, "secure": True, "samesite": "strict"}


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(COOKIE_NAME, token, max_age=REFRESH_TOKEN_TTL_S, **_COOKIE_ATTRS)  # type: ignore[arg-type]


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, **_COOKIE_ATTRS)  # type: ignore[arg-type]


def _auth_response(result: auth_service.AuthResult) -> AuthResponse:
    return AuthResponse(
        access_token=result.access_token,
        expires_in=ACCESS_TOKEN_TTL_S,
        user=UserOut.model_validate(result.user),
    )


@router.post(
    "/register",
    status_code=201,
    response_model=AuthResponse,
    summary="Create an account and sign in",
)
async def register(
    body: RegisterRequest,
    response: Response,
    settings: SettingsDep,
    factory: FactoryDep,
    ip: ClientIpDep,
) -> AuthResponse:
    result = await auth_service.register(
        factory,
        settings,
        ip=ip,
        email=body.email,
        password=body.password,
        display_name=body.display_name,
        invite_code=body.invite_code,
    )
    _set_refresh_cookie(response, result.refresh_token)
    return _auth_response(result)


@router.post("/login", response_model=AuthResponse, summary="Sign in with email and password")
async def login(
    body: LoginRequest,
    response: Response,
    settings: SettingsDep,
    factory: FactoryDep,
    ip: ClientIpDep,
) -> AuthResponse:
    result = await auth_service.login(
        factory, settings, ip=ip, email=body.email, password=body.password
    )
    _set_refresh_cookie(response, result.refresh_token)
    return _auth_response(result)


@router.post(
    "/refresh",
    response_model=AuthResponse,
    summary="Rotate the refresh cookie and get a new access token",
)
async def refresh(
    response: Response,
    settings: SettingsDep,
    factory: FactoryDep,
    nexus_refresh: Annotated[str | None, Cookie()] = None,
) -> AuthResponse:
    try:
        result = await auth_service.refresh(factory, settings, token=nexus_refresh)
    except ApiError as exc:
        # A rejected refresh token is dead: tell the browser to drop it.
        cleared = Response()
        _clear_refresh_cookie(cleared)
        exc.headers = {**(exc.headers or {}), "set-cookie": cleared.headers["set-cookie"]}
        raise
    _set_refresh_cookie(response, result.refresh_token)
    return _auth_response(result)


@router.post("/logout", status_code=204, summary="Revoke the current refresh token")
async def logout(
    current: CurrentUserDep,
    factory: FactoryDep,
    nexus_refresh: Annotated[str | None, Cookie()] = None,
) -> Response:
    await auth_service.logout(factory, user_id=current.id, token=nexus_refresh)
    response = Response(status_code=204)
    _clear_refresh_cookie(response)
    return response
