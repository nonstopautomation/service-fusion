"""
Configuration Management
Loads environment variables and provides typed config objects.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from zoneinfo import ZoneInfo


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    # Service Fusion API
    sf_client_id: str = Field(..., alias="SERVICE_FUSION_CLIENT_ID")
    sf_client_secret: str = Field(..., alias="SERVICE_FUSION_CLIENT_SECRET")
    sf_api_base_url: str = "https://api.servicefusion.com"

    # GoHighLevel API
    ghl_api_token: str = Field(..., alias="GHL_API_TOKEN")
    ghl_location_id: str = Field(..., alias="GHL_LOCATION_ID")

    # GHL Custom Field IDs
    ghl_sf_customer_id_field: str = Field(default="", alias="GHL_SF_CUSTOMER_ID_FIELD")
    ghl_sf_last_sync_field: str = Field(default="", alias="GHL_SF_LAST_SYNC_FIELD")
    ghl_sf_updated_at_field: str = Field(default="", alias="GHL_SF_UPDATED_AT_FIELD")

    # GHL Pipeline Stage IDs
    ghl_stage_appointment_request: str = Field(
        default="", alias="GHL_STAGE_APPOINTMENT_REQUEST"
    )
    ghl_stage_estimate_scheduled: str = Field(
        default="", alias="GHL_STAGE_ESTIMATE_SCHEDULED"
    )
    ghl_stage_estimate_sent: str = Field(default="", alias="GHL_STAGE_ESTIMATE_SENT")
    ghl_stage_estimate_stop: str = Field(default="", alias="GHL_STAGE_ESTIMATE_STOP")
    ghl_stage_canceled: str = Field(default="", alias="GHL_STAGE_CANCELED")
    ghl_stage_job_scheduled: str = Field(default="", alias="GHL_STAGE_JOB_SCHEDULED")
    ghl_stage_job_in_progress: str = Field(
        default="", alias="GHL_STAGE_JOB_IN_PROGRESS"
    )
    ghl_stage_review_referral: str = Field(
        default="", alias="GHL_STAGE_REVIEW_REFERRAL"
    )
    # In the Settings class, add:
    ghl_opportunity_crm_job_id_field: str = Field(
        default="", alias="GHL_OPPORTUNITY_CRM_JOB_ID_FIELD"
    )
    ghl_pipeline_id: str = Field(default="", alias="GHL_PIPELINE_ID")
    # Service Fusion Status Names
    sf_status_unscheduled: str = Field(
        default="Unscheduled", alias="SF_STATUS_UNSCHEDULED"
    )
    sf_status_scheduled: str = Field(default="Scheduled", alias="SF_STATUS_SCHEDULED")
    sf_status_dispatched: str = Field(
        default="Dispatched", alias="SF_STATUS_DISPATCHED"
    )
    sf_status_delayed: str = Field(default="Delayed", alias="SF_STATUS_DELAYED")
    sf_status_on_the_way: str = Field(
        default="On The Way", alias="SF_STATUS_ON_THE_WAY"
    )
    sf_status_on_site: str = Field(default="On Site", alias="SF_STATUS_ON_SITE")
    sf_status_started: str = Field(default="Started", alias="SF_STATUS_STARTED")
    sf_status_paused: str = Field(default="Paused", alias="SF_STATUS_PAUSED")
    sf_status_resumed: str = Field(default="Resumed", alias="SF_STATUS_RESUMED")
    sf_status_completed: str = Field(default="Completed", alias="SF_STATUS_COMPLETED")
    sf_status_cancelled: str = Field(default="Cancelled", alias="SF_STATUS_CANCELLED")
    sf_status_job_closed: str = Field(
        default="Job Closed", alias="SF_STATUS_JOB_CLOSED"
    )
    sf_status_to_be_invoiced: str = Field(
        default="To be invoiced", alias="SF_STATUS_TO_BE_INVOICED"
    )
    sf_status_invoiced: str = Field(default="Invoiced", alias="SF_STATUS_INVOICED")
    sf_status_paid_in_full: str = Field(
        default="Paid in Full", alias="SF_STATUS_PAID_IN_FULL"
    )
    sf_status_partially_completed: str = Field(
        default="Partially Completed", alias="SF_STATUS_PARTIALLY_COMPLETED"
    )
    sf_status_archived: str = Field(default="Archived", alias="SF_STATUS_ARCHIVED")

    # Estimate statuses
    sf_estimate_status_requested: str = "Estimate Requested"
    sf_estimate_status_scheduled: str = "Estimate Scheduled"
    sf_estimate_status_provided: str = "Estimate Provided"
    sf_estimate_status_accepted: str = "Estimate Accepted"
    sf_estimate_status_won: str = "Estimate Won"
    sf_estimate_status_lost: str = "Lost"
    # App Settings
    port: int = Field(default=8080, alias="PORT")
    sync_interval_minutes: int = Field(default=5, alias="SYNC_INTERVAL_MINUTES")
    state_file_path: str = "sync_state.json"

    sf_timezone: str = "America/New_York"

    @property
    def sf_tz(self) -> ZoneInfo:
        """Get Service Fusion timezone object"""
        return ZoneInfo(self.sf_timezone)

    class Config:
        env_file = ".env"
        case_sensitive = False


# Global settings instance
settings = Settings()  # type: ignore[arg-type]


# Single unified mapping for both jobs AND estimates
sf_to_ghl_stage_map = {
    # Job statuses
    settings.sf_status_unscheduled: settings.ghl_stage_job_scheduled,
    settings.sf_status_cancelled: settings.ghl_stage_canceled,
    settings.sf_status_scheduled: settings.ghl_stage_job_scheduled,
    settings.sf_status_dispatched: settings.ghl_stage_job_scheduled,
    settings.sf_status_partially_completed: settings.ghl_stage_job_in_progress,
    settings.sf_status_delayed: settings.ghl_stage_job_in_progress,
    settings.sf_status_on_the_way: settings.ghl_stage_job_in_progress,
    settings.sf_status_on_site: settings.ghl_stage_job_in_progress,
    settings.sf_status_started: settings.ghl_stage_job_in_progress,
    settings.sf_status_paused: settings.ghl_stage_job_in_progress,
    settings.sf_status_resumed: settings.ghl_stage_job_in_progress,
    settings.sf_status_completed: settings.ghl_stage_review_referral,
    # Estimates
    settings.sf_estimate_status_requested: settings.ghl_stage_estimate_scheduled,
    settings.sf_estimate_status_accepted: settings.ghl_stage_estimate_stop,
    settings.sf_estimate_status_won: settings.ghl_stage_estimate_stop,
    settings.sf_estimate_status_lost: settings.ghl_stage_estimate_stop,
    settings.sf_estimate_status_provided: settings.ghl_stage_estimate_sent,
}
