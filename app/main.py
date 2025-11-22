"""
Service Fusion → GoHighLevel Sync Application

Syncs customers and jobs from Service Fusion to GoHighLevel.
"""

from fastapi import FastAPI, HTTPException, Request
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager

from app.config import settings, sf_to_ghl_stage_map
from app.services import sf_client, ghl_client
from app.utils import state_manager
from app.models import SFCustomer, SFJob, SFEstimate
from typing import Union, Optional

import uvicorn
import httpx
from app.error_handler import (
    safe_scheduled_job,
    ErrorSeverity,
    JobSyncError,
    slack_notifier,
)
# ============================================================================
# Customer Sync Logic
# ============================================================================


async def handle_sf_customer_update(customer: SFCustomer):
    """
    Handle a Service Fusion customer update by upserting to GoHighLevel.

    GHL's upsert will automatically match by phone/email based on their settings.
    """

    print(customer)
    # Skip if no phone or email
    if not customer.phone and not customer.email:
        print("  Skipping - no phone or email")
        print()
        return

    try:
        # Build contact data
        contact_data = {
            "firstName": customer.first_name,
            "lastName": customer.last_name,
            "phone": customer.phone,
            "email": customer.email,
            "source": "Service Fusion",
        }

        # Add custom fields using IDs from config
        custom_fields = []

        if settings.ghl_sf_customer_id_field:
            custom_fields.append(
                {
                    "id": settings.ghl_sf_customer_id_field,
                    "field_value": str(customer.id),
                }
            )

        if settings.ghl_sf_last_sync_field:
            custom_fields.append(
                {
                    "id": settings.ghl_sf_last_sync_field,
                    "field_value": datetime.now(timezone.utc).isoformat(),
                }
            )

        if settings.ghl_sf_updated_at_field:
            custom_fields.append(
                {
                    "id": settings.ghl_sf_updated_at_field,
                    "field_value": customer.updated_at,
                }
            )

        if custom_fields:
            contact_data["customFields"] = custom_fields

        await ghl_client.upsert_contact(contact_data)

    except Exception as e:
        print(f"  Error syncing customer {customer.id}: {e}")
        print()
        raise


@safe_scheduled_job
async def check_for_customer_updates():
    """Poll Service Fusion for updated customers and sync to GHL"""

    # ═══════════════════════════════════════════════════════════════
    # INFRASTRUCTURE CALLS - Let errors propagate to decorator
    # ═══════════════════════════════════════════════════════════════
    print("Checking for customer updates")
    # Get last poll time from state
    last_poll = state_manager.get_last_customer_poll_time()
    # ↑ If this fails (corrupted state file), error propagates UP to decorator

    # Get updated customers from Service Fusion
    customers = await sf_client.get_updated_customers(since=last_poll)
    # ↑ If this fails (SF API down), error propagates UP to decorator
    print(len(customers))

    # ═══════════════════════════════════════════════════════════════
    # INDIVIDUAL CUSTOMER SYNC - Handle partial failures
    # ═══════════════════════════════════════════════════════════════

    sync_errors = []  # Track which customers failed

    for customer in customers:
        try:
            await handle_sf_customer_update(customer)
            # ↑ If THIS customer fails, we catch it below

        except Exception as e:
            # ONE customer failed - log it and continue with others
            print(f"  Failed to sync customer {customer.id}: {e}")
            sync_errors.append({"customer_id": customer.id, "error": str(e)})
            # DON'T re-raise - continue to next customer

    # ═══════════════════════════════════════════════════════════════
    # BATCH NOTIFICATION - Alert about partial failures
    # ═══════════════════════════════════════════════════════════════

    if sync_errors:
        await slack_notifier.send_error(
            error=Exception(f"{len(sync_errors)} customers failed to sync"),
            function_name="check_for_customer_updates",
            severity=ErrorSeverity.MEDIUM,
            context={
                "total_customers": len(customers),
                "failed_count": len(sync_errors),
                "failed_customers": sync_errors,  # First 5 examples
            },
        )

    # ═══════════════════════════════════════════════════════════════
    # UPDATE STATE AND STATS
    # ═══════════════════════════════════════════════════════════════

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    state_manager.save_last_poll_time(now)

    stats = state_manager.get_stats()
    total_checks = stats.get("total_checks", 0) + 1
    total_updates = stats.get("total_updates_found", 0) + len(customers)

    state_manager.update_stats(
        total_checks=total_checks,
        total_updates_found=total_updates,
        last_check=now.isoformat(),
    )

    print(f"\n{'=' * 80}")
    if customers:
        success_count = len(customers) - len(sync_errors)
        print(f"Check complete - {success_count}/{len(customers)} customer(s) synced")
        if sync_errors:
            print(f"{len(sync_errors)} error(s) occurred - check Slack for details")
    else:
        print("Check complete - No updates found")
    print(f"Total checks: {total_checks} | Total updates found: {total_updates}")
    print(f"{'=' * 80}\n")


