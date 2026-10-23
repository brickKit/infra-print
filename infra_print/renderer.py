"""The render workers (docs/design.md, "资源上限"): rendering runs in worker processes, never on the event loop, so a
render's CPU, memory or crash never reaches the member. At most `workers` renders run at once; a render waits for a
worker at most `queue_wait` (then SERVICE_BUSY); a worker past `timeout` is killed, one that dies (the address-space
limit stops it) is replaced, and either answers RENDER_LIMIT_EXCEEDED, as does an output larger than `output`."""
from __future__ import annotations

import asyncio
import contextlib
import json
import struct
import sys
from dataclasses import dataclass

from besdk import errs

ERR_LIMIT = errs.define("infra/print", "RENDER_LIMIT_EXCEEDED", errs.Code.RESOURCE_EXHAUSTED, 422, errs.RETRY_NO,
                        "The render exceeded its time, memory or output limit")
ERR_RENDER = errs.define("infra/print", "RENDER_FAILED", errs.Code.FAILED_PRECONDITION, 422, errs.RETRY_NO,
                         "The template could not be rendered with this data")

# Typst parallelizes with a thread pool whose stacks and malloc arenas reserve about 1 GiB of address space; one
# thread renders an invoice as fast (measured: 28.6 ms against 28.9 ms for 300 lines) and keeps a worker near 300 MiB,
# so RLIMIT_AS can bound it. The proxy is dead on purpose: a template never reaches the network.
WORKER_ENV = {"RAYON_NUM_THREADS": "1", "HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
              "https_proxy": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9", "NO_PROXY": "", "no_proxy": ""}


@dataclass
class Limits:
    timeout: float  # seconds
    memory: int  # bytes of address space a worker may use
    output: int  # bytes
    workers: int
    queue_wait: float  # seconds


class _Worker:
    def __init__(self, proc: asyncio.subprocess.Process):
        self.proc = proc

    async def ask(self, req: bytes) -> tuple[dict, bytes]:
        w, r = self.proc.stdin, self.proc.stdout
        w.write(struct.pack(">I", len(req)) + req)
        await w.drain()
        head = json.loads(await self._frame(r))
        return head, await self._frame(r)

    @staticmethod
    async def _frame(r: asyncio.StreamReader) -> bytes:
        n = struct.unpack(">I", await r.readexactly(4))[0]
        return await r.readexactly(n)

    def kill(self) -> None:
        with contextlib.suppress(ProcessLookupError):
            self.proc.kill()


class Renderer:
    def __init__(self, fonts: str, limits: Limits, log):
        self.fonts, self.limits, self.log = fonts, limits, log
        self._slots = asyncio.Semaphore(limits.workers)
        self._idle: list[_Worker] = []
        self._all: set[_Worker] = set()

    async def _spawn(self) -> _Worker:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-m", "infra_print.worker", self.fonts, str(self.limits.memory),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env=WORKER_ENV)  # only its own: the process environment is shared by a shell's members
        w = _Worker(proc)
        self._all.add(w)
        return w

    def _drop(self, w: _Worker) -> None:
        w.kill()
        self._all.discard(w)

    async def render(self, content: str, inputs: dict[str, str], timestamp: int) -> bytes:
        """Renders a Typst source with string inputs at a fixed timestamp; the same arguments give the same bytes."""
        try:
            await asyncio.wait_for(self._slots.acquire(), self.limits.queue_wait)
        except TimeoutError:
            raise errs.new("SERVICE_BUSY", "every render worker is busy; try again shortly") from None
        try:
            w = self._idle.pop() if self._idle else await self._spawn()
            req = json.dumps({"content": content, "inputs": inputs, "timestamp": timestamp}).encode()
            try:
                head, pdf = await asyncio.wait_for(w.ask(req), self.limits.timeout)
            except TimeoutError:
                self._drop(w)
                raise ERR_LIMIT.new(f"the render took longer than {self.limits.timeout:g}s") from None
            except (asyncio.IncompleteReadError, ConnectionError, ValueError):
                self._drop(w)  # it died: the memory limit stopped it
                raise ERR_LIMIT.new(f"the render needed more than {self.limits.memory >> 20} MiB") from None
            self._idle.append(w)
            if not head.get("ok"):
                raise ERR_RENDER.new(head.get("error", "the template failed"))
            if len(pdf) > self.limits.output:
                raise ERR_LIMIT.new(f"the document is {len(pdf) >> 10} KiB, more than {self.limits.output >> 10} KiB")
            return pdf
        finally:
            self._slots.release()

    async def close(self) -> None:
        for w in list(self._all):
            self._drop(w)
            with contextlib.suppress(Exception):
                await w.proc.wait()
        self._idle.clear()
