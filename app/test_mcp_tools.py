"""
A failed MCP connect must not leave the event loop spinning.

Reproduces the agentos-api 100% CPU spin: a streamable-http MCP handshake that
fails inside a short-lived task leaves an anyio cancel scope that reschedules
``CancelScope._deliver_cancellation`` on every loop iteration.
"""

import asyncio
from collections.abc import Callable
from importlib.metadata import version

import pytest
from agno.tools.mcp import MCPTools

from app.mcp_tools import SafeMCPTools

# Discard port on loopback: connection refused immediately, no DNS or network.
UNREACHABLE_URL = "http://127.0.0.1:9/mcp"


async def _spin_samples_after_failed_connect(tools_cls: Callable[..., MCPTools]) -> int:
    tools = tools_cls(url=UNREACHABLE_URL, transport="streamable-http", timeout_seconds=2)

    # Connect from a task that then finishes, the way an AgentOS request handler does.
    task = asyncio.create_task(tools.connect())
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert not tools.initialized

    loop = asyncio.get_running_loop()
    samples = 0
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        ready = loop._ready  # type: ignore[attr-defined]
        samples += len([h for h in ready if "_deliver_cancellation" in repr(getattr(h, "_callback", h))])
        await asyncio.sleep(0.005)
    return samples


@pytest.mark.skipif(int(version("agno").split(".")[0]) >= 3, reason="agno 3.x cleans up failed connects upstream")
def test_upstream_mcptools_spins_after_failed_connect() -> None:
    assert asyncio.run(_spin_samples_after_failed_connect(MCPTools)) > 0


def test_safe_mcptools_does_not_spin_after_failed_connect() -> None:
    assert asyncio.run(_spin_samples_after_failed_connect(SafeMCPTools)) == 0


def test_safe_mcptools_force_reconnect_after_failure_does_not_spin() -> None:
    async def run() -> int:
        tools = SafeMCPTools(url=UNREACHABLE_URL, transport="streamable-http", timeout_seconds=2)
        await tools.connect()
        await tools.connect(force=True)
        return await _spin_samples_after_failed_connect(lambda **_: tools)

    assert asyncio.run(run()) == 0