# ============================================================================
# Job Sync Logic
# ============================================================================


async def find_converted_estimate_for_job(job: SFJob) -> Optional[int]:
    """
    When a job is created, check if it came from a converted estimate.
    Match by: same customer + same updated_at timestamp + "Estimate Won" status
    """
    # Only check for newly created jobs (created_at == updated_at)
    if not job.created_at or job.created_at != job.updated_at:
        return None  # Job has been updated since creation, not a fresh conversion

    print("  Checking if job came from converted estimate...")

    # Get recent estimates for this customer (last 2 hours to be safe)
    from datetime import timedelta

    # Parse the job's created_at to datetime
    job_created_dt = job.created_at_datetime
    if not job_created_dt:
        return None  # Can't determine conversion without created_at

    since = job_created_dt - timedelta(hours=2)

    try:
        estimates = await sf_client.get_updated_estimates(since=since, max_results=50)

        # Find estimate with exact match
        for estimate in estimates:
            if (
                estimate.customer_id == job.customer_id
                and estimate.status == "Estimate Won"
                and estimate.updated_at
                == job.updated_at  # Exact same timestamp string!
            ):
                print(f"  MATCH FOUND: Estimate #{estimate.id} → Job #{job.id}")
                print(f"     Both updated at: {job.updated_at}")
                return estimate.id

        print("  No matching estimate found - treating as new job")
        return None

    except Exception as e:
        print(f"  Error checking for converted estimate: {e}")
        return None  # Fail gracefully - treat as new job


