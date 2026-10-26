"""Deterministic scripted Actor for tests and the no-API-key demo.

It replays a fixed list of proposals and never touches a model, network or API
key, which lets the whole loop (observe -> action -> observe -> finish) run
offline against a local page.
"""

from __future__ import annotations

from collections.abc import Iterable

from .contracts import AgentContext, Finish, Proposal


class MockActor:
    """Replays a fixed script of proposals; exhausts into Finish."""

    def __init__(self, script: Iterable[Proposal]) -> None:
        self._script: tuple[Proposal, ...] = tuple(script)
        if not self._script:
            raise ValueError("MockActor needs a non-empty script")
        self._cursor = 0

    @property
    def proposals_left(self) -> int:
        return len(self._script) - self._cursor

    async def propose(self, context: AgentContext) -> Proposal:
        if self._cursor >= len(self._script):
            return Finish(reason="mock script exhausted", success=True)
        proposal = self._script[self._cursor]
        self._cursor += 1
        return proposal
