"""Worker pipeline: recompute, idempotency, poison messages, retries."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from conftest import Harness, make_results, unique_repo
from polygraph_cloud.ids import new_id
from polygraph_cloud.models import Project, Report, RunIndex
from polygraph_cloud.queue import Message
from polygraph_cloud.services.ingest import create_report
from polygraph_cloud.services.processing import process_report
from polygraph_cloud.worker import handle_message, run


async def _queued_report(h: Harness, results=None) -> tuple[str, str]:
    """Create a queued report without processing it (as the API does before the worker runs)."""
    results = results or make_results()
    async with h.ctx.sessionmaker() as db:
        p = Project(
            id=new_id("prj"),
            owner="octo-demo",
            name=new_id("r")[-8:],
            full_name_lower=f"octo-demo/{new_id('r')[-8:]}",
            is_private=False,
            public_results=True,
        )
        db.add(p)
        await db.flush()
        rep = await create_report(h.ctx, db, p, results, results.to_json().encode(), source="token")
        await db.commit()
        return rep.id, p.id


async def test_tampered_summary_is_ignored(h: Harness, mint) -> None:
    repo, rid = unique_repo()
    tampered = make_results(tamper=True)
    assert tampered.summary.silent_lie.numerator == 0  # the file lies
    r = await h.upload(tampered, bearer=mint(repository=repo, repository_id=rid))
    async with h.ctx.sessionmaker() as db:
        rep = await db.get(Report, r.json()["report_id"])
    assert rep.status == "ready"
    assert (rep.silent_lie_num, rep.silent_lie_den) == (1, 6)
    assert (rep.success_num, rep.success_den) == (3, 6)
    assert (rep.coverage_covered, rep.coverage_applicable) == (2, 3)  # file claimed 3/3
    assert rep.summary["silent_lie"]["numerator"] == 1


async def test_processing_is_idempotent(h: Harness) -> None:
    rid, _ = await _queued_report(h)
    assert await process_report(h.ctx, rid) == "ready"
    assert await process_report(h.ctx, rid) == "done"
    # Even a forced re-run replaces rows instead of duplicating them.
    async with h.ctx.sessionmaker() as db:
        await db.execute(update(Report).where(Report.id == rid).values(status="queued"))
        await db.commit()
    assert await process_report(h.ctx, rid) == "ready"
    async with h.ctx.sessionmaker() as db:
        n = (
            await db.execute(
                select(func.count()).select_from(RunIndex).where(RunIndex.report_id == rid)
            )
        ).scalar_one()
    assert n == 7


async def test_stale_processing_is_reclaimed(h: Harness) -> None:
    rid, _ = await _queued_report(h)
    async with h.ctx.sessionmaker() as db:
        await db.execute(
            update(Report)
            .where(Report.id == rid)
            .values(status="processing", processing_started_at=datetime.now(UTC))
        )
        await db.commit()
    assert await process_report(h.ctx, rid) == "busy"
    async with h.ctx.sessionmaker() as db:
        await db.execute(
            update(Report)
            .where(Report.id == rid)
            .values(processing_started_at=datetime.now(UTC) - timedelta(hours=1))
        )
        await db.commit()
    assert await process_report(h.ctx, rid) == "ready"


class FakeQueue:
    inline = False

    def __init__(self, messages: list[Message]) -> None:
        self.messages = list(messages)
        self.acked: list[str] = []

    async def send(self, body: dict) -> None:
        self.messages.append(Message(body=json.dumps(body), receipt=str(len(self.messages))))

    async def receive(self, max_messages: int = 10) -> list[Message]:
        out, self.messages = self.messages[:max_messages], self.messages[max_messages:]
        return out

    async def ack(self, message: Message) -> None:
        self.acked.append(message.receipt)

    async def ping(self) -> None:
        return None


async def test_poison_messages_are_dropped(h: Harness) -> None:
    q = FakeQueue(
        [
            Message(body="not json", receipt="a"),
            Message(body=json.dumps({"report_id": "../../etc"}), receipt="b"),
            Message(body=json.dumps(["x"]), receipt="c"),
        ]
    )
    h.ctx.queue = q
    await run(h.ctx, once=True)
    assert q.acked == ["a", "b", "c"]


async def test_missing_report_is_acked(h: Harness) -> None:
    q = FakeQueue([Message(body=json.dumps({"report_id": new_id("rep")}), receipt="m")])
    h.ctx.queue = q
    await run(h.ctx, once=True)
    assert q.acked == ["m"]


async def test_transient_failure_retries_then_rejects(h: Harness, monkeypatch) -> None:
    rid, _ = await _queued_report(h)
    storage = h.ctx.storage
    real_get = storage.get

    async def broken_get(key: str):
        raise ConnectionError("storage down")

    monkeypatch.setattr(storage, "get", broken_get)
    h.ctx.queue = FakeQueue([])
    body = json.dumps({"report_id": rid})
    assert await handle_message(h.ctx, Message(body=body, receipt="1", receive_count=1)) == "retry"
    async with h.ctx.sessionmaker() as db:
        assert (await db.get(Report, rid)).status == "queued"  # handed back for the retry
    last = h.ctx.settings.worker_max_attempts
    assert (
        await handle_message(h.ctx, Message(body=body, receipt="2", receive_count=last)) == "failed"
    )
    async with h.ctx.sessionmaker() as db:
        rep = await db.get(Report, rid)
    assert rep.status == "rejected" and "attempt" in rep.rejection_reason
    assert h.ctx.queue.acked == ["2"]
    monkeypatch.setattr(storage, "get", real_get)


async def test_text_policy_rechecked_in_worker(h: Harness) -> None:
    rid, _ = await _queued_report(h, make_results(include_text=True))
    assert await process_report(h.ctx, rid) == "rejected"
    async with h.ctx.sessionmaker() as db:
        rep = await db.get(Report, rid)
    assert "text upload" in rep.rejection_reason and rep.raw_key is None


async def test_corrupt_raw_object_is_rejected(h: Harness) -> None:
    rid, pid = await _queued_report(h)
    await h.ctx.storage.put(f"raw/{pid}/{rid}.json.gz", gzip.compress(b'{"schema": "nope"}'))
    assert await process_report(h.ctx, rid) == "rejected"
