"""stdio transport: the proxy runs as a wrapper command around the real server.

    agent  <-- stdin/stdout -->  polygraph proxy  <-- pipes -->  real server

Messages are newline-delimited JSON. Server-to-client messages that are not
answers to forwarded requests (notifications, server requests) and anything
that does not parse are passed through untouched. Works on Windows and Linux:
the agent side is read with a worker thread (asyncio cannot poll a Windows
pipe on stdin), the server side uses anyio subprocess streams.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Any

import anyio
from anyio.abc import ByteSendStream, Process
from anyio.streams.buffered import BufferedByteReceiveStream

from ..faults import FaultPlan
from ..jsonrpc import Message, ParseError, id_key
from .core import ProxyEngine

MAX_LINE = 64 * 1024 * 1024


class StdioUpstream:
    """Sends requests to the child server and matches responses by id."""

    def __init__(self, stdin: ByteSendStream) -> None:
        self._stdin = stdin
        self._lock = anyio.Lock()
        self._pending: dict[str, tuple[anyio.Event, list[Message | None]]] = {}
        self.closed = False

    async def write_raw(self, data: bytes) -> None:
        if self.closed:
            return
        async with self._lock:
            try:
                await self._stdin.send(data)
            except (anyio.BrokenResourceError, anyio.ClosedResourceError, OSError):
                self.closed = True

    async def send(self, msg: Message) -> Message | None:
        if self.closed:
            return None
        key = id_key(msg.id)
        ev = anyio.Event()
        slot: list[Message | None] = []
        self._pending[key] = (ev, slot)
        await self.write_raw(msg.to_line())
        if self.closed and not slot:
            self._pending.pop(key, None)
            return None
        await ev.wait()
        return slot[0] if slot else None

    def resolve(self, msg: Message) -> bool:
        item = self._pending.pop(id_key(msg.id), None)
        if item is None:
            return False
        ev, slot = item
        slot.append(msg)
        ev.set()
        return True

    def close(self) -> None:
        self.closed = True
        for ev, _slot in self._pending.values():
            ev.set()
        self._pending.clear()


async def _thread_lines(stream: Any) -> AsyncIterator[bytes]:
    while True:
        line = await anyio.to_thread.run_sync(stream.readline, abandon_on_cancel=True)
        if not line:
            return
        yield line


async def run_stdio_proxy(
    plan: FaultPlan,
    ledger_path: str | os.PathLike[str] | None,
    command: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    client_lines: AsyncIterator[bytes] | None = None,
    client_write: Callable[[bytes], Awaitable[None]] | None = None,
    server_name: str | None = None,
) -> int:
    """Run the proxy until the agent closes stdin or the server exits.

    ``client_lines``/``client_write`` default to this process's stdin/stdout;
    tests pass their own. Returns the server's exit code.
    """
    engine = ProxyEngine(plan, os.fspath(ledger_path) if ledger_path else None, server=server_name)
    out_lock = anyio.Lock()

    if client_write is None:
        out = sys.stdout.buffer

        async def client_write(data: bytes) -> None:
            out.write(data)
            out.flush()

    assert client_write is not None
    write = client_write

    async def to_client(data: bytes) -> None:
        async with out_lock:
            with contextlib.suppress(OSError, ValueError):
                await write(data)

    async def emit(msg: Message) -> None:
        await to_client(msg.to_line())

    lines = client_lines if client_lines is not None else _thread_lines(sys.stdin.buffer)
    proc: Process = await anyio.open_process(
        list(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        env=dict(env) if env is not None else None,
    )
    assert proc.stdin is not None and proc.stdout is not None
    upstream = StdioUpstream(proc.stdin)
    server_out = BufferedByteReceiveStream(proc.stdout)

    in_flight = {"n": 0}
    idle = {"event": anyio.Event()}
    idle["event"].set()

    async def handle(msg: Message) -> None:
        in_flight["n"] += 1
        if idle["event"].is_set():
            idle["event"] = anyio.Event()
        try:
            reply = await engine.handle_request(msg, upstream, emit=emit)
            for note in reply.before:
                await to_client(note.to_line())
            if isinstance(reply.message, Message):
                await to_client(reply.message.to_line())
            elif isinstance(reply.message, bytes):
                await to_client(reply.message.rstrip(b"\n") + b"\n")
        finally:
            in_flight["n"] -= 1
            if in_flight["n"] == 0:
                idle["event"].set()

    async def pump_client(tg: anyio.abc.TaskGroup) -> None:
        async for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = Message.parse(line)
            except ParseError:
                await upstream.write_raw(line + b"\n")
                continue
            if msg.is_request:
                tg.start_soon(handle, msg)
            else:
                if msg.is_notification:
                    engine.on_client_notification(msg)
                await upstream.write_raw(line + b"\n")
        # The agent is gone: let the server finish and exit.
        with contextlib.suppress(OSError, anyio.BrokenResourceError, anyio.ClosedResourceError):
            await proc.stdin.aclose()  # type: ignore[union-attr]

    async def pump_server(tg: anyio.abc.TaskGroup) -> None:
        while True:
            try:
                line = await server_out.receive_until(b"\n", MAX_LINE)
            except (anyio.EndOfStream, anyio.IncompleteRead, anyio.ClosedResourceError):
                break
            except anyio.DelimiterNotFound:
                break
            stripped = line.strip()
            if not stripped:
                continue
            try:
                msg = Message.parse(stripped)
            except ParseError:
                await to_client(stripped + b"\n")
                continue
            if msg.is_response and upstream.resolve(msg):
                continue
            await to_client(stripped + b"\n")
        upstream.close()
        # Let replies that are already on their way (e.g. under a slow fault) finish.
        with anyio.move_on_after(plan.timeout_s + plan.slow_ms / 1000.0 + 5.0):
            await idle["event"].wait()
        tg.cancel_scope.cancel()

    async with anyio.create_task_group() as tg:
        tg.start_soon(pump_server, tg)
        await pump_client(tg)
        # Wait for the server to exit on its own once stdin is closed.
        with anyio.move_on_after(10):
            await proc.wait()
        tg.cancel_scope.cancel()
    if proc.returncode is None:
        proc.terminate()
        with anyio.move_on_after(5):
            await proc.wait()
        if proc.returncode is None:  # pragma: no cover
            proc.kill()
    await proc.aclose()
    return int(proc.returncode or 0)
