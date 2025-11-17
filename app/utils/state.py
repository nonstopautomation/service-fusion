"""
State Management
Simple JSON file-based state persistence for sync timestamps.
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from app.error_handler import slack_notifier, ErrorSeverity
import asyncio


class StateManager:
    """Manages sync state using a JSON file"""

    def __init__(self, file_path: str = "sync_state.json"):
        self.file_path = Path(file_path)

    def get_last_customer_poll_time(self) -> datetime:
        """
        Get the last time we polled Service Fusion for customers.

        Returns:
            datetime: Last poll time (timezone-naive UTC)

        Falls back to 24 hours ago if state file is corrupted.
        """
        try:
            # First run - no file exists yet
            if not self.file_path.exists():
                first_run = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
                    hours=24
                )
                return first_run

            # Read state file
            with open(self.file_path, "r") as f:
                data = json.load(f)

            last_poll_str = data.get("last_sf_poll")

            # Missing field - use fallback
            if not last_poll_str:
                return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
                    hours=24
                )

            # Parse and return
            return datetime.fromisoformat(last_poll_str)

        except (PermissionError, json.JSONDecodeError, ValueError) as e:
            asyncio.create_task(
                slack_notifier.send_error(
                    error=e,
                    function_name="get_last_poll_time",
                    severity=ErrorSeverity.LOW,  # Low because we have a fallback
                    context={
                        "file_path": str(self.file_path),
                        "fallback": "Using 24 hours ago. Something wrong with state file. Using 24 hours ago as a fallback. So Low Severity",
                        "action": "Check file permissions and JSON validity",
                    },
                )
            )

            # Return safe fallback
            return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)

    def save_last_poll_time(self, dt: Optional[datetime] = None) -> None:
        """
        Save the last customer poll time to state file.

        Args:
            dt: Datetime to save (timezone-naive UTC)
                If None, uses current time
        """
        if dt is None:
            dt = datetime.now(timezone.utc).replace(tzinfo=None)

        # Load existing state
        if self.file_path.exists():
            with open(self.file_path, "r") as f:
                data = json.load(f)
        else:
            data = {}

        # Update last poll time
        data["last_sf_poll"] = dt.isoformat()

        # Save back to file
        with open(self.file_path, "w") as f:
            json.dump(data, f, indent=2)

    def get_last_job_poll_time(self) -> datetime:
        """
        Get the last time we polled Service Fusion for jobs.

        Returns:
            datetime: Last poll time (timezone-naive UTC)

        Falls back to 24 hours ago if state file has issues.
        Sends Slack notification if fallback is used.
        """
        try:
            if not self.file_path.exists():
                first_run = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
                    hours=24
                )
                print(
                    f"🔵 First job poll - checking updates since {first_run.isoformat()}"
                )
                return first_run

            with open(self.file_path, "r") as f:
                data = json.load(f)

            last_poll_str = data.get("last_job_poll")

            if not last_poll_str:
                print("⚠️  State file missing 'last_job_poll', using 24h fallback")
                return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
                    hours=24
                )

            return datetime.fromisoformat(last_poll_str)

        except (PermissionError, json.JSONDecodeError, ValueError) as e:
            # Notify async (don't block)
            asyncio.create_task(
                slack_notifier.send_error(
                    error=e,
                    function_name="get_last_job_poll_time",
                    severity=ErrorSeverity.LOW,
                    context={
                        "file_path": str(self.file_path),
                        "fallback_used": "24 hours ago",
                        "impact": "May re-sync some recent jobs",
                    },
                )
            )

            # Return safe default
            return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)

    def save_last_job_poll_time(self, dt: Optional[datetime] = None) -> None:
        """
        Save the last job poll time to state file.

        Args:
            dt: Datetime to save (timezone-naive UTC)
                If None, uses current time
        """
        if dt is None:
            dt = datetime.now(timezone.utc).replace(tzinfo=None)

        # Load existing state
        if self.file_path.exists():
            with open(self.file_path, "r") as f:
                data = json.load(f)
        else:
            data = {}

        # Update last job poll time
        data["last_job_poll"] = dt.isoformat()

        # Save back to file
        with open(self.file_path, "w") as f:
            json.dump(data, f, indent=2)

    def get_stats(self) -> dict:
        """Get all stats from state file"""
        if not self.file_path.exists():
            return {}

        with open(self.file_path, "r") as f:
            return json.load(f)

    def update_stats(self, **kwargs) -> None:
        """Update stats in state file"""
        data = self.get_stats()
        data.update(kwargs)

        with open(self.file_path, "w") as f:
            json.dump(data, f, indent=2)

    def get_last_estimate_poll_time(self) -> datetime:
        """
        Get the last time we polled Service Fusion for estimates.

        Returns:
            datetime: Last poll time (timezone-naive UTC)

        Falls back to 24 hours ago if state file has issues.
        Sends Slack notification if fallback is used.
        """
        try:
            # First run — no file exists
            if not self.file_path.exists():
                first_run = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
                    hours=24
                )
                print(
                    f"🟦 First estimate poll — checking updates since {first_run.isoformat()}"
                )
                return first_run

            # Load state
            with open(self.file_path, "r") as f:
                data = json.load(f)

            last_poll_str = data.get("last_estimate_poll")

            # Missing field fallback
            if not last_poll_str:
                print("⚠️ State file missing 'last_estimate_poll', using 24h fallback")
                return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
                    hours=24
                )

            return datetime.fromisoformat(last_poll_str)

        except (PermissionError, json.JSONDecodeError, ValueError) as e:
            asyncio.create_task(
                slack_notifier.send_error(
                    error=e,
                    function_name="get_last_estimate_poll_time",
                    severity=ErrorSeverity.LOW,
                    context={
                        "file_path": str(self.file_path),
                        "fallback_used": "24 hours ago",
                        "impact": "May re-sync recent estimates",
                    },
                )
            )

            return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)

    def save_last_estimate_poll_time(self, dt: Optional[datetime] = None) -> None:
        """
        Save the last estimate poll time to the state file.

        Args:
            dt: Datetime to save (timezone-naive UTC).
                If None, uses current time.
        """
        if dt is None:
            dt = datetime.now(timezone.utc).replace(tzinfo=None)

        # Load existing state
        if self.file_path.exists():
            with open(self.file_path, "r") as f:
                data = json.load(f)
        else:
            data = {}

        # Update estimate timestamp
        data["last_estimate_poll"] = dt.isoformat()

        # Save updated state
        with open(self.file_path, "w") as f:
            json.dump(data, f, indent=2)


# Global state manager instance
state_manager = StateManager()
