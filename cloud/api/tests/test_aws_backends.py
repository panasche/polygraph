"""S3 (SSE-KMS) storage and SQS queue backends, against moto's in-memory AWS."""

from __future__ import annotations

import json

import pytest

moto = pytest.importorskip("moto")

import boto3  # noqa: E402

from polygraph_cloud.config import Settings  # noqa: E402
from polygraph_cloud.queue import SQSQueue  # noqa: E402
from polygraph_cloud.storage import S3Storage  # noqa: E402

REGION = "ap-south-1"


@pytest.fixture
def aws(monkeypatch):  # type: ignore[no-untyped-def]
    for k, v in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": REGION,
    }.items():
        monkeypatch.setenv(k, v)
    with moto.mock_aws():
        yield


async def test_s3_storage_roundtrip_with_kms(aws) -> None:  # type: ignore[no-untyped-def]
    kms = boto3.client("kms", region_name=REGION)
    key_id = kms.create_key()["KeyMetadata"]["KeyId"]
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(
        Bucket="polygraph-test", CreateBucketConfiguration={"LocationConstraint": REGION}
    )
    st = S3Storage(Settings(storage_backend="s3", s3_bucket="polygraph-test", kms_key_id=key_id))
    await st.ping()
    await st.put("runs/prj_a/rep_b/run-1.json", b'{"x": 1}')
    for i in range(3):
        await st.put(f"runs/prj_a/rep_b/run-extra-{i}.json", b"{}")
    await st.put("raw/prj_a/rep_b.json.gz", b"gz", content_type="application/gzip")
    assert await st.get("runs/prj_a/rep_b/run-1.json") == b'{"x": 1}'
    assert await st.get("runs/prj_a/rep_b/missing.json") is None
    head = s3.head_object(Bucket="polygraph-test", Key="runs/prj_a/rep_b/run-1.json")
    assert head["ServerSideEncryption"] == "aws:kms"
    assert len(await st.list_keys("runs/prj_a/rep_b/")) == 4
    assert await st.delete_prefix("runs/prj_a/") == 4
    assert await st.list_keys("runs/prj_a/") == []
    await st.delete("raw/prj_a/rep_b.json.gz")
    assert await st.get("raw/prj_a/rep_b.json.gz") is None
    with pytest.raises(ValueError):
        await st.put("../escape", b"x")


async def test_sqs_queue_roundtrip(aws) -> None:  # type: ignore[no-untyped-def]
    url = boto3.client("sqs", region_name=REGION).create_queue(QueueName="uploads")["QueueUrl"]
    q = SQSQueue(Settings(queue_backend="sqs", sqs_queue_url=url, sqs_wait_time_s=0))
    await q.ping()
    await q.send({"report_id": "rep_0123456789abcdefghjkmnpqrs"})
    msgs = await q.receive(10)
    assert len(msgs) == 1 and json.loads(msgs[0].body)["report_id"].startswith("rep_")
    assert msgs[0].receive_count == 1
    await q.ack(msgs[0])
    assert await q.receive(10) == []
