"""
Service Fusion API Models

Based on official Service Fusion API documentation.
We model only the fields we care about for syncing to GoHighLevel.
"""

from pydantic import BaseModel, Field
from typing import Optional, Any
import re
from app.config import settings
from datetime import datetime, timezone
# ============================================================================
# Nested Models (Contacts, Locations, Custom Fields)
# ============================================================================


class SFPhone(BaseModel):
    """Phone number from Service Fusion contact"""

    phone: Optional[str] = None
    ext: Optional[int] = None
    type: Optional[str] = None
    is_mobile: Optional[bool] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def normalized(self) -> Optional[str]:
        """Get phone number with only digits: (303) 555-1234 -> 3035551234"""
        if not self.phone:
            return None
        return re.sub(r"\D", "", self.phone)


class SFEmail(BaseModel):
    """Email address from Service Fusion contact"""

    email: Optional[str] = None
    email_class: Optional[str] = Field(None, alias="class")  # 'class' is Python keyword
    types_accepted: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SFContact(BaseModel):
    """Contact person associated with a Service Fusion customer"""

    prefix: Optional[str] = None
    fname: Optional[str] = None  # Service Fusion uses 'fname', not 'first_name'
    lname: Optional[str] = None  # Service Fusion uses 'lname', not 'last_name'
    suffix: Optional[str] = None
    contact_type: Optional[str] = None
    dob: Optional[str] = None
    anniversary: Optional[str] = None
    job_title: Optional[str] = None
    department: Optional[str] = None
    is_primary: Optional[bool] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    phones: list[SFPhone] = Field(default_factory=list)
    emails: list[SFEmail] = Field(default_factory=list)

    class Config:
        populate_by_name = True  # Allow using aliases

    @property
    def primary_phone(self) -> Optional[str]:
        """Get first phone number (normalized to digits only)"""
        return self.phones[0].normalized if self.phones else None

    @property
    def primary_email(self) -> Optional[str]:
        """Get first email address"""
        return self.emails[0].email if self.emails else None

    @property
    def full_name(self) -> str:
        """Get full name with prefix/suffix if available"""
        parts = [self.prefix, self.fname, self.lname, self.suffix]
        return " ".join(p for p in parts if p).strip()

    @property
    def first_name(self) -> Optional[str]:
        """Alias for fname (for readability in code)"""
        return self.fname

    @property
    def last_name(self) -> Optional[str]:
        """Alias for lname (for readability in code)"""
        return self.lname


class SFLocation(BaseModel):
    """Location/address for a Service Fusion customer"""

    street_1: Optional[str] = None
    street_2: Optional[str] = None
    city: Optional[str] = None
    state_prov: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None
    nickname: Optional[str] = None
    gate_instructions: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_type: Optional[str] = None
    is_primary: Optional[bool] = None
    is_gated: Optional[bool] = None
    is_bill_to: Optional[bool] = None
    customer_contact: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def full_address(self) -> str:
        """Format as single address string"""
        parts = []
        if self.street_1:
            parts.append(self.street_1)
        if self.street_2:
            parts.append(self.street_2)

        city_state_zip = []
        if self.city:
            city_state_zip.append(self.city)
        if self.state_prov:
            city_state_zip.append(self.state_prov)
        if self.postal_code:
            city_state_zip.append(self.postal_code)

        if city_state_zip:
            parts.append(", ".join(city_state_zip))

        return ", ".join(parts) if parts else ""


class SFCustomField(BaseModel):
    """Custom field from Service Fusion"""

    name: Optional[str] = None
    value: Any = None  # Can be any type
    type: Optional[str] = None
    group: Optional[str] = None
    is_required: Optional[bool] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ============================================================================
# Main Customer Model
# ============================================================================


