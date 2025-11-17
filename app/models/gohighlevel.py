"""
GoHighLevel API Models

Pydantic models for GoHighLevel contacts and API responses.
"""

from pydantic import BaseModel, Field
from typing import Optional, Any


class GHLCustomField(BaseModel):
    """Custom fields for GHL contact"""

    sf_customer_id: Optional[str] = None
    sf_last_sync: Optional[str] = None
    sf_updated_at: Optional[str] = None


class GHLContact(BaseModel):
    """GoHighLevel Contact"""

    id: Optional[str] = None
    locationId: Optional[str] = None

    # Basic info
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    name: Optional[str] = None  # Full name
    email: Optional[str] = None
    phone: Optional[str] = None

    # Address
    address1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postalCode: Optional[str] = None
    country: Optional[str] = None

    # Metadata
    source: Optional[str] = None
    dateAdded: Optional[str] = None
    dateUpdated: Optional[str] = None

    # Custom fields
    customField: Optional[dict[str, Any]] = Field(default_factory=dict)

    class Config:
        extra = "ignore"


class GHLContactsResponse(BaseModel):
    """Response from GET /contacts"""

    contacts: list[GHLContact] = Field(default_factory=list)
    total: Optional[int] = None

    class Config:
        extra = "ignore"
