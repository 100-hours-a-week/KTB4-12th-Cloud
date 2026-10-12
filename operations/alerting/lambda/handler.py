"""CloudWatch/SNS 메시지를 Discord Embed로 전달한다.

Discord Webhook URL은 환경변수나 배포 패키지에 저장하지 않고 Secrets Manager에서
실행 시 조회한다. 조회한 값과 원본 SNS 메시지는 로그에 출력하지 않는다.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

_secrets_client: Any | None = None
_cached_webhook_url: str | None = None

STATE_COLORS = {
    "ALARM": 0xE74C3C,
    "OK": 0x2ECC71,
    "INSUFFICIENT_DATA": 0xF1C40F,
    "INFO": 0x3498DB,
}


def _get_webhook_url() -> str:
    """Webhook을 한 번 조회하고 Lambda 실행 환경이 재사용되는 동안 캐시한다."""

    global _cached_webhook_url, _secrets_client
    if _cached_webhook_url:
        return _cached_webhook_url

    # boto3는 Lambda Python Runtime에 포함된다. 지연 Import로 순수 로컬 단위 테스트가
    # AWS SDK 설치 여부와 무관하게 메시지 변환 로직을 검증할 수 있게 한다.
    if _secrets_client is None:
        import boto3

        _secrets_client = boto3.client("secretsmanager")

    secret_arn = os.environ["DISCORD_WEBHOOK_SECRET_ARN"]
    response = _secrets_client.get_secret_value(SecretId=secret_arn)
    secret_string = response.get("SecretString")
    if not secret_string:
        raise ValueError("Discord webhook secret must contain SecretString")

    # 단순 URL 문자열과 {"webhook_url": "..."} JSON 형식을 모두 허용한다.
    try:
        decoded = json.loads(secret_string)
    except json.JSONDecodeError:
        webhook_url = secret_string
    else:
        webhook_url = decoded.get("webhook_url") if isinstance(decoded, dict) else None

    if not isinstance(webhook_url, str) or not webhook_url.startswith(
        "https://discord.com/api/webhooks/"
    ):
        raise ValueError("Discord webhook secret has an invalid URL")

    _cached_webhook_url = webhook_url
    return webhook_url


def _truncate(value: Any, limit: int = 1000) -> str:
    text = str(value or "-")
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _normalize_timestamp(value: Any) -> str | None:
    """CloudWatch의 ISO 시각을 Discord가 해석하기 쉬운 RFC 3339 형태로 바꾼다."""

    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _metric_summary(message: dict[str, Any]) -> str:
    trigger = message.get("Trigger") or {}
    namespace = trigger.get("Namespace")
    metric_name = trigger.get("MetricName")
    dimensions = trigger.get("Dimensions") or []

    metric = "/".join(filter(None, [namespace, metric_name])) or "-"
    dimension_text = ", ".join(
        f"{item.get('name')}={item.get('value')}"
        for item in dimensions
        if item.get("name") and item.get("value")
    )
    return f"{metric}\n{dimension_text}" if dimension_text else metric


def _build_payload(message_text: str, topic_arn: str) -> dict[str, Any]:
    """CloudWatch Alarm JSON 또는 일반 SNS 메시지를 Discord payload로 바꾼다."""

    try:
        message = json.loads(message_text)
    except json.JSONDecodeError:
        message = {"Message": message_text}

    if "AlarmName" in message:
        state = str(message.get("NewStateValue", "INFO"))
        title = f"[{state}] {message.get('AlarmName', 'CloudWatch Alarm')}"
        fields = [
            {"name": "환경", "value": os.getenv("ENVIRONMENT", "unknown"), "inline": True},
            {"name": "리전", "value": _truncate(message.get("Region")), "inline": True},
            {"name": "AWS 계정", "value": _truncate(message.get("AWSAccountId")), "inline": True},
            {"name": "메트릭", "value": _truncate(_metric_summary(message)), "inline": False},
            {
                "name": "상태 변경 사유",
                "value": _truncate(message.get("NewStateReason")),
                "inline": False,
            },
        ]
        timestamp = _normalize_timestamp(message.get("StateChangeTime"))
    else:
        state = "INFO"
        title = f"[INFO] {os.getenv('SERVICE_NAME', 'AWS Infrastructure')}"
        fields = [
            {"name": "환경", "value": os.getenv("ENVIRONMENT", "unknown"), "inline": True},
            {"name": "SNS Topic", "value": _truncate(topic_arn), "inline": False},
            {"name": "메시지", "value": _truncate(message.get("Message", message)), "inline": False},
        ]
        timestamp = datetime.now(timezone.utc).isoformat()

    embed: dict[str, Any] = {
        "title": _truncate(title, 256),
        "color": STATE_COLORS.get(state, STATE_COLORS["INFO"]),
        "fields": fields,
        "footer": {"text": "Seonjalal AWS Alerting"},
    }
    if timestamp:
        embed["timestamp"] = timestamp

    return {
        "username": os.getenv("DISCORD_USERNAME", "Seonjalal Infra Alert"),
        "allowed_mentions": {"parse": []},
        "embeds": [embed],
    }


def _post_to_discord(webhook_url: str, payload: dict[str, Any]) -> None:
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "seonjalal-infra-alert/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status not in (200, 204):
                raise RuntimeError(f"Discord returned HTTP {response.status}")
    except urllib.error.HTTPError as error:
        # 예외를 다시 발생시켜 SNS/Lambda 재시도 경로가 동작하게 한다.
        raise RuntimeError(f"Discord returned HTTP {error.code}") from error


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, int]:
    webhook_url = _get_webhook_url()
    delivered = 0

    for record in event.get("Records", []):
        sns = record.get("Sns") or {}
        message_text = sns.get("Message")
        if not isinstance(message_text, str):
            raise ValueError("SNS record does not contain a string Message")

        payload = _build_payload(message_text, sns.get("TopicArn", "-"))
        _post_to_discord(webhook_url, payload)
        delivered += 1

    if delivered == 0:
        raise ValueError("Event does not contain SNS records")

    # Webhook URL이나 원본 메시지를 반환하거나 로그로 출력하지 않는다.
    return {"delivered": delivered}
