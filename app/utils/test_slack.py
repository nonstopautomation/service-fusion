"""
Test script for Slack webhook integration
"""

import asyncio
from error_handler import SlackNotifier, ErrorSeverity


async def test_slack_notification():
    """Test sending a notification to Slack"""

    notifier = SlackNotifier()

    try:
        print("Testing Slack notification...")
        print(f"Webhook URL: {notifier.WEBHOOK_URL}\n")

        # Create a test error
        test_error = Exception("This is a test error from Service Fusion sync")

        # Send test notification
        await notifier.send_error(
            error=test_error,
            function_name="test_slack_notification",
            severity=ErrorSeverity.LOW,
            context={
                "test": True,
                "purpose": "Verify webhook integration",
                "customer_count": 5,
                "sample_ids": [123, 456, 789],
            },
        )

        print("Notification sent! Check your Slack channel.\n")

    except Exception as e:
        print(f"Error sending notification: {e}\n")
        import traceback

        traceback.print_exc()

    finally:
        await notifier.close()


if __name__ == "__main__":
    asyncio.run(test_slack_notification())