async def sync_work_order_to_ghl(
    work_order: Union[SFJob, SFEstimate], index: int, work_type: str = "Job"
):
    """
    Sync a Service Fusion job OR estimate to GoHighLevel opportunity.

    Args:
        work_order: Either SFJob or SFEstimate
        index: Display index
        work_type: "Job" or "Estimate" for logging
    """
    print(f"{'─' * 80}")
    print(f"[{index}] {work_type}: #{work_order.number} (ID: {work_order.id})")
    print(f"    Customer: {work_order.customer_name} (ID: {work_order.customer_id})")
    print(f"    Status: {work_order.status}")
    print(f"{'─' * 80}")

    # Check if this is a converted estimate → job
    converted_from_estimate_id = None
    if isinstance(work_order, SFJob):
        converted_from_estimate_id = await find_converted_estimate_for_job(work_order)

    # Map SF status to GHL stage
    ghl_stage_id = sf_to_ghl_stage_map.get(work_order.status)

    if not ghl_stage_id:
        print(f"  Unknown SF status: '{work_order.status}' - skipping")
        print()
        return

    try:
        # Step 1: Get SF customer data
        print("  Fetching SF customer data...")
        sf_customer = await sf_client.get_customer_by_id(work_order.customer_id)

        if not sf_customer:
            print(f"  Customer {work_order.customer_id} not found in Service Fusion")
            print()
            raise JobSyncError(
                message=f"Customer {work_order.customer_id} does not exist",
                severity=ErrorSeverity.MEDIUM,
                context={
                    "work_order_id": work_order.id,
                    "work_order_number": work_order.number,
                    "customer_id": work_order.customer_id,
                },
            )

        # Validate contact info
        if not sf_customer.phone and not sf_customer.email:
            print("  Customer has no phone or email - skipping")
            print()
            await slack_notifier.send_error(
                error=Exception(
                    f"{work_type} {work_order.id} skipped - no contact info"
                ),
                function_name="sync_work_order_to_ghl",
                severity=ErrorSeverity.LOW,
                context={
                    "work_order_id": work_order.id,
                    "work_type": work_type,
                },
            )
            return

        # Step 2: Find/create contact
        print("  Looking up GHL contact...")
        ghl_contact = None

        if sf_customer.phone:
            print("  Searching by phone...")
            ghl_contact = await ghl_client.search_contact_by_phone(sf_customer.phone)

        if not ghl_contact and sf_customer.email:
            print("  Searching by email...")
            ghl_contact = await ghl_client.search_contact_by_email(sf_customer.email)

        if ghl_contact:
            print("  Found contact, adding SF customer ID...")
            await ghl_client.update_contact_custom_field(
                ghl_contact["id"],
                settings.ghl_sf_customer_id_field,
                str(sf_customer.id),
            )
        else:
            print("  Creating new GHL contact...")
            contact_data = {
                "firstName": sf_customer.first_name,
                "lastName": sf_customer.last_name,
                "source": "Service Fusion",
            }

            if sf_customer.phone:
                contact_data["phone"] = sf_customer.phone
            if sf_customer.email:
                contact_data["email"] = sf_customer.email

            contact = await ghl_client.upsert_contact(contact_data)
            ghl_contact = {"id": contact.id}
            print(f"  Created contact {ghl_contact['id']}")

        print(f"  Using contact {ghl_contact['id']}")

        # Extract contact_id for use in opportunity lookups
        contact_id = ghl_contact.get("id")
        if not contact_id:
            raise JobSyncError(
                message="Failed to get GHL contact ID",
                severity=ErrorSeverity.HIGH,
                context={
                    "work_order_id": work_order.id,
                    "customer_id": work_order.customer_id,
                },
            )

        # Step 3: Handle converted estimates OR find existing opportunity
        opportunity = None

        if converted_from_estimate_id:
            # This job was converted from an estimate - find the existing opportunity
            print(
                f"  Looking for opportunity from converted Estimate #{converted_from_estimate_id}..."
            )
            opportunity = await ghl_client.search_opportunity_by_job_id(
                converted_from_estimate_id, contact_id
            )

            if opportunity:
                print(f"  Found estimate's opportunity {opportunity['id']}")
                print(
                    f"  Updating crm_job_id: {converted_from_estimate_id} → {work_order.id}"
                )

                # Update the custom field to the new job ID
                await ghl_client.update_opportunity_custom_field(
                    opportunity["id"], "crm_job_id", str(work_order.id)
                )
                # Keep opportunity reference so we can update the stage below
            else:
                print(
                    "  Estimate opportunity not found - will create new job opportunity"
                )

        # ONLY search if we don't already have an opportunity from the conversion
        if not opportunity:
            # Normal flow: look up by current work order ID
            print("  Looking up GHL opportunity...")
            print(f"      Contact ID: {contact_id}")
            print(f"      Work Order ID: {work_order.id}")

            opportunity = await ghl_client.search_opportunity_by_job_id(
                work_order.id, contact_id
            )

            if opportunity:
                print(f"      Found existing opportunity: {opportunity['id']}")
            else:
                print("      No existing opportunity found - will create new")

        # Step 4: Create or update opportunity
        if not opportunity:
            # Create new opportunity using POST (not upsert)
            print("  Creating new opportunity...")
            opp_data = {
                "contactId": contact_id,
                "pipelineId": settings.ghl_pipeline_id,
                "pipelineStageId": ghl_stage_id,
                "name": f"SF-{work_order.id}: {work_type} #{work_order.number}",
                "status": "open",
                "customFields": [
                    {
                        "id": settings.ghl_opportunity_crm_job_id_field,  # Use field ID, not key
                        "field_value": str(work_order.id),
                    }
                ],
            }

            opportunity = await ghl_client.create_opportunity(opp_data)
            print(f"  Created opportunity {opportunity.get('id')}")
        else:
            # Update existing opportunity stage
            current_stage = opportunity.get("pipelineStageId")

            if current_stage == ghl_stage_id:
                print("  Opportunity stage already correct")
            else:
                print(f"  Updating opportunity stage: {current_stage} → {ghl_stage_id}")
                await ghl_client.update_opportunity(
                    opportunity["id"],
                    {
                        "pipelineStageId": ghl_stage_id,
                        "status": "open",
                    },
                )
                print("  Updated opportunity stage")

        print(f"  Status: {work_order.status} → Stage: {ghl_stage_id}")
        print()

    except JobSyncError:
        print(f"  Error syncing {work_type.lower()}")
        print()
        raise

    except Exception as e:
        print(f"  Error syncing {work_type.lower()}")
        print()
        raise JobSyncError(
            message=f"Failed to sync {work_type.lower()} {work_order.id}",
            severity=ErrorSeverity.HIGH,
            context={
                "work_order_id": work_order.id,
                "work_order_number": work_order.number,
                "customer_id": work_order.customer_id,
                "original_error": str(e),
            },
        ) from e


