from fastapi import APIRouter, Response

from app.api.deps import CurrentUserDep, FactoryDep
from app.api.schemas import ChangePasswordRequest, UpdateMeRequest, UserOut
from app.core.errors import ApiError
from app.db.repositories import users
from app.services import auth as auth_service

router = APIRouter(prefix="/me", tags=["account"])


@router.get("", response_model=UserOut, summary="Get the current user")
async def get_me(current: CurrentUserDep) -> UserOut:
    return UserOut.model_validate(current.user)


@router.patch("", response_model=UserOut, summary="Update profile and settings")
async def update_me(body: UpdateMeRequest, current: CurrentUserDep, factory: FactoryDep) -> UserOut:
    async with factory() as session, session.begin():
        updated = await users.update_profile(
            session,
            current.id,
            display_name=body.display_name,
            memory_enabled=body.memory_enabled,
        )
    if updated is None:  # deleted between authentication and update
        raise ApiError(401, "UNAUTHORIZED", "Authentication required.")
    return UserOut.model_validate(updated)


@router.post("/password", status_code=204, summary="Change password (revokes other sessions)")
async def change_password(
    body: ChangePasswordRequest, current: CurrentUserDep, factory: FactoryDep
) -> Response:
    await auth_service.change_password(
        factory,
        user=current.user,
        family_id=current.family_id,
        current_password=body.current_password,
        new_password=body.new_password,
    )
    return Response(status_code=204)
