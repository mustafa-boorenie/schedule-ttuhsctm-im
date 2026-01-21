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
from ..services.email import EmailService
from ..models import Admin

router = APIRouter(prefix="/api/admin", tags=["admin-auth"])


@router.post("/login")
async def request_magic_link(
    request: AdminLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Request a magic link for admin login.

    If the email is registered as an admin, a magic link will be sent.
    For security, we always return success even if the email is not found.
    """
    auth_service = AuthService(db)
    email_service = EmailService()

    admin = await auth_service.get_admin_by_email(request.email)

    if admin:
        magic_link = await auth_service.create_magic_link(admin)
        magic_link_url = auth_service.get_magic_link_url(magic_link.token)
        await email_service.send_magic_link(admin.email, magic_link_url)

    # Always return success to prevent email enumeration
    return {
        "message": "If your email is registered, you will receive a login link shortly.",
        "status": "ok"
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
        secure=False,  # Set to True in production with HTTPS
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
