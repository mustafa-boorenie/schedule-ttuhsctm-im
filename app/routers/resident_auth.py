"""
Resident authentication routes with TTUHSC email fuzzy matching.

Texas Tech email format: FIRST_INITIAL + FIRST_7_LETTERS_OF_LAST_NAME @ ttuhsc.edu
Example: Mustafa Boorenie -> mbooreni@ttuhsc.edu
"""
import re
from typing import Optional, List, Tuple
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, Cookie
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from jose import jwt, JWTError

from ..database import get_db
from ..models import Resident
from ..settings import settings

router = APIRouter(prefix="/api/resident", tags=["resident-auth"])


def generate_ttuhsc_email(full_name: str) -> str:
    """
    Generate expected TTUHSC email from a full name.

    Format: first_initial + first_7_letters_of_last_name @ ttuhsc.edu

    Examples:
        "Mustafa Boorenie" -> "mbooreni@ttuhsc.edu"
        "John Smith" -> "jsmith@ttuhsc.edu"
        "Jane Van Der Berg" -> "jvander@ttuhsc.edu" (uses last word as last name)
    """
    # Clean and split the name
    name_parts = full_name.strip().split()

    if len(name_parts) < 2:
        # Single name - use it as both first and last
        first_initial = name_parts[0][0].lower() if name_parts else ""
        last_name = name_parts[0].lower() if name_parts else ""
    else:
        first_initial = name_parts[0][0].lower()
        # Use the last word as the last name
        last_name = name_parts[-1].lower()

    # Remove any non-alphabetic characters from last name
    last_name = re.sub(r'[^a-z]', '', last_name)

    # Take first 7 characters of last name
    last_name_portion = last_name[:7]

    return f"{first_initial}{last_name_portion}@ttuhsc.edu"


def parse_ttuhsc_email(email: str) -> Tuple[str, str]:
    """
    Parse a TTUHSC email to extract the first initial and last name portion.

    Returns: (first_initial, last_name_portion)
    """
    email = email.lower().strip()

    # Check if it's a TTUHSC email
    if not email.endswith("@ttuhsc.edu"):
        return ("", "")

    # Extract the username part
    username = email.split("@")[0]

    if len(username) < 2:
        return ("", "")

    first_initial = username[0]
    last_name_portion = username[1:]

    return (first_initial, last_name_portion)


def fuzzy_match_email_to_resident(
    email: str,
    residents: List[Resident]
) -> Optional[Resident]:
    """
    Fuzzy match an email to a resident using TTUHSC email format.

    Matching strategy:
    1. Exact email match (if resident has email stored)
    2. Generated email match (based on name)
    3. Fuzzy match allowing for minor variations
    """
    email = email.lower().strip()

    # Strategy 1: Check for exact email match in database
    for resident in residents:
        if resident.email and resident.email.lower() == email:
            return resident

    # Parse the input email
    first_initial, last_name_portion = parse_ttuhsc_email(email)

    if not first_initial or not last_name_portion:
        return None

    # Strategy 2: Generate expected emails and find exact match
    for resident in residents:
        expected_email = generate_ttuhsc_email(resident.name)
        if expected_email == email:
            return resident

    # Strategy 3: Fuzzy matching
    # Try matching with different last name interpretations
    candidates = []

    for resident in residents:
        name_parts = resident.name.strip().split()
        if not name_parts:
            continue

        resident_first_initial = name_parts[0][0].lower()

        # First initial must match
        if resident_first_initial != first_initial:
            continue

        # Try different last name interpretations
        for i in range(1, len(name_parts)):
            # Use each word after the first as potential last name
            potential_last = re.sub(r'[^a-z]', '', name_parts[i].lower())
            potential_last_portion = potential_last[:7]

            if potential_last_portion == last_name_portion:
                candidates.append((resident, i, len(name_parts)))

        # Also try combining last name parts (e.g., "Van Der Berg" -> "vanderb")
        if len(name_parts) > 2:
            combined_last = "".join(
                re.sub(r'[^a-z]', '', part.lower())
                for part in name_parts[1:]
            )
            combined_portion = combined_last[:7]
            if combined_portion == last_name_portion:
                candidates.append((resident, 0, len(name_parts)))  # Special marker

    if candidates:
        # Prefer the match using the actual last name (last word)
        # Sort by: (is_last_word_match, total_parts) descending
        candidates.sort(key=lambda x: (x[1] == x[2] - 1, -x[1]), reverse=True)
        return candidates[0][0]

    return None


