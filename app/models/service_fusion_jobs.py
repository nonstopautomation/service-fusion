"""
Service Fusion Job Models

Minimal models for tracking job status changes.
"""

from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
from app.config import settings


class SFJob(BaseModel):
    """Service Fusion Job - only fields we need for status tracking"""

    id: int
    number: Optional[str] = None
    customer_id: int
    customer_name: Optional[str] = None
    status: str  # This is what we'll map to GHL stages
    updated_at: str

    # Optional fields for logging/debugging
    description: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    created_at: Optional[str] = None

    class Config:
        extra = "ignore"

    @property
    def updated_at_datetime(self) -> datetime:
        """
        Parse Service Fusion timestamp.

        SF API returns timestamps with +00:00 but they're actually in
        the client's local timezone (e.g., America/New_York).

        We need to:
        1. Parse the timestamp
        2. Interpret it as SF's timezone (not UTC!)
        3. Convert to UTC
        4. Return as timezone-naive for comparison
        """
        # Parse the timestamp (ignoring the lying +00:00)
        dt_str = self.updated_at.replace("+00:00", "")
        dt_naive = datetime.fromisoformat(dt_str)

        # Treat it as SF's timezone (not UTC!)
        dt_sf_tz = dt_naive.replace(tzinfo=settings.sf_tz)

        # Convert to UTC
        dt_utc = dt_sf_tz.astimezone(timezone.utc)

        # Return timezone-naive UTC (to match state manager)
        return dt_utc.replace(tzinfo=None)

    @property
    def created_at_datetime(self) -> Optional[datetime]:
        """Parse created_at string to datetime"""
        if not self.created_at:
            return None

        # Parse the timestamp (ignoring the lying +00:00)
        dt_str = self.created_at.replace("+00:00", "")
        dt_naive = datetime.fromisoformat(dt_str)

        # Treat it as SF's timezone (not UTC!)
        dt_sf_tz = dt_naive.replace(tzinfo=settings.sf_tz)

        # Convert to UTC
        dt_utc = dt_sf_tz.astimezone(timezone.utc)

        # Return timezone-naive UTC (to match state manager)
        return dt_utc.replace(tzinfo=None)


class SFJobsResponse(BaseModel):
    """Response from GET /v1/jobs"""

    items: list[SFJob]

    class Config:
        extra = "ignore"
