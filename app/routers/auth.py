from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from app.models.user import AccessToken, Login, UserCreate, UserRead, UserUpdate
from app.security import issue_token, require_roles

router = APIRouter(prefix="/auth", tags=["Auth"])


def active_user(request: Request, claims=Depends(require_roles("WAREHOUSE", "SHIPPING"))):
    return request.app.state.auth_service.get_user(UUID(claims["sub"]))


def active_admin(user=Depends(active_user)):
    if user.role != "ADMIN":
        raise HTTPException(403, "Insufficient permissions")
    return user


@router.post("/login", response_model=AccessToken)
def login(body: Login, request: Request):
    user = request.app.state.auth_service.login(body)
    return AccessToken(access_token=issue_token(user, request.app.state.private_key,
                                               request.app.state.token_verifier, request.app.state.token_ttl))


@router.get("/me", response_model=UserRead)
def me(user=Depends(active_user)):
    return user


@router.post("/users", response_model=UserRead, status_code=201, dependencies=[Depends(active_admin)])
def create_user(body: UserCreate, request: Request):
    return request.app.state.auth_service.create_user(body)


@router.patch("/users/{user_id}", response_model=UserRead, dependencies=[Depends(active_admin)])
def update_user(user_id: UUID, body: UserUpdate, request: Request):
    return request.app.state.auth_service.update_user(user_id, body)
