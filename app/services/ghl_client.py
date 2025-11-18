"""
GoHighLevel API Client
Handles API requests to GoHighLevel for contact and opportunity management.
"""

import httpx
from typing import Optional
from app.config import settings
from app.models import GHLContact


class GoHighLevelClient:
    """Client for GoHighLevel API"""

    def __init__(self):
        self.api_token = settings.ghl_api_token
        self.location_id = settings.ghl_location_id
        self.base_url = "https://services.leadconnectorhq.com"

    def _headers(self) -> dict:
        """Get headers for API requests"""
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "Version": "2021-07-28",
        }

    # ========================================================================
    # Contact Methods
    # ========================================================================

    async def search_contact_by_sf_id(self, sf_customer_id: int) -> Optional[dict]:
        """
        Search for contact by SF customer ID custom field.

        Args:
            sf_customer_id: Service Fusion customer ID

        Returns:
            Contact dict if found, None otherwise
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/contacts/",
                headers=self._headers(),
                params={
                    "locationId": self.location_id,
                    f"customField.sf_customer_id": str(sf_customer_id),
                },
            )
            response.raise_for_status()
            data = response.json()
            contacts = data.get("contacts", [])
            return contacts[0] if contacts else None

    async def search_contact_by_phone(self, phone: str) -> Optional[dict]:
        """
        Search for contact by phone number.

        Args:
            phone: Phone number

        Returns:
            Contact dict if found, None otherwise
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/contacts/",
                headers=self._headers(),
                params={
                    "locationId": self.location_id,
                    "query": phone,
                },
            )
            response.raise_for_status()
            data = response.json()
            contacts = data.get("contacts", [])
            return contacts[0] if contacts else None

    async def update_contact_custom_field(
        self, contact_id: str, field_id: str, value: str
    ) -> dict:
        """
        Update a custom field on a contact.

        Args:
            contact_id: GHL contact ID
            field_id: Custom field ID
            value: Value to set

        Returns:
            Updated contact data
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(
                f"{self.base_url}/contacts/{contact_id}",
                headers=self._headers(),
                json={"customFields": [{"id": field_id, "value": value}]},
            )
            response.raise_for_status()
            return response.json()

    async def upsert_contact(self, contact_data: dict) -> GHLContact:
        """
        Create or update contact using upsert endpoint.
        GHL will match by email/phone based on duplicate settings.

        Args:
            contact_data: Contact data dictionary

        Returns:
            Created/updated GHLContact
        """
        contact_data["locationId"] = self.location_id
        print(contact_data)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/contacts/upsert",
                headers=self._headers(),
                json=contact_data,
            )
            print(response)

            if response.status_code > 299:
                print(f"GHL Error Response: {response.text}")

            response.raise_for_status()
            return GHLContact(**response.json().get("contact", response.json()))

    # ========================================================================
    # Opportunity Methods
    # ========================================================================
    async def search_opportunity_by_job_id(
        self, sf_job_id: int, contact_id: str
    ) -> Optional[dict]:
        """
        Search for opportunity by SF job ID custom field.

        Since GHL doesn't support filtering by custom fields, we:
        1. Get all opportunities for the contact
        2. Filter client-side by crm_job_id custom field

        Args:
            sf_job_id: Service Fusion job ID
            contact_id: GHL contact ID to narrow search

        Returns:
            Opportunity dict if found, None otherwise
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get all opportunities for this contact
            response = await client.get(
                f"{self.base_url}/opportunities/search",
                headers=self._headers(),
                params={
                    "location_id": self.location_id,
                    "contact_id": contact_id,
                },
            )
            response.raise_for_status()
            data = response.json()
            opportunities = data.get("opportunities", [])

            print(f"      DEBUG - Searching for crm_job_id: {sf_job_id}")
            print(
                f"      DEBUG - Found {len(opportunities)} opportunities for contact"
            )

            # Filter by custom field client-side
            crm_job_id_field = settings.ghl_opportunity_crm_job_id_field

            for opp in opportunities:
                custom_fields = opp.get("customFields", [])

                for field in custom_fields:
                    # GHL returns: {'id': '...', 'type': 'string', 'fieldValueString': '...'}
                    field_id = field.get("id")
                    field_value = field.get("fieldValueString") or field.get("value")

                    if field_id == crm_job_id_field and field_value == str(sf_job_id):
                        print(f"      MATCH FOUND: {opp.get('name')}")
                        return opp

            print(f"      No match found for crm_job_id: {sf_job_id}")
            return None

    async def upsert_opportunity(self, opportunity_data: dict) -> dict:
        """
        Create or update opportunity using upsert endpoint.

        Args:
            opportunity_data: Opportunity data dictionary

        Returns:
            Created/updated opportunity
        """
        # Ensure required fields
        opportunity_data["locationId"] = self.location_id
        if "pipelineId" not in opportunity_data:
            opportunity_data["pipelineId"] = settings.ghl_pipeline_id

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/opportunities/upsert",
                headers=self._headers(),
                json=opportunity_data,
            )

            if response.status_code not in [200, 201]:
                print(f"GHL Error: {response.text}")

            response.raise_for_status()
            return response.json()

    async def search_contact_by_email(self, email: str) -> Optional[dict]:
        """
        Search for contact by email address.

        Args:
            email: Email address

        Returns:
            Contact dict if found, None otherwise
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/contacts/",
                headers=self._headers(),
                params={
                    "locationId": self.location_id,
                    "query": email,
                },
            )
            response.raise_for_status()
            data = response.json()
            contacts = data.get("contacts", [])
            return contacts[0] if contacts else None

    async def create_opportunity(self, opportunity_data: dict) -> dict:
        """
        Create a new opportunity using POST (not upsert).

        Args:
            opportunity_data: Opportunity data (contactId, pipelineId, etc.)

        Returns:
            Created opportunity object
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Build the request payload
            payload = {
                "locationId": settings.ghl_location_id,
                **opportunity_data,
            }

            try:
                response = await client.post(
                    f"{self.base_url}/opportunities/",
                    headers={
                        "Authorization": f"Bearer {settings.ghl_api_token}",
                        "Version": "2021-07-28",
                    },
                    json=payload,
                )
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as e:
                print(f"  GHL API Error: {e.response.status_code}")
                print(f"  Response: {e.response.text}")
                raise

    async def update_opportunity_custom_field(
        self, opportunity_id: str, field_key: str, field_value: str
    ) -> dict:
        """
        Update a custom field on an opportunity.

        Args:
            opportunity_id: GHL opportunity ID
            field_key: Custom field key (e.g., "crm_job_id")
            field_value: New value for the field

        Returns:
            Updated opportunity object
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.put(
                    f"{self.base_url}/opportunities/{opportunity_id}",
                    headers={
                        "Authorization": f"Bearer {settings.ghl_api_token}",
                        "Version": "2021-07-28",
                    },
                    json={
                        "customFields": [
                            {
                                "key": field_key,
                                "value": field_value,
                            }
                        ]
                    },
                )
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as e:
                print(
                    f"  GHL API Error updating custom field: {e.response.status_code}"
                )
                print(f"  Response: {e.response.text}")
                raise

    async def update_opportunity(self, opportunity_id: str, update_data: dict) -> dict:
        """
        Update an existing opportunity using PUT.

        Args:
            opportunity_id: GHL opportunity ID
            update_data: Fields to update (e.g., pipelineStageId, status)

        Returns:
            Updated opportunity object
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.put(
                    f"{self.base_url}/opportunities/{opportunity_id}",
                    headers={
                        "Authorization": f"Bearer {settings.ghl_api_token}",
                        "Version": "2021-07-28",
                    },
                    json=update_data,
                )
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as e:
                print(
                    f"  GHL API Error updating opportunity: {e.response.status_code}"
                )
                print(f"  Response: {e.response.text}")
                raise


# Global client instance
ghl_client = GoHighLevelClient()
