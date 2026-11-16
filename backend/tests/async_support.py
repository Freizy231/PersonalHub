"""Test-only wakeup fallback for restricted sandboxes.

Here cross-thread self-pipe wakeups can be suppressed. Periodic selector wakeups
let real executor work complete, including asyncio.Runner executor shutdown.
No API, database, executor or assertion is mocked by this harness.
"""
import asyncio
import unittest


class PollingLoop(asyncio.SelectorEventLoop):
    def _run_once(self):
        self.call_later(0.02, lambda: None)
        super()._run_once()


class AsyncAPITestCase(unittest.IsolatedAsyncioTestCase):
    def _setupAsyncioRunner(self):
        self._asyncioRunner = asyncio.Runner(debug=True, loop_factory=PollingLoop)