@safe_scheduled_job
async def check_for_job_updates():
    """Poll Service Fusion for updated jobs and sync to GHL"""

    print(f"\n{'=' * 80}")
    print(f"Checking for JOB updates - {datetime.now(timezone.utc).isoformat()}")
    print(f"{'=' * 80}\n")

    # ═══════════════════════════════════════════════════════════════
    # INFRASTRUCTURE CALLS - Let errors propagate to decorator
    # ═══════════════════════════════════════════════════════════════

    # Get last poll time for jobs
    last_poll = state_manager.get_last_job_poll_time()
    # ↑ If this fails (corrupted state file), error propagates UP to decorator
    print(f"Last job poll: {last_poll.isoformat()}")

    # Get updated jobs
    jobs = await sf_client.get_updated_jobs(since=last_poll)
    # ↑ If this fails (SF API down), error propagates UP to decorator
    print(f"Found {len(jobs)} updated job(s)\n")

    # ═══════════════════════════════════════════════════════════════
    # INDIVIDUAL JOB SYNC - Handle partial failures
    # ═══════════════════════════════════════════════════════════════

    sync_errors = []  # Track which jobs failed

    # Sync each job
    for idx, job in enumerate(jobs, 1):
        try:
            await sync_work_order_to_ghl(job, idx, work_type="Job")

        except JobSyncError as e:
            # ONE job failed - log it and continue with others
            print(f"  Failed to sync job {job.id}: {e.message}")
            sync_errors.append(
                {"job_id": job.id, "job_number": job.number, "error": e.message}
            )
            # DON'T re-raise - continue to next job

        except Exception as e:
            # Unexpected error - log and continue
            print(f"  Unexpected error syncing job {job.id}: {e}")
            sync_errors.append(
                {"job_id": job.id, "job_number": job.number, "error": str(e)}
            )

    # ═══════════════════════════════════════════════════════════════
    # BATCH NOTIFICATION - Alert about partial failures
    # ═══════════════════════════════════════════════════════════════

    if sync_errors:
        await slack_notifier.send_error(
            error=Exception(f"{len(sync_errors)} jobs failed to sync"),
            function_name="check_for_job_updates",
            severity=ErrorSeverity.MEDIUM,
            context={
                "total_jobs": len(jobs),
                "failed_count": len(sync_errors),
                "failed_jobs": sync_errors[:5],  # First 5 examples
            },
        )

    # ═══════════════════════════════════════════════════════════════
    # UPDATE STATE AND STATS
    # ═══════════════════════════════════════════════════════════════

    # Update state
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    state_manager.save_last_job_poll_time(now)

    # Update stats
    stats = state_manager.get_stats()
    state_manager.update_stats(
        total_job_checks=stats.get("total_job_checks", 0) + 1,
        total_job_updates_found=stats.get("total_job_updates_found", 0) + len(jobs),
    )

    # ═══════════════════════════════════════════════════════════════
    # SUMMARY OUTPUT
    # ═══════════════════════════════════════════════════════════════

    print(f"{'=' * 80}")
    if jobs:
        success_count = len(jobs) - len(sync_errors)
        print(f"Synced {success_count}/{len(jobs)} job update(s)")
        if sync_errors:
            print(f"{len(sync_errors)} error(s) occurred - check Slack for details")
    else:
        print("No job updates found")
    print(f"{'=' * 80}\n")


