"""
Authentication service for admin magic link authentication.
"""
from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from jose import jwt, JWTError
from itsdangerous import URLSafeTimedSerializer

from ..models import Admin, MagicLink
from ..settings import settings


class AuthService:
    """Service for handling admin authentication via magic links."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.serializer = URLSafeTimedSerializer(settings.secret_key)

    async def get_admin_by_email(self, email: str) -> Optional[Admin]:
        """Get admin by email address."""
        result = await self.db.execute(
            select(Admin).where(Admin.email == email.lower(), Admin.is_active == True)
        )
        return result.scalar_one_or_none()

    async def get_admin_by_id(self, admin_id: int) -> Optional[Admin]:
        """Get admin by ID."""
        result = await self.db.execute(
            select(Admin).where(Admin.id == admin_id, Admin.is_active == True)
        )
        return result.scalar_one_or_none()

    async def create_admin(self, email: str, name: Optional[str] = None) -> Admin:
        """Create a new admin user."""
        admin = Admin(
            email=email.lower(),
            name=name,
            is_active=True,
        )
        self.db.add(admin)
        await self.db.flush()
        return admin

    async def create_magic_link(self, admin: Admin) -> MagicLink:
        """Create a magic link for admin authentication."""
        # Invalidate any existing unused magic links for this admin
        result = await self.db.execute(
            select(MagicLink).where(
                MagicLink.admin_id == admin.id,
                MagicLink.used_at == None
            )
        )
        for old_link in result.scalars():
            old_link.used_at = datetime.utcnow()  # Mark as used/invalidated

        # Create new magic link
        magic_link = MagicLink(
            admin_id=admin.id,
            token=str(uuid4()),
            expires_at=datetime.utcnow() + timedelta(minutes=settings.magic_link_expire_minutes),
        )
        self.db.add(magic_link)
        await self.db.flush()
        return magic_link

    async def verify_magic_link(self, token: str) -> Optional[Admin]:
        """Verify a magic link token and return the admin if valid."""
        result = await self.db.execute(
            select(MagicLink).where(
                MagicLink.token == token,
                MagicLink.used_at == None,
                MagicLink.expires_at > datetime.utcnow()
            )
        )
        magic_link = result.scalar_one_or_none()

        if not magic_link:
            return None

        # Mark magic link as used
        magic_link.used_at = datetime.utcnow()

        # Get and update admin
        admin = await self.get_admin_by_id(magic_link.admin_id)
        if admin:
            admin.last_login = datetime.utcnow()

        return admin

    def create_access_token(self, admin: Admin) -> str:
        """Create a JWT access token for an authenticated admin."""
        expire = datetime.utcnow() + timedelta(days=settings.session_expire_days)
        payload = {
            "sub": str(admin.id),
            "email": admin.email,
            "exp": expire,
            "iat": datetime.utcnow(),
        }
        return jwt.encode(payload, settings.secret_key, algorithm="HS256")

    def verify_access_token(self, token: str) -> Optional[dict]:
        """Verify a JWT access token and return the payload if valid."""
        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
            return payload
        except JWTError:
            return None

    def get_magic_link_url(self, token: str) -> str:
        """Generate the full magic link URL."""
        return f"{settings.base_url}/admin/verify/{token}"


async def get_current_admin(
    token: str,
    db: AsyncSession,
) -> Optional[Admin]:
    """Dependency to get current authenticated admin from token."""
    auth_service = AuthService(db)
    payload = auth_service.verify_access_token(token)

    if not payload:
        return None

    admin_id = int(payload.get("sub"))
    return await auth_service.get_admin_by_id(admin_id)
