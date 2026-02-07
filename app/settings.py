"""
Application settings and environment configuration.
"""
import logging
from typing import List, Optional, Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    app_name: str = "Residency Rotation Calendar"
    app_version: str = "2.0.1"
    debug: bool = False
    testing: bool = False
    base_url: str = "http://localhost:8000"
    secret_key: str = Field(default="change-me-in-production-use-strong-secret")

    # CORS
    cors_origins: Union[List[str], str] = ["*"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        if isinstance(v, str):
            if v == "*":
                return ["*"]
            return [origin.strip() for origin in v.split(",")]
        return v

    # Rate limiting
    rate_limit_per_minute: int = 60

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/rotation_calendar"
    )
    database_pool_size: int = 5
    database_max_overflow: int = 10

    # Email (for magic links)
    smtp_host: str = "smtp.sendgrid.net"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_email: str = "calendar@example.com"

    # Magic link settings
    magic_link_expire_minutes: int = 15
    session_expire_days: int = 7
    admin_password: Optional[str] = None

    # Scheduler
    scheduler_enabled: bool = True

    # OpenAI (for LLM parsing)
    openai_api_key: Optional[str] = None

    # Amion
    amion_base_url: str = ""
    amion_sync_hour: int = 3  # 3 AM daily sync
    amion_all_rows_url: str = ""
    amion_oncall_url: str = ""

    # Schedule settings
    schedule_start_year: int = 2025
    schedule_start_month: int = 7
    schedule_start_day: int = 1

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, v):
        if v == "change-me-in-production-use-strong-secret":
            logger.warning(
                "Using default secret key! Set SECRET_KEY environment variable in production."
            )
        elif len(v) < 32:
            logger.warning("Secret key should be at least 32 characters for security.")
        return v

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v):
        if "localhost" in v and not v.startswith("postgresql"):
            raise ValueError("Invalid database URL format")
        return v

    def validate_production_settings(self) -> List[str]:
        """
        Validate settings for production deployment.
        Returns list of warnings/errors.
        """
        issues = []

        if self.debug:
            issues.append("DEBUG mode is enabled - disable for production")

        if self.secret_key == "change-me-in-production-use-strong-secret":
            issues.append("Default SECRET_KEY is being used - set a secure key")

        if not self.smtp_user or not self.smtp_password:
            issues.append("SMTP credentials not configured - email features won't work")

        if "localhost" in self.database_url:
            issues.append("Using localhost database - configure remote DB for production")

        if self.cors_origins == ["*"]:
            issues.append("CORS allows all origins - restrict for production")

        return issues

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


# Global settings instance
settings = Settings()
