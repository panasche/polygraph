"""Upload worker: ``python -m polygraph_cloud.worker [--once]``.

Long-polls SQS. For each message:

- body isn't ``{"report_id": "rep_..."}`` -> poison: logged and deleted;
- report already ready/rejected or gone -> deleted (idempotent redelivery);
- processing raised -> left on the queue; SQS redelivers it after the
  visibility timeout. On the ``WORKER_MAX_ATTEMPTS``-th receive the report is
  marked rejected and the message deleted, so nothing loops forever. Configure
  the queue's redrive policy (DLQ) with a higher ``maxReceiveCount`` to catch
  worker crashes too.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import signal
import sys

import anyio

from . import runtime
from .config import get_settings
from .context import AppContext, build_context
from .logs import configure_logging, get_logger
from .queue import Message

log = get_logger("polygraph_cloud.worker")

_REPORT_ID = re.compile(r"^rep_[0-9a-z]{26}$")


def parse_message(body: str) -> str | None:
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    rid = data.get("report_id")
    if not isinstance(rid, str) or not _REPORT_ID.match(rid):
        return None
    return rid


async def handle_message(ctx: AppContext, message: Message) -> str:
    """Handle one queue message. Returns what happened (for logs and tests)."""
    from .services.processing import process_report, reject

    report_id = parse_message(message.body)
    if report_id is None:
        log.warning("poison_message_dropped")
        await ctx.queue.ack(message)
        return "poison"
    try:
        outcome = await process_report(ctx, report_id)
    except Exception as exc:
        log.error(
            "report_processing_failed",
            report_id=report_id,
            attempt=message.receive_count,
            error_type=type(exc).__name__,
        )
        if message.receive_count >= ctx.settings.worker_max_attempts or ctx.queue.inline:
            await reject(
                ctx,
                report_id,
                f"Processing failed after {message.receive_count} attempt(s). Upload it again.",
            )
            await ctx.queue.ack(message)
            return "failed"
        return "retry"
    if outcome == "busy":
        # Someone else holds it; let the visibility timeout bring it back.
        return "busy"
    await ctx.queue.ack(message)
    return outcome


async def run(ctx: AppContext, once: bool = False) -> None:
    stop = anyio.Event()

    def _stop(*_: object) -> None:
        log.info("worker_stopping")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(ValueError, OSError):  # not on the main thread (tests)
            signal.signal(sig, _stop)

    log.info("worker_started", queue="inline" if ctx.queue.inline else "sqs")
    while not stop.is_set():
        messages = await ctx.queue.receive(10)
        if not messages:
            if once:
                return
            continue
        for m in messages:
            await handle_message(ctx, m)
            if stop.is_set():
                break


async def _main(once: bool) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.check()
    ctx = build_context(settings)
    try:
        if ctx.queue.inline:
            log.info("inline_queue_no_worker_needed")
            return 0
        await run(ctx, once=once)
    finally:
        await ctx.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m polygraph_cloud.worker")
    parser.add_argument("--once", action="store_true", help="drain the queue, then exit")
    args = parser.parse_args(argv)
    return runtime.run(_main, args.once)


if __name__ == "__main__":
    sys.exit(main())
