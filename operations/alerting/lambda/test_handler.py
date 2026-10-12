import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-2")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

import handler


class HandlerTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ENVIRONMENT"] = "production"

    def test_builds_alarm_embed_without_mentions(self) -> None:
        message = {
            "AlarmName": "seonjalal-production-cpu-high",
            "AWSAccountId": "123456789012",
            "NewStateValue": "ALARM",
            "NewStateReason": "Threshold Crossed",
            "StateChangeTime": "2026-10-05T00:00:00.000+0000",
            "Region": "Asia Pacific (Seoul)",
            "Trigger": {
                "Namespace": "AWS/EC2",
                "MetricName": "CPUUtilization",
                "Dimensions": [{"name": "InstanceId", "value": "i-0123456789abcdef0"}],
            },
        }

        payload = handler._build_payload(json.dumps(message), "arn:aws:sns:example")

        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertIn("[ALARM]", payload["embeds"][0]["title"])
        self.assertEqual(payload["embeds"][0]["color"], handler.STATE_COLORS["ALARM"])
        self.assertIn("CPUUtilization", payload["embeds"][0]["fields"][3]["value"])
        self.assertEqual(payload["embeds"][0]["timestamp"], "2026-10-05T00:00:00+00:00")

    @patch.object(handler, "_post_to_discord")
    @patch.object(handler, "_get_webhook_url", return_value="https://discord.com/api/webhooks/test/token")
    def test_delivers_sns_message(self, _mock_secret, mock_post) -> None:
        event = {
            "Records": [
                {
                    "Sns": {
                        "TopicArn": "arn:aws:sns:ap-northeast-2:123456789012:alerts",
                        "Message": "manual test",
                    }
                }
            ]
        }

        result = handler.lambda_handler(event, None)

        self.assertEqual(result, {"delivered": 1})
        mock_post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
