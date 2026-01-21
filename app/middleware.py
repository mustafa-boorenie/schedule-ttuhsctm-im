"""
Middleware for error handling, logging, and rate limiting.
"""
import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Callable

from fastapi import Request, Response, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log all incoming requests with timing information."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = str(uuid.uuid4())[:8]
        start_time = time.time()

        # Add request ID to state for use in handlers
        request.state.request_id = request_id

        # Log request
        logger.info(
            f"[{request_id}] {request.method} {request.url.path} "
            f"from {request.client.host if request.client else 'unknown'}"
        )

        try:
            response = await call_next(request)
            duration = (time.time() - start_time) * 1000

            logger.info(
                f"[{request_id}] {request.method} {request.url.path} "
                f"-> {response.status_code} ({duration:.1f}ms)"
            )

            # Add timing header
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Response-Time"] = f"{duration:.1f}ms"

            return response
        except Exception as e:
            duration = (time.time() - start_time) * 1000
            logger.error(
                f"[{request_id}] {request.method} {request.url.path} "
                f"-> ERROR ({duration:.1f}ms): {str(e)}"
            )
            raise


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Comprehensive error handling for all requests."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        try:
            return await call_next(request)
        except HTTPException:
            # Let FastAPI handle HTTP exceptions normally
            raise
        except ValueError as e:
            logger.warning(f"Validation error: {str(e)}")
            return JSONResponse(
                status_code=400,
                content={
                    "error": "validation_error",
                    "message": str(e),
                    "path": request.url.path,
                },
            )
        except PermissionError as e:
            logger.warning(f"Permission denied: {str(e)}")
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": str(e) or "Permission denied",
                    "path": request.url.path,
                },
            )
        except Exception as e:
            logger.exception(f"Unhandled exception: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={
                    "error": "internal_server_error",
                    "message": "An unexpected error occurred",
                    "path": request.url.path,
                },
            )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiting for public endpoints."""

    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.requests: dict = defaultdict(list)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Only rate limit public API endpoints
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        # Skip rate limiting for admin endpoints (they have auth)
        if "/admin/" in request.url.path:
            return await call_next(request)

        # Get client IP
        client_ip = request.client.host if request.client else "unknown"

        # Clean old requests (older than 1 minute)
        current_time = time.time()
        self.requests[client_ip] = [
            t for t in self.requests[client_ip]
            if current_time - t < 60
        ]

        # Check rate limit
        if len(self.requests[client_ip]) >= self.requests_per_minute:
            logger.warning(f"Rate limit exceeded for {client_ip}")
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "message": f"Rate limit of {self.requests_per_minute} requests per minute exceeded",
                    "retry_after": 60,
                },
                headers={"Retry-After": "60"},
            )

        # Track this request
        self.requests[client_ip].append(current_time)

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Cache control for API responses
        if request.url.path.startswith("/api/"):
            if "/calendar/" in request.url.path:
                # Calendar files can be cached briefly
                response.headers["Cache-Control"] = "public, max-age=300"
            else:
                response.headers["Cache-Control"] = "no-store"

        return response
