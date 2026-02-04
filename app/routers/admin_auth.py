"""
Admin authentication routes (magic link login).
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, Cookie
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..schemas import AdminLoginRequest, MagicLinkVerifyResponse, AdminResponse
from ..services.auth import AuthService, get_current_admin
from ..models import Admin
from ..settings import settings

router = APIRouter(prefix="/api/admin", tags=["admin-auth"])


@router.post("/setup")
async def setup_first_admin(
    request: AdminLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Create the first admin user. Only works if no admins exist.
    This is a one-time setup endpoint for initial deployment.
    """
    from sqlalchemy import select, func

    # Check if any admins exist
    result = await db.execute(select(func.count(Admin.id)))
    admin_count = result.scalar()

    if admin_count > 0:
        raise HTTPException(
            status_code=403,
            detail="Setup already complete. Use /login to request a magic link."
        )

    # Create the first admin
    admin = Admin(
        email=request.email,
        name=request.email.split("@")[0],
        is_active=True,
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)

    return {
        "message": f"Admin user created for {request.email}. You can now use /login to request a magic link.",
        "admin_id": admin.id,
        "status": "ok"
    }


@router.post("/login")
async def login(
    request: AdminLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Admin login.

    Uses password auth only. Magic links are disabled.
    """
    auth_service = AuthService(db)
    admin = await auth_service.get_admin_by_email(request.email)

    if not admin:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not settings.admin_password:
        raise HTTPException(
            status_code=500,
            detail="ADMIN_PASSWORD not configured"
        )

    if request.password != settings.admin_password:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = auth_service.create_access_token(admin)
    return {
        "message": "Login successful",
        "token": token,
        "admin": {
            "id": admin.id,
            "email": admin.email,
            "name": admin.name
        }
    }


@router.get("/verify/{token}")
async def verify_magic_link(
    token: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    Verify a magic link token and return an access token.

    This endpoint can be used in two ways:
    1. API: Returns JSON with access_token
    2. Browser: Sets cookie and redirects to admin portal
    """
    auth_service = AuthService(db)
    admin = await auth_service.verify_magic_link(token)

    if not admin:
        raise HTTPException(status_code=401, detail="Invalid or expired magic link")

    access_token = auth_service.create_access_token(admin)

    # Set secure HTTP-only cookie
    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie(
        key="admin_token",
        value=access_token,
        httponly=True,
        secure=not settings.debug,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,  # 7 days
    )

    return response


@router.post("/verify/{token}")
async def verify_magic_link_api(
    token: str,
    db: AsyncSession = Depends(get_db),
) -> MagicLinkVerifyResponse:
    """
    Verify a magic link token and return an access token (API version).
    """
    auth_service = AuthService(db)
    admin = await auth_service.verify_magic_link(token)

    if not admin:
        raise HTTPException(status_code=401, detail="Invalid or expired magic link")

    access_token = auth_service.create_access_token(admin)

    return MagicLinkVerifyResponse(
        access_token=access_token,
        admin=AdminResponse.model_validate(admin),
    )


@router.post("/logout")
async def logout(response: Response):
    """Log out by clearing the admin token cookie."""
    response = Response(content='{"message": "Logged out"}', media_type="application/json")
    response.delete_cookie("admin_token")
    return response


@router.get("/me")
async def get_current_admin_info(
    admin_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db),
) -> AdminResponse:
    """Get information about the currently logged-in admin."""
    if not admin_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    admin = await get_current_admin(admin_token, db)
    if not admin:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return AdminResponse.model_validate(admin)


# Dependency for protected routes
async def require_admin(
    admin_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db),
) -> Admin:
    """Dependency that requires a valid admin session."""
    if not admin_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    admin = await get_current_admin(admin_token, db)
    if not admin:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return admin
