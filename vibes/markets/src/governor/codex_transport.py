"""Persistent Codex app-server connection; all subprocess communication is JSON data."""

import asyncio
import json

from governor.app_client import AppError


class CodexTransport:
    def __init__(self, argv, cwd, on_event):
        self.argv, self.cwd, self.on_event = argv, cwd, on_event
        self.process = None
        self.pending = {}
        self.sequence = 0
        self.reader = None
        self.stderr_reader = None
        self.closing = False

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            *self.argv,
            cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=8_000_000,
        )
        self.reader = asyncio.create_task(self._read())
        self.stderr_reader = asyncio.create_task(self._drain_stderr())
        await self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "governor_terminal",
                    "title": "Governor",
                    "version": "0.1.0",
                },
            },
        )
        await self.send({"method": "initialized"})

    async def _drain_stderr(self):
        # Codex diagnostics can contain private config. Drain without exposing them in the UI.
        while await self.process.stderr.read(8192):
            pass

    async def send(self, message):
        if not self.process or self.process.returncode is not None:
            raise AppError("Codex disconnected. Your Governor budget and holds are preserved.")
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        await self.process.stdin.drain()

    async def request(self, method, params=None):
        self.sequence += 1
        mid = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[mid] = future
        try:
            await self.send({"id": mid, "method": method, "params": params or {}})
            async with asyncio.timeout(90):
                return await future
        finally:
            self.pending.pop(mid, None)

    async def reply(self, mid, result=None, error=None):
        await self.send({"id": mid, **({"error": error} if error else {"result": result})})

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if "method" in message:
                    await self.on_event(message)
                elif (future := self.pending.get(message.get("id"))) and not future.done():
                    if "error" in message:
                        future.set_exception(
                            AppError(message["error"].get("message", "Codex error"))
                        )
                    else:
                        future.set_result(message.get("result", {}))
        except (ValueError, OSError) as exc:
            error = AppError(f"Codex stream ended: {type(exc).__name__}")
        else:
            error = AppError("Codex disconnected. Reopen with the same Governor session.")
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(locals().get("error", AppError("Codex stopped")))
            if not self.closing:
                await self.on_event({"method": "governor/disconnected"})

    async def close(self):
        self.closing = True
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        for task in (self.reader, self.stderr_reader):
            if task:
                task.cancel()
        await asyncio.gather(
            *(t for t in (self.reader, self.stderr_reader) if t), return_exceptions=True
        )
