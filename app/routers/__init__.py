"""
API routers.
"""
from .admin_auth import router as admin_auth_router
from .admin import router as admin_router
from .schedule import router as schedule_router
from .amion import router as amion_router
from .days_off import router as days_off_router
from .swap import router as swap_router

__all__ = [
    "admin_auth_router",
    "admin_router",
    "schedule_router",
    "amion_router",
    "days_off_router",
    "swap_router",
]
