"""
Error handling and logging utilities with Slack webhook integration.
"""

import logging
import traceback
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from functools import wraps
import httpx
from enum import Enum


class ErrorSeverity(str, Enum):
    """Error severity levels"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ServiceFusionSyncError(Exception):
    """Base exception for Service Fusion sync errors"""

    def __init__(
        self,
        message: str,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        context: Optional[Dict[str, Any]] = None,
    ):
        self.message = message
        self.severity = severity
        self.context = context or {}
        super().__init__(self.message)


class CustomerSyncError(ServiceFusionSyncError):
    """Error syncing customer data"""

    pass


class JobSyncError(ServiceFusionSyncError):
    """Error syncing job data"""

    pass


class APIError(ServiceFusionSyncError):
    """Error calling external API (SF or GHL)"""

    pass


class SlackNotifier:
    """Handles sending error notifications to Slack"""

    WEBHOOK_URL = "https://hooks.slack.com/triggers/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"

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=10.0)

    async def send_error(
        self,
        error: Exception,
        function_name: str,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        context: Optional[Dict[str, Any]] = None,
    ):
        """Send error notification to Slack"""
        try:
            # Build error message
            error_type = type(error).__name__
            error_msg = str(error)

            # Get traceback
            tb = traceback.format_exc()

            # Build readable message
            formatted_message = f"Service Fusion Sync Error\n\n"
            formatted_message += f"Severity: {severity.value.upper()}\n"
            formatted_message += f"Function: {function_name}\n"
            formatted_message += f"Error Type: {error_type}\n"
            formatted_message += f"Message: {error_msg}\n"

            if context:
                formatted_message += f"\nContext:\n"
                for key, value in context.items():
                    # Handle lists/dicts specially
                    if isinstance(value, (list, dict)):
                        formatted_message += f"  • {key}: {str(value)[:200]}\n"
                    else:
                        formatted_message += f"  • {key}: {value}\n"

            formatted_message += (
                f"\nTimestamp: {datetime.now(timezone.utc).isoformat()}"
            )

            # Log locally ALWAYS (backup if Slack fails)
            logging.error(
                f"\n{'=' * 60}\n{formatted_message}\n{'=' * 60}",
                extra={"severity": severity.value, "function": function_name},
            )

            # Try to send to Slack
            message = {"service_fusion_error": formatted_message}

            response = await self.client.post(
                self.WEBHOOK_URL,
                json=message,
            )

            if response.status_code == 200:
                logging.info(f"Slack notification sent for {function_name}")
            else:
                logging.warning(
                    f"Slack notification failed: {response.status_code} - {response.text}"
                )

        except Exception as e:
            # Don't let Slack notification failures break the app
            logging.warning(
                f"Could not send Slack notification: {e}\n"
                f"   (This is expected if hooks.slack.com is not in allowed domains)\n"
                f"   Error was logged locally instead."
            )

    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()


# Global notifier instance
slack_notifier = SlackNotifier()


def with_error_handling(
    severity: ErrorSeverity = ErrorSeverity.MEDIUM,
    notify_slack: bool = True,
):
    """
    Decorator for adding error handling to async functions.

    Usage:
        @with_error_handling(severity=ErrorSeverity.HIGH)
        async def my_function():
            ...
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)

            except ServiceFusionSyncError as e:
                # Our custom errors - already have context
                logging.error(
                    f"Error in {func.__name__}: {e.message}",
                    extra={
                        "severity": e.severity.value,
                        "context": e.context,
                    },
                )

                if notify_slack:
                    await slack_notifier.send_error(
                        error=e,
                        function_name=func.__name__,
                        severity=e.severity,
                        context=e.context,
                    )

                raise

            except Exception as e:
                # Unexpected errors
                context = {
                    "args": str(args)[:200],  # Truncate to avoid huge messages
                    "kwargs": str(kwargs)[:200],
                }

                logging.error(
                    f"Unexpected error in {func.__name__}: {e}",
                    extra={
                        "severity": severity.value,
                        "context": context,
                    },
                    exc_info=True,
                )

                if notify_slack:
                    await slack_notifier.send_error(
                        error=e,
                        function_name=func.__name__,
                        severity=severity,
                        context=context,
                    )

                raise

        return wrapper

    return decorator


# At the bottom of app/error_handler.py


def safe_scheduled_job(func):
    """
    Decorator that prevents scheduled jobs from crashing the scheduler.

    Usage:
        @safe_scheduled_job
        async def my_scheduled_job():
            ...

    Catches all exceptions, logs them, sends to Slack, but doesn't re-raise
    so the scheduler continues running.
    """

    @wraps(func)
    async def wrapper(*args, **kwargs):
        job_name = func.__name__

        try:
            logging.info(f"Starting scheduled job: {job_name}")
            result = await func(*args, **kwargs)
            logging.info(f"Completed scheduled job: {job_name}")
            return result

        except Exception as e:
            # Log with full traceback
            logging.error(f"Scheduled job '{job_name}' failed: {e}", exc_info=True)

            # Notify via Slack
            await slack_notifier.send_error(
                error=e,
                function_name=f"scheduled_job:{job_name}",
                severity=ErrorSeverity.HIGH,
                context={
                    "job_name": job_name,
                    "note": "Job failed but scheduler continues running",
                    "next_run": "Will retry on next scheduled interval",
                },
            )

            # Print user-friendly console message
            print(f"\n{'=' * 80}")
            print(f"Scheduled job '{job_name}' failed")
            print(f"   Error: {type(e).__name__}: {e}")
            print(f"   Scheduler continues - will retry on next interval")
            print(f"{'=' * 80}\n")

            # DON'T re-raise - let scheduler continue

    return wrapper


# Configure logging
def setup_logging():
    """Configure application logging"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Suppress noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