@safe_scheduled_job
async def check_for_estimate_updates():
    """Poll Service Fusion for updated estimates and sync to GHL"""

    print(f"\n{'=' * 80}")
    print(f"Checking for ESTIMATE updates - {datetime.now(timezone.utc).isoformat()}")
    print(f"{'=' * 80}\n")

    # Get last poll time for estimates
    last_poll = state_manager.get_last_estimate_poll_time()
    print(f"Last estimate poll: {last_poll.isoformat()}")

    # Get updated estimates
    estimates = await sf_client.get_updated_estimates(since=last_poll)

    print(f"Found {len(estimates)} updated estimate(s)\n")

    # Track sync errors
    sync_errors = []

    # Sync each estimate
    for idx, estimate in enumerate(estimates, 1):
        try:
            await sync_work_order_to_ghl(estimate, idx, work_type="Estimate")

        except Exception as e:
            print(f"  Failed to sync estimate {estimate.id}: {e}")
            sync_errors.append(
                {
                    "estimate_id": estimate.id,
                    "estimate_number": estimate.number,
                    "error": str(e),
                }
            )

    # Batch notify about failures
    if sync_errors:
        await slack_notifier.send_error(
            error=Exception(f"{len(sync_errors)} estimates failed to sync"),
            function_name="check_for_estimate_updates",
            severity=ErrorSeverity.MEDIUM,
            context={
                "total_estimates": len(estimates),
                "failed_count": len(sync_errors),
                "failed_estimates": sync_errors[:5],
            },
        )

    # Update state
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    state_manager.save_last_estimate_poll_time(now)

    # Update stats
    stats = state_manager.get_stats()
    state_manager.update_stats(
        total_estimate_checks=stats.get("total_estimate_checks", 0) + 1,
        total_estimate_updates_found=stats.get("total_estimate_updates_found", 0)
        + len(estimates),
    )

    # Summary
    print(f"{'=' * 80}")
    if estimates:
        success_count = len(estimates) - len(sync_errors)
        print(f"Synced {success_count}/{len(estimates)} estimate update(s)")
        if sync_errors:
            print(f"{len(sync_errors)} error(s) occurred - check Slack for details")
    else:
        print("No estimate updates found")
    print(f"{'=' * 80}\n")


# ============================================================================
# FastAPI App
# ============================================================================

# Scheduler instance
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown"""

    # Single job that runs all syncs sequentially
    async def run_all_syncs():
        await check_for_customer_updates()
        await check_for_estimate_updates()  # Estimates first!
        await check_for_job_updates()  # Jobs after

    scheduler.add_job(
        run_all_syncs,
        "interval",
        minutes=settings.sync_interval_minutes,
        id="run_all_syncs",
    )

    scheduler.start()
    print("Scheduler started\n")

    # Run immediately on startup
    await run_all_syncs()

    yield

    scheduler.shutdown()
    print("\nScheduler stopped")


app = FastAPI(
    title="Service Fusion → GoHighLevel Sync",
    description="Syncs customers and jobs from Service Fusion to GoHighLevel",
    version="1.0.0",
    lifespan=lifespan,
)


# ============================================================================
# API Endpoints
# ============================================================================


@app.get("/")
async def root():
    """Root endpoint with status"""
    stats = state_manager.get_stats()

    return {
        "status": "running",
        "interval_minutes": settings.sync_interval_minutes,
        "stats": stats,
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "ok"}


@app.get("/test-conversion")
async def test_conversion():
    """Test estimate → job conversion tracking"""

    # Get the converted job
    job = await sf_client.get_job_by_id(1079024772)

    # Get the original estimate
    estimate = await sf_client.get_estimate_by_id(1079024244)

    return {
        "job": job,
        "estimate": estimate,
    }


@app.get("/stats")
async def get_stats():
    """Get sync statistics"""
    stats = state_manager.get_stats()
    return {"state_file": settings.state_file_path, "stats": stats}


@app.post("/upload_contact_to_service_fusion")
async def upload_contact_to_service_fusion(request: Request):
    data = await request.json()
    print(data)

    email = data.get("email")

    phone = data.get("phone")

    # Clean phone number for search

    clean_phone = None

    if phone:
        clean_phone = phone.lstrip("+")

        if len(clean_phone) == 11 and clean_phone.startswith("1"):
            clean_phone = clean_phone[1:]

    # Check if customer already exists

    existing_customer = await sf_client.find_customer_by_email_or_phone(
        email, clean_phone
    )

    if existing_customer:
        print(f"Customer already exists: {existing_customer.get('id')}")

        return {
            "status": "exists",
            "message": "Customer already exists in Service Fusion",
            "service_fusion": existing_customer,
        }

    # Build contact with proper handling of optional fields

    contact = {
        "fname": data.get("first_name", ""),
        "lname": data.get("last_name", ""),
        "is_primary": True,
    }

    # Only add phones if we have a phone number

    if clean_phone:
        contact["phones"] = [{"phone": clean_phone, "type": "Mobile"}]

    # Only add emails if we have an email

    if email:
        contact["emails"] = [{"email": email, "class": "Business"}]

    # Map GHL → SF customer payload

    sf_payload = {
        "customer_name": f"{data.get('first_name', '')} {data.get('last_name', '')}".strip(),
        "contacts": [contact],
    }

    # Only add locations if GHL provides one

    street = data.get("address1", "") or data.get("Contact Street Address", "")
    city = data.get("city", "") or data.get("Contact City", "")
    state = data.get("state", "") or data.get("Contact State", "")
    postal = data.get("postal_code", "") or data.get("Contact Postal Code", "")
    country = data.get("country", "US")
    # Only build address if there is at least a street or city
    if street or city:
        sf_payload["locations"] = [
            {
                "street_1": street,
                "city": city,
                "state_prov": state,
                "postal_code": postal,
                "country": country,
                "is_primary": True,
            }
        ]

    try:
        created = await sf_client.create_customer(sf_payload)

        return {"status": "created", "service_fusion": created}

    except httpx.HTTPStatusError as e:
        print(f"SF Error Response: {e.response.text}")

        raise HTTPException(status_code=500, detail=str(e))

    except Exception as e:
        print(e)

        raise HTTPException(status_code=500, detail=str(e))


"""
Unified endpoint that replaces both:
- /upload_contact_to_service_fusion
- /create_job_in_service_fusion

