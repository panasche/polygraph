"""The upload queue: SQS in the cloud, an in-process queue for development and tests.

Message body (CONTRACTS.md 5): ``{"report_id": "rep_..."}``.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol

import anyio
import anyio.to_thread

from .config import Settings


@dataclass
class Message:
    body: str
    receipt: str
    receive_count: int = 1


class Queue(Protocol):
    inline: bool

    async def send(self, body: dict[str, Any]) -> None: ...
    async def receive(self, max_messages: int = 10) -> list[Message]: ...
    async def ack(self, message: Message) -> None: ...
    async def ping(self) -> None: ...


class InlineQueue:
    """Messages are processed in the API process right after the response is sent."""

    inline = True

    def __init__(self) -> None:
        self._items: deque[str] = deque()
        self._counter = 0

    async def send(self, body: dict[str, Any]) -> None:
        self._items.append(json.dumps(body))

    async def receive(self, max_messages: int = 10) -> list[Message]:
        out = []
        while self._items and len(out) < max_messages:
            self._counter += 1
            out.append(Message(body=self._items.popleft(), receipt=str(self._counter)))
        return out

    async def ack(self, message: Message) -> None:
        return None

    async def ping(self) -> None:
        return None

    def __len__(self) -> int:
        return len(self._items)


class SQSQueue:
    inline = False

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        if not settings.sqs_queue_url:
            raise ValueError("SQS_QUEUE_URL is required for QUEUE_BACKEND=sqs")
        self.url = settings.sqs_queue_url
        self.visibility_timeout = settings.sqs_visibility_timeout_s
        self.wait_time = settings.sqs_wait_time_s
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "sqs",
                region_name=settings.aws_region,
                endpoint_url=settings.sqs_endpoint_url or None,
                config=Config(retries={"max_attempts": 5, "mode": "standard"}),
            )
        self.client = client

    async def send(self, body: dict[str, Any]) -> None:
        await anyio.to_thread.run_sync(
            lambda: self.client.send_message(QueueUrl=self.url, MessageBody=json.dumps(body))
        )

    async def receive(self, max_messages: int = 10) -> list[Message]:
        def _recv() -> dict[str, Any]:
            return self.client.receive_message(
                QueueUrl=self.url,
                MaxNumberOfMessages=max(1, min(10, max_messages)),
                WaitTimeSeconds=self.wait_time,
                VisibilityTimeout=self.visibility_timeout,
                MessageSystemAttributeNames=["ApproximateReceiveCount"],
            )

        resp = await anyio.to_thread.run_sync(_recv)
        out = []
        for m in resp.get("Messages", []):
            count = int(m.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
            out.append(Message(body=m["Body"], receipt=m["ReceiptHandle"], receive_count=count))
        return out

    async def ack(self, message: Message) -> None:
        await anyio.to_thread.run_sync(
            lambda: self.client.delete_message(QueueUrl=self.url, ReceiptHandle=message.receipt)
        )

    async def ping(self) -> None:
        await anyio.to_thread.run_sync(
            lambda: self.client.get_queue_attributes(
                QueueUrl=self.url, AttributeNames=["ApproximateNumberOfMessages"]
            )
        )


def make_queue(settings: Settings) -> Queue:
    if settings.queue_backend == "sqs":
        return SQSQueue(settings)
    return InlineQueue()
