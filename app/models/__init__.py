"""
Data models for the sync application
"""

from .service_fusion import (
    SFCustomer,
    SFContact,
    SFPhone,
    SFEmail,
    SFCustomersResponse,
)
from .service_fusion_jobs import (
    SFJob,
    SFJobsResponse,
)
from .gohighlevel import (
    GHLContact,
    GHLContactsResponse,
    GHLCustomField,
)

from .service_fusion_estimates import SFEstimate, SFEstimatesResponse

__all__ = [
    "SFCustomer",
    "SFContact",
    "SFPhone",
    "SFEmail",
    "SFCustomersResponse",
    "SFJob",
    "SFJobsResponse",
    "GHLContact",
    "GHLContactsResponse",
    "GHLCustomField",
    "SFEstimate",  # ← ADD THIS
    "SFEstimatesResponse",  # ← ADD THIS
]
