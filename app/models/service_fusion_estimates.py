"""
Service Fusion Estimate Models
"""

from pydantic import BaseModel, Field
from datetime import datetime, timezone


class SFEstimate(BaseModel):
    """Service Fusion Estimate model"""

    id: int
    number: str
    customer_id: int
    customer_name: str
    status: str
    updated_at: str

    class Config:
        extra = "ignore"  # Ignore extra fields we don't need

    @property
    def name(self) -> str:
        """Convenience property for display name"""
        return f"Estimate #{self.number}"

    @property
    def updated_at_datetime(self) -> datetime:
        """
        Parse SF timestamp (same as jobs/customers).
        SF API returns timestamps in client's local timezone despite +00:00.
        """
        from app.config import settings

        dt_str = self.updated_at.replace("+00:00", "")
        dt_naive = datetime.fromisoformat(dt_str)
        dt_sf_tz = dt_naive.replace(tzinfo=settings.sf_tz)
        dt_utc = dt_sf_tz.astimezone(timezone.utc)
        return dt_utc.replace(tzinfo=None)


class SFEstimateMeta(BaseModel):
    """Pagination metadata for estimates response"""

    total_count: int = Field(alias="totalCount")
    page_count: int = Field(alias="pageCount")
    current_page: int = Field(alias="currentPage")
    per_page: int = Field(alias="perPage")

    class Config:
        populate_by_name = True


class SFEstimatesResponse(BaseModel):
    """Service Fusion estimates list response"""

    items: list[SFEstimate]
    meta: SFEstimateMeta = Field(alias="_meta")

    class Config:
        populate_by_name = True

    # Convenience properties to match the pattern of other responses
    @property
    def total(self) -> int:
        return self.meta.total_count

    @property
    def page(self) -> int:
        return self.meta.current_page

    @property
    def per_page(self) -> int:
        return self.meta.per_page
