"""Auth routes: register, login, logout, me."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.deps import get_current_user
from app.auth.ratelimit import rate_limit
from app.auth.schemas import LoginRequest, RegisterRequest, TokenResponse, UserPublic
from app.auth.service import AuthService, create_access_token
from app.db.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=201, response_model=TokenResponse)
async def register(body: RegisterRequest, _: None = Depends(rate_limit)) -> TokenResponse:
    user = await AuthService().register(body.email, body.password)
    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        user=UserPublic.from_user(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, _: None = Depends(rate_limit)) -> TokenResponse:
    user = await AuthService().authenticate(body.email, body.password)
    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        user=UserPublic.from_user(user),
    )


@router.post("/logout", status_code=204)
async def logout(_user: User = Depends(get_current_user)) -> None:
    # Stateless JWT: the client drops the token. A server denylist can be added later.
    return None


@router.get("/me", response_model=UserPublic)
async def me(user: User = Depends(get_current_user)) -> UserPublic:
    return UserPublic.from_user(user)
