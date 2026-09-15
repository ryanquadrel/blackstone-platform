"""
MCP Tools
=========

agno 2.6.x ``MCPTools.connect()`` enters the transport and session context
managers, then ``initialize()`` swallows any handshake failure and leaves
``_initialized`` False. ``close()`` returns early in that state, so the
half-entered contexts are never exited. The streamable-http transport holds an
anyio task group whose cancel scope outlives the task that entered it, and anyio
reschedules ``CancelScope._deliver_cancellation`` on every event-loop iteration
forever. The loop pins a CPU core while still serving requests.

AgentOS calls ``connect()`` from request handlers (``GET /agents`` included,
which the compose healthcheck hits every 30s), so one failed handshake is
enough to start the spin.

agno 3.x fixes this upstream with ``MCPTools._safe_cleanup()``. Until the pin
moves, ``SafeMCPTools`` exits partially-entered contexts in the same task that
entered them, which is the only place anyio allows it.
"""

from agno.tools.mcp import MCPTools


class SafeMCPTools(MCPTools):
    async def connect(self, force: bool = False) -> None:  # type: ignore[override]
        if force:
            # Upstream's force path drops the old contexts without exiting them.
            await self._exit_partial_contexts()
        await super().connect(force=force)
        if not self._initialized:
            await self._exit_partial_contexts()

    async def _exit_partial_contexts(self) -> None:
        for context in reversed(self._active_contexts):
            try:
                await context.__aexit__(None, None, None)
            except BaseException:  # noqa: BLE001, S110
                # Same policy as agno 3.x _safe_cleanup: cleanup must not mask
                # the connect failure that upstream already logged.
                pass
        self._active_contexts = []
        self._session_context = None
        self.session = None
        self._context = None
        self._initialized = False