This handles both scenarios:
1. Contact created → Sync to SF as customer only
2. Contact created + appointment booked → Sync customer + create job

Add this to main.py and remove the old endpoints.
"""


"""
Fixed unified endpoint with proper dict handling
Replace the previous version with this one
"""


@app.post("/sync_ghl_to_service_fusion")
async def sync_ghl_to_service_fusion(request: Request):
    """
    Sync GHL contact to Service Fusion.

    - Always creates/finds customer
    - Always creates a scheduled job
    - No appointment time lookup (simplified)
    """
    data = await request.json()
    print(f"Received sync request for: {data.get('full_name')}")

    email = data.get("email")
    phone = data.get("phone")

    # Clean phone number
    clean_phone = None
    if phone:
        clean_phone = phone.lstrip("+")
        if len(clean_phone) == 11 and clean_phone.startswith("1"):
            clean_phone = clean_phone[1:]

    # ========================================================================
    # STEP 1: Find or create customer in Service Fusion
    # ========================================================================
    customer = None
    sf_customer_id = data.get("sf_customer_id")

    try:
        # Try to use existing SF customer ID
        if sf_customer_id:
            print(f"Using existing SF customer ID: {sf_customer_id}")
            customer_obj = await sf_client.get_customer_by_id(int(sf_customer_id))
            customer = customer_obj.model_dump() if customer_obj else None

        # Search by email/phone if no ID or customer not found
        if not customer:
            customer = await sf_client.find_customer_by_email_or_phone(
                email, clean_phone
            )

        # Create new customer if still not found
        if not customer:
            print("Customer not found, creating new customer...")

            contact = {
                "fname": data.get("first_name", ""),
                "lname": data.get("last_name", ""),
                "is_primary": True,
            }

            if clean_phone:
                contact["phones"] = [{"phone": clean_phone, "type": "Mobile"}]

            if email:
                contact["emails"] = [{"email": email, "class": "Business"}]

            customer_payload = {
                "customer_name": f"{data.get('first_name', '')} {data.get('last_name', '')}".strip(),
                "contacts": [contact],
            }

            # Add location if available
            street = data.get("address1", "") or data.get("Contact Street Address", "")
            city = data.get("city", "") or data.get("Contact City", "")
            state = data.get("state", "") or data.get("Contact State", "")
            postal = data.get("postal_code", "") or data.get("Contact Postal Code", "")

            if street or city:
                customer_payload["locations"] = [
                    {
                        "street_1": street,
                        "city": city,
                        "state_prov": state,
                        "postal_code": postal,
                        "country": data.get("country", "US"),
                        "is_primary": True,
                    }
                ]

            customer = await sf_client.create_customer(customer_payload)
            print(f"Created new customer: {customer.get('id')}")
        else:
            print(f"Found existing customer: {customer.get('id')}")

    except Exception as e:
        await slack_notifier.send_error(
            error=e,
            function_name="sync_ghl_to_service_fusion - customer_creation",
            severity=ErrorSeverity.CRITICAL,
            context={
                "email": email,
                "phone": clean_phone,
                "name": data.get("full_name"),
            },
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to find/create customer: {str(e)}"
        )

    # ========================================================================
    # STEP 2: Create the job in Service Fusion
    # ========================================================================
    created_job = None
    customer_name = customer.get("customer_name") or customer.get(
        "fully_qualified_name"
    )

    try:
        # Get service description from custom field
        service_needed = data.get("customData", {}).get("service_needed", "")
        if not service_needed:
            service_needed = data.get("Service Needed", "Service request from GHL")

        # Build job payload (no times, just scheduled status)
        job_payload = {
            "customer_name": customer_name,
            "description": service_needed,
            "status": "Scheduled",
        }

        # Add location from customer data if available
        street = data.get("address1", "") or data.get("Contact Street Address", "")
        city = data.get("city", "") or data.get("Contact City", "")
        state = data.get("state", "") or data.get("Contact State", "")
        postal = data.get("postal_code", "") or data.get("Contact Postal Code", "")

        if street:
            job_payload["street_1"] = street
        if city:
            job_payload["city"] = city
        if state:
            job_payload["state_prov"] = state
        if postal:
            job_payload["postal_code"] = postal

        # Add contact info to job
        if data.get("first_name"):
            job_payload["contact_first_name"] = data.get("first_name")
        if data.get("last_name"):
            job_payload["contact_last_name"] = data.get("last_name")

        # Build notes with all relevant info
        notes = []

        # Add service needed to notes
        if service_needed and service_needed != "Service request from GHL":
            notes.append(f"Service needed: {service_needed}")

        # Add any additional details from custom fields
        if note := data.get("Note"):
            notes.append(f"Customer note: {note}")

        if additional_details := data.get("Additional details"):
            notes.append(f"Details: {additional_details}")

        if caller_inquiry := data.get("Caller Service Inquiry"):
            notes.append(f"Service inquiry: {caller_inquiry}")

        # Add appointment info if available in webhook data
        if appointment_start := data.get("Appointment Start Date"):
            notes.append(f"Requested date: {appointment_start}")

        if appointment_time := data.get("Appointment Start Time"):
            notes.append(f"Requested time: {appointment_time}")

        if notes:
            job_payload["notes"] = [{"notes": "\n".join(notes)}]

        # Log payload for debugging
        import json

        print("Job Payload:", json.dumps(job_payload, indent=2, default=str))

        # Create the job
        created_job = await sf_client.create_job(job_payload)

        print(
            f"SUCCESS - Created job: {created_job.get('id')} - {created_job.get('number')}"
        )

    except httpx.HTTPStatusError as e:
        await slack_notifier.send_error(
            error=e,
            function_name="sync_ghl_to_service_fusion - job_creation",
            severity=ErrorSeverity.HIGH,
            context={
                "customer_id": customer.get("id") if customer else None,
                "customer_name": customer_name,
                "status_code": e.response.status_code,
                "response": e.response.text[:500]
                if hasattr(e.response, "text")
                else str(e),
            },
        )
        # Don't raise - return success with customer but note job failed
        print(f"Job creation failed but customer was created: {e}")

    except Exception as e:
        await slack_notifier.send_error(
            error=e,
            function_name="sync_ghl_to_service_fusion - job_creation",
            severity=ErrorSeverity.HIGH,
            context={
                "customer_id": customer.get("id") if customer else None,
                "customer_name": customer_name,
            },
        )
        print(f"Job creation failed but customer was created: {e}")

    # ========================================================================
    # Return results
    # ========================================================================
    response = {
        "status": "success",
        "customer_id": customer.get("id") if customer else None,
        "customer_name": customer_name,
        "customer_created": sf_customer_id is None,
    }

    if created_job:
        response["job_created"] = True
        response["job_id"] = created_job.get("id")
        response["job_number"] = created_job.get("number")
    else:
        response["job_created"] = False
        response["reason"] = "Job creation failed"

    return response


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=True)
