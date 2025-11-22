"""
Service Fusion API Client

Handles authentication and API requests to Service Fusion.
Returns typed Pydantic models for type safety.
"""

import httpx
import time
from datetime import datetime
from typing import Optional
from app.config import settings
from app.models import (
    SFCustomer,
    SFCustomersResponse,
    SFJob,
    SFEstimate,
    SFEstimatesResponse,
    SFJobsResponse,
)

from app.error_handler import (
    ErrorSeverity,
    slack_notifier,
)


class ServiceFusionClient:
    """Client for Service Fusion API with OAuth authentication"""

    def __init__(self):
        self.client_id = settings.sf_client_id
        self.client_secret = settings.sf_client_secret
        self.base_url = settings.sf_api_base_url

        # Token management
        self.access_token: Optional[str] = None
        self.token_expiry: float = 0

    async def get_jobs(
        self, page: int = 1, per_page: int = 50, sort: str = "-updated_at"
    ) -> SFJobsResponse:
        """
        Get jobs from Service Fusion.

        Args:
            page: Page number (1-indexed)
            per_page: Results per page (max 50)
            sort: Sort order (use '-updated_at' for newest first)

        Returns:
            SFJobsResponse: Parsed response with jobs
        """
        token = await self.get_token()

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/v1/jobs",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "page": page,
                    "per_page": per_page,
                    "sort": sort,
                },
            )
            response.raise_for_status()

            # Parse response with Pydantic
            return SFJobsResponse(**response.json())

    async def get_token(self) -> Optional[str]:
        """
        Get OAuth access token (cached until expiry).

        Returns:
            str: Access token
        """
        # Return cached token if still valid
        if self.access_token and time.time() < self.token_expiry:
            return self.access_token

        # Request new token
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/oauth/access_token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
            response.raise_for_status()
            data = response.json()

            # Cache token (refresh 5 minutes before expiry)
            self.access_token = data["access_token"]
            self.token_expiry = time.time() + int(data["expires_in"]) - 300

            print(f"Got new SF access token (expires in {data['expires_in']}s)")
            return self.access_token

    async def get_customers(
        self, page: int = 1, per_page: int = 50, sort: str = "-updated_at"
    ) -> SFCustomersResponse:
        """
        Get customers from Service Fusion.

        Args:
            page: Page number (1-indexed)
            per_page: Results per page (max 50)
            sort: Sort order (use '-updated_at' for newest first)

        Returns:
            SFCustomersResponse: Parsed response with customers
        """
        token = await self.get_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/v1/customers",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "page": page,
                    "per_page": per_page,
                    "sort": sort,
                    "expand": "contacts.phones,contacts.emails,locations",
                },
            )
            response.raise_for_status()
            print(SFCustomersResponse(**response.json()))
            # Parse response with Pydantic
            return SFCustomersResponse(**response.json())

    async def get_updated_customers(
        self, since: datetime, max_results: int = 100
    ) -> list[SFCustomer]:
        """
        Get customers updated since a specific datetime.

        Args:
            since: Only return customers updated after this time (timezone-naive UTC)
            max_results: Maximum number of customers to return

        Returns:
            list[SFCustomer]: List of updated customers

        Raises:
            httpx.HTTPStatusError: If Service Fusion API returns error
            httpx.TimeoutError: If request times out
            httpx.NetworkError: If network connection fails

        Note:
            Customers with bad data (invalid timestamps, missing fields) are skipped
            and reported via Slack notification in a batch.
        """
        print(f"DEBUG: Looking for customers updated after {since.isoformat()}")
        updated_customers = []
        bad_customers = []  # Track customers with data quality issues
        page = 1

        while len(updated_customers) < max_results:
            # Get page of customers (sorted newest first)
            # Note: Network/API errors will propagate up to the caller
            response = await self.get_customers(page=page, per_page=50)

            if not response.items:
                # No more customers
                break

            # Check each customer
            for customer in response.items:
                try:
                    # Parse customer's updated_at timestamp
                    print(
                        f"DEBUG: Customer {customer.id} RAW updated_at={customer.updated_at}"
                    )
                    customer_updated = customer.updated_at_datetime
                    # ↑ This can raise AttributeError or ValueError if data is bad

                    print(
                        f"DEBUG: Customer {customer.id} {customer} updated={customer_updated.isoformat()}, newer={customer_updated > since}"
                    )
                    # If customer is newer than our cutoff, include it
                    if customer_updated > since:
                        updated_customers.append(customer)
                    else:
                        # Since results are sorted by -updated_at (newest first),
                        # once we hit an older customer, we can stop

                        # Send batch notification before returning (if any bad data found)
                        if bad_customers:
                            await self._notify_bad_customers(bad_customers)

                        return updated_customers

                except (AttributeError, ValueError, TypeError) as e:
                    # Data quality issue with this specific customer
                    # Log it, add to batch, and continue processing other customers
                    print(
                        f"Skipping customer {customer.id} - bad data: {type(e).__name__}: {e}"
                    )

                    bad_customers.append(
                        {
                            "customer_id": customer.id,
                            "customer_name": f"{getattr(customer, 'first_name', '')} {getattr(customer, 'last_name', '')}".strip()
                            or "Unknown",
                            "error": str(e),
                            "error_type": type(e).__name__,
                            "page": page,
                        }
                    )

                    # Continue to next customer - don't let one bad record stop everything
                    continue

            # If we got fewer results than per_page, we've reached the end
            if len(response.items) < 50:
                break

            page += 1

        # Send batch notification if any bad customers found
        if bad_customers:
            await self._notify_bad_customers(bad_customers)

        return updated_customers[:max_results]

    async def _notify_bad_customers(self, bad_customers: list):
        """
        Send batched notification about customers with data quality issues.

        Args:
            bad_customers: List of dicts with customer_id, error, etc.
        """
        from app.error_handler import slack_notifier, ErrorSeverity

        error_summary = f"Found {len(bad_customers)} customer(s) with data quality issues during sync"

        # Limit context size to avoid huge Slack messages
        context = {
            "count": len(bad_customers),
            "customers": bad_customers[:10],  # Show first 10 examples
            "recommendation": "Check Service Fusion data quality for these customers",
        }

        if len(bad_customers) > 10:
            context["note"] = (
                f"Showing first 10 of {len(bad_customers)} total bad records"
            )

        await slack_notifier.send_error(
            error=Exception(error_summary),
            function_name="get_updated_customers",
            severity=ErrorSeverity.LOW,  # Data quality issue, not system failure
            context=context,
        )

    # In ServiceFusionClient class
    async def get_customer_by_id(self, customer_id: int) -> Optional[SFCustomer]:
        """
        Get a specific customer by ID from Service Fusion.

        Args:
            customer_id: The customer ID to fetch

        Returns:
            SFCustomer if found, None otherwise
        """
        token = await self.get_token()

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    f"{self.base_url}/v1/customers/{customer_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    params={
                        "expand": "contacts.phones,contacts.emails,locations",
                    },
                )
                response.raise_for_status()

                # Parse single customer response
                customer_data = response.json()
                return SFCustomer(**customer_data)

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    # Customer doesn't exist in SF
                    return None
                raise

    async def get_updated_jobs(
        self, since: datetime, max_results: int = 100
    ) -> list[SFJob]:
        """
        Get jobs updated since a specific datetime.

        Args:
            since: Only return jobs updated after this time (timezone-naive UTC)
            max_results: Maximum number of jobs to return

        Returns:
            list[SFJob]: List of updated jobs

        Raises:
            httpx.HTTPStatusError: If Service Fusion API returns error
            httpx.TimeoutError: If request times out
            httpx.NetworkError: If network connection fails

        Note:
            Jobs with bad data (invalid timestamps, missing fields) are skipped
            and reported via Slack notification in a batch.
        """
        updated_jobs = []
        bad_jobs = []  # Track jobs with data quality issues
        page = 1

        while len(updated_jobs) < max_results:
            # Get page of jobs (sorted newest first)
            # Note: Network/API errors will propagate up to the caller
            response = await self.get_jobs(page=page, per_page=50)

            if not response.items:
                # No more jobs
                break

            # Check each job
            for job in response.items:
                try:
                    # Parse job's updated_at timestamp
                    job_updated = job.updated_at_datetime
                    # ↑ This can raise AttributeError or ValueError if data is bad

                    # If job is newer than our cutoff, include it
                    if job_updated > since:
                        updated_jobs.append(job)
                    else:
                        # Since results are sorted by -updated_at (newest first),
                        # once we hit an older job, we can stop

                        # Send batch notification before returning (if any bad data found)
                        if bad_jobs:
                            await self._notify_bad_jobs(bad_jobs)

                        return updated_jobs

                except (AttributeError, ValueError, TypeError) as e:
                    # Data quality issue with this specific job
                    # Log it, add to batch, and continue processing other jobs
                    print(f"Skipping job {job.id} - bad data: {type(e).__name__}: {e}")

                    bad_jobs.append(
                        {
                            "job_id": job.id,
                            "job_number": getattr(job, "number", "Unknown"),
                            "customer_name": getattr(job, "customer_name", "Unknown"),
                            "error": str(e),
                            "error_type": type(e).__name__,
                            "page": page,
                        }
                    )

                    # Continue to next job - don't let one bad record stop everything
                    continue

            # If we got fewer results than per_page, we've reached the end
            if len(response.items) < 50:
                break

            page += 1

        # Send batch notification if any bad jobs found
        if bad_jobs:
            await self._notify_bad_jobs(bad_jobs)

        return updated_jobs[:max_results]

    async def _notify_bad_jobs(self, bad_jobs: list):
        """
        Send batched notification about jobs with data quality issues.

        Args:
            bad_jobs: List of dicts with job_id, error, etc.
        """
        from app.error_handler import slack_notifier, ErrorSeverity

        error_summary = (
            f"Found {len(bad_jobs)} job(s) with data quality issues during sync"
        )

        # Limit context size to avoid huge Slack messages
        context = {
            "count": len(bad_jobs),
            "jobs": bad_jobs[:10],  # Show first 10 examples
            "recommendation": "Check Service Fusion data quality for these jobs",
        }

        if len(bad_jobs) > 10:
            context["note"] = f"Showing first 10 of {len(bad_jobs)} total bad records"

        await slack_notifier.send_error(
            error=Exception(error_summary),
            function_name="get_updated_jobs",
            severity=ErrorSeverity.LOW,  # Data quality issue, not system failure
            context=context,
        )

    # In sf_client.py, get_estimates method
    async def get_estimates(
        self, page: int = 1, per_page: int = 50, sort: str = "-updated_at"
    ) -> SFEstimatesResponse:
        """Get estimates from Service Fusion."""
        token = await self.get_token()

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/v1/estimates",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "page": page,
                    "per_page": per_page,
                    "sort": sort,
                },
            )
            response.raise_for_status()

            # DEBUG: Print raw response
            raw_data = response.json()

            # Parse response with Pydantic
            return SFEstimatesResponse(**raw_data)

    async def get_updated_estimates(
        self, since: datetime, max_results: int = 100
    ) -> list[SFEstimate]:
        """
        Get estimates updated since a specific datetime.

        Args:
            since: Only return estimates updated after this time (timezone-naive UTC)
            max_results: Maximum number of estimates to return

        Returns:
            list[SFEstimate]: List of updated estimates
        """
        updated_estimates = []
        bad_estimates = []  # Track estimates with data quality issues
        page = 1

        while len(updated_estimates) < max_results:
            # Get page of estimates (sorted newest first)
            response = await self.get_estimates(page=page, per_page=50)

            if not response.items:
                break

            # Check each estimate
            for estimate in response.items:
                try:
                    estimate_updated = estimate.updated_at_datetime

                    if estimate_updated > since:
                        updated_estimates.append(estimate)
                    else:
                        # Send batch notification before returning
                        if bad_estimates:
                            await self._notify_bad_estimates(bad_estimates)

                        return updated_estimates

                except (AttributeError, ValueError, TypeError) as e:
                    print(
                        f"Skipping estimate {estimate.id} - bad data: {type(e).__name__}: {e}"
                    )

                    bad_estimates.append(
                        {
                            "estimate_id": estimate.id,
                            "estimate_number": getattr(estimate, "number", "Unknown"),
                            "customer_name": getattr(
                                estimate, "customer_name", "Unknown"
                            ),
                            "error": str(e),
                            "error_type": type(e).__name__,
                            "page": page,
                        }
                    )
                    continue

            if len(response.items) < 50:
                break

            page += 1

        if bad_estimates:
            await self._notify_bad_estimates(bad_estimates)

        return updated_estimates[:max_results]

    async def _notify_bad_estimates(self, bad_estimates: list):
        """Send batched notification about estimates with data quality issues"""
        from app.error_handler import slack_notifier, ErrorSeverity

        error_summary = f"Found {len(bad_estimates)} estimate(s) with data quality issues during sync"

        context = {
            "count": len(bad_estimates),
            "estimates": bad_estimates[:10],
            "recommendation": "Check Service Fusion data quality for these estimates",
        }

        if len(bad_estimates) > 10:
            context["note"] = (
                f"Showing first 10 of {len(bad_estimates)} total bad records"
            )

        await slack_notifier.send_error(
            error=Exception(error_summary),
            function_name="get_updated_estimates",
            severity=ErrorSeverity.LOW,
            context=context,
        )

    async def get_job_by_id(self, job_id: int) -> Optional[SFJob]:
        """
        Get a specific job by ID from Service Fusion.

        Args:
            job_id: The job ID to fetch

        Returns:
            SFJob if found, None otherwise
        """
        token = await self.get_token()

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    f"{self.base_url}/v1/jobs/{job_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()

                # DEBUG: Print raw response to see ALL fields
                job_data = response.json()
                print("DEBUG - Raw job response:")
                import json

                print(json.dumps(job_data, indent=2))

                return SFJob(**job_data)

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    return None
                raise

    async def get_estimate_by_id(self, estimate_id: int) -> Optional[SFEstimate]:
        """
        Get a specific estimate by ID from Service Fusion.

        Args:
            estimate_id: The estimate ID to fetch

        Returns:
            SFEstimate if found, None otherwise
        """
        token = await self.get_token()

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    f"{self.base_url}/v1/estimates/{estimate_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()

                # DEBUG: Print raw response to see ALL fields
                estimate_data = response.json()
                print("DEBUG - Raw estimate response:")
                import json

                print(json.dumps(estimate_data, indent=2))

                return SFEstimate(**estimate_data)

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    return None
                raise

    async def create_customer(self, customer_data: dict):
        token = await self.get_token()

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/v1/customers",
                headers={"Authorization": f"Bearer {token}"},
                json=customer_data,
            )

            response.raise_for_status()

            return response.json()

    async def find_customer_by_email_or_phone(
        self, email: str | None, phone: str | None
    ):
        """Search SF for a customer by email or phone."""

        token = await self.get_token()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                params = {
                    "expand": "contacts.phones,contacts.emails,locations",
                    "per-page": 50,
                }

                if email:
                    params["filters[email]"] = email

                if phone:
                    params["filters[phone]"] = phone

                response = await client.get(
                    f"{self.base_url}/v1/customers",
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                )

                response.raise_for_status()

                data = response.json()

                items = data.get("items", [])

                if not items:
                    return None

                # Return FIRST match

                return items[0]

        except Exception as e:
            await slack_notifier.send_error(
                error=e,
                function_name="find_customer_by_email_or_phone",
                severity=ErrorSeverity.CRITICAL,
                context={"email": email, "phone": phone},
            )

            raise

    async def create_job(self, job_data: dict):
        """
        Create a new job in Service Fusion.

        Args:
            job_data: Job payload (must include customer_name, description, and status)

        Returns:
            Created job data

        Raises:
            httpx.HTTPStatusError: If Service Fusion API returns error
        """
        try:
            token = await self.get_token()

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/v1/jobs",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=job_data,
                )
                response.raise_for_status()
                return response.json()

        except httpx.HTTPStatusError as e:
            print(f"Service Fusion job creation failed: {e.response.status_code}")
            print(f"Response: {e.response.text}")
            raise

        except Exception as e:
            print(f"Unexpected error creating job: {e}")
            raise


sf_client = ServiceFusionClient()