class SFCustomer(BaseModel):
    """
    Service Fusion Customer

    This is the main entity we sync to GoHighLevel.
    Only includes fields we care about - Pydantic ignores extras.
    """

    # === Required Fields ===
    id: int
    customer_name: str
    updated_at: str  # ISO datetime string

    # === Optional Customer Info ===
    fully_qualified_name: Optional[str] = None
    parent_customer: Optional[str] = None
    account_number: Optional[str] = None
    account_balance: Optional[float] = None

    # === Notes ===
    private_notes: Optional[str] = None
    public_notes: Optional[str] = None

    # === Customer Settings ===
    credit_rating: Optional[str] = None
    is_vip: Optional[bool] = False
    is_taxable: Optional[bool] = None

    # === Business Info ===
    referral_source: Optional[str] = None
    agent: Optional[str] = None
    industry: Optional[str] = None

    # === Financial Settings ===
    labor_charge_type: Optional[str] = None
    labor_charge_default_rate: Optional[float] = None
    discount: Optional[float] = None
    discount_type: Optional[str] = None
    payment_type: Optional[str] = None
    payment_terms: Optional[str] = None
    assigned_contract: Optional[str] = None
    tax_item_name: Optional[str] = None

    # === Dates ===
    last_serviced_date: Optional[str] = None
    created_at: Optional[str] = None

    # === Settings ===
    is_bill_for_drive_time: Optional[bool] = None

    # === QuickBooks Integration (we don't care but SF returns it) ===
    qbo_sync_token: Optional[int] = None
    qbo_currency: Optional[str] = None
    qbo_id: Optional[int] = None
    qbd_id: Optional[str] = None

    # === Nested Data (what we really care about!) ===
    contacts: list[SFContact] = Field(default_factory=list)
    locations: list[SFLocation] = Field(default_factory=list)
    custom_fields: list[SFCustomField] = Field(default_factory=list)

    class Config:
        # Allow extra fields in the API response that we don't model
        extra = "ignore"

    # === Computed Properties for Easy Access ===

    @property
    def primary_contact(self) -> Optional[SFContact]:
        """Get the primary contact (or first contact if no primary marked)"""
        # First try to find contact marked as primary
        for contact in self.contacts:
            if contact.is_primary:
                return contact
        # Fallback to first contact
        return self.contacts[0] if self.contacts else None

    @property
    def phone(self) -> Optional[str]:
        """Get primary phone number (normalized)"""
        contact = self.primary_contact
        return contact.primary_phone if contact else None

    @property
    def email(self) -> Optional[str]:
        """Get primary email address"""
        contact = self.primary_contact
        return contact.primary_email if contact else None

    @property
    def first_name(self) -> str:
        """Extract first name from contact or customer_name"""
        contact = self.primary_contact
        if contact and contact.fname:
            return contact.fname
        # Fallback: split customer_name
        return self.customer_name.split()[0] if self.customer_name else ""

    @property
    def last_name(self) -> str:
        """Extract last name from contact or customer_name"""
        contact = self.primary_contact
        if contact and contact.lname:
            return contact.lname
        # Fallback: split customer_name
        parts = self.customer_name.split()
        return " ".join(parts[1:]) if len(parts) > 1 else ""

    @property
    def primary_location(self) -> Optional[SFLocation]:
        """Get primary location (or first location)"""
        # First try to find location marked as primary
        for location in self.locations:
            if location.is_primary:
                return location
        # Fallback to first location
        return self.locations[0] if self.locations else None

    @property
    def address(self) -> Optional[str]:
        """Get formatted primary address"""
        location = self.primary_location
        return location.full_address if location else None

    @property
    def updated_at_datetime(self) -> datetime:
        """
        Parse Service Fusion timestamp.

        SF API returns timestamps in UTC with +00:00 suffix.
        We strip the timezone info and return as timezone-naive UTC.
        """
        dt_str = self.updated_at.replace("+00:00", "").replace("Z", "")

        return datetime.fromisoformat(dt_str)

    def get_custom_field(self, field_name: str) -> Any:
        """Get custom field value by name"""
        for field in self.custom_fields:
            if field.name == field_name:
                return field.value
        return None

    def __str__(self):
        return f"SFCustomer(id={self.id}, name='{self.customer_name}')"

    def __repr__(self):
        return self.__str__()


# ============================================================================
# API Response Models
# ============================================================================


class SFMeta(BaseModel):
    """Pagination metadata from Service Fusion API"""

    totalCount: Optional[int] = None
    pageCount: Optional[int] = None
    currentPage: int = 0  # Default is 0 maybe?
    perPage: Optional[int] = None


class SFCustomersResponse(BaseModel):
    """Response from GET /v1/customers"""

    items: list[SFCustomer]
    expandable: Optional[list[str]] = Field(default_factory=list, alias="_expandable")
    meta: Optional[SFMeta] = Field(None, alias="_meta")

    class Config:
        populate_by_name = True  # Allows using aliases


# ============================================================================
# Usage Examples (Documentation)
# ============================================================================

"""
Example Usage:

# 1. Parse API response
api_data = await httpx_client.get("/v1/customers").json()
response = SFCustomersResponse(**api_data)

# 2. Access data with full type safety
for customer in response.items:
    print(f"Customer: {customer.customer_name}")
    print(f"Phone: {customer.phone}")           # Normalized: 3035551234
    print(f"Email: {customer.email}")
    print(f"Address: {customer.address}")
    print(f"First: {customer.first_name}")
    print(f"Last: {customer.last_name}")
    print(f"Updated: {customer.updated_at_datetime}")

# 3. IDE autocomplete works perfectly
customer.  # <-- IDE shows all available properties

# 4. Type checking catches errors at development time
customer.id  # int
customer.phone  # Optional[str]
customer.updated_at_datetime  # datetime

# 5. Validation happens automatically
bad_data = {"items": [{"id": "not_a_number"}]}
response = SFCustomersResponse(**bad_data)  # ValidationError!
"""