def create_resident_token(resident: Resident) -> str:
    """Create a JWT access token for a resident."""
    expire = datetime.utcnow() + timedelta(days=settings.session_expire_days)
    payload = {
        "sub": str(resident.id),
        "name": resident.name,
        "type": "resident",
        "calendar_token": resident.calendar_token,
        "exp": expire,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def verify_resident_token(token: str) -> Optional[dict]:
    """Verify a JWT access token and return the payload if valid."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        if payload.get("type") != "resident":
            return None
        return payload
    except JWTError:
        return None


@router.post("/login")
async def resident_login(
    request: dict,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    Resident login via TTUHSC email fuzzy matching.

    The email is matched against resident names using the TTUHSC email format:
    FIRST_INITIAL + FIRST_7_LETTERS_OF_LAST_NAME @ ttuhsc.edu

    Example: mbooreni@ttuhsc.edu matches "Mustafa Boorenie"
    """
    email = request.get("email", "").lower().strip()

    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    # Get all active residents
    result = await db.execute(
        select(Resident).where(Resident.is_active == True)
    )
    residents = result.scalars().all()

    if not residents:
        raise HTTPException(
            status_code=404,
            detail="No residents found in the system"
        )

    # Try to match the email to a resident
    matched_resident = fuzzy_match_email_to_resident(email, list(residents))

    if not matched_resident:
        raise HTTPException(
            status_code=401,
            detail="Could not find a matching resident for this email. "
                   "Please ensure you're using your TTUHSC email address."
        )

    # Create access token
    token = create_resident_token(matched_resident)

    # Set cookie
    response.set_cookie(
        key="resident_token",
        value=token,
        httponly=True,
        secure=False,  # Set to True in production with HTTPS
        samesite="lax",
        max_age=60 * 60 * 24 * 7,  # 7 days
    )

    return {
        "message": "Login successful",
        "resident": {
            "id": matched_resident.id,
            "name": matched_resident.name,
            "pgy_level": matched_resident.pgy_level.value,
            "calendar_token": matched_resident.calendar_token,
        },
        "token": token,
    }


@router.get("/me")
async def get_current_resident(
    resident_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db),
):
    """Get information about the currently logged-in resident."""
    if not resident_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = verify_resident_token(resident_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    resident_id = int(payload.get("sub"))

    result = await db.execute(
        select(Resident).where(Resident.id == resident_id, Resident.is_active == True)
    )
    resident = result.scalar_one_or_none()

    if not resident:
        raise HTTPException(status_code=401, detail="Resident not found")

    return {
        "id": resident.id,
        "name": resident.name,
        "email": resident.email,
        "pgy_level": resident.pgy_level.value,
        "calendar_token": resident.calendar_token,
    }


@router.post("/logout")
async def resident_logout(response: Response):
    """Log out by clearing the resident token cookie."""
    response = Response(
        content='{"message": "Logged out"}',
        media_type="application/json"
    )
    response.delete_cookie("resident_token")
    return response


@router.get("/lookup")
async def lookup_email(
    email: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Debug endpoint to check what resident an email would match to.
    Returns the generated email format for a matched resident.
    """
    email = email.lower().strip()

    result = await db.execute(
        select(Resident).where(Resident.is_active == True)
    )
    residents = result.scalars().all()

    matched_resident = fuzzy_match_email_to_resident(email, list(residents))

    if not matched_resident:
        # Show what emails are available for debugging
        available = [
            {
                "name": r.name,
                "expected_email": generate_ttuhsc_email(r.name)
            }
            for r in residents[:10]  # Limit to 10 for privacy
        ]
        return {
            "matched": False,
            "input_email": email,
            "message": "No matching resident found",
            "sample_residents": available,
        }

    return {
        "matched": True,
        "input_email": email,
        "resident_name": matched_resident.name,
        "expected_email": generate_ttuhsc_email(matched_resident.name),
    }


# Dependency for protected routes
async def require_resident(
    resident_token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db),
) -> Resident:
    """Dependency that requires a valid resident session."""
    if not resident_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = verify_resident_token(resident_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    resident_id = int(payload.get("sub"))

    result = await db.execute(
        select(Resident).where(Resident.id == resident_id, Resident.is_active == True)
    )
    resident = result.scalar_one_or_none()

    if not resident:
        raise HTTPException(status_code=401, detail="Resident not found")

    return resident
