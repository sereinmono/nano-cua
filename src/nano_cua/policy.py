"""Default policy: observe -> ask the Actor -> execute, until the Actor finishes.

The whole policy is replaceable: the Runner only understands Observe / Execute
/ Finish commands. A future scoring policy can wrap an Actor, inspect or score
the proposed action, and emit Observe instead of Execute to collect more
evidence - without touching the Runner or the adapters.
"""

from __future__ import annotations

from .contracts import (
    Action,
    Actor,
    AgentContext,
    Command,
    Execute,
    Finish,
    ObservationRequest,
    Observe,
)


class BasicPolicy:
    """Simple observe-decision-execute loop.

    First step observes; after every executed action it observes again; then the
    Actor decides the next action or finishes. The policy holds no task state:
    everything it needs is derived from the immutable AgentContext.
    """

    def __init__(self, actor: Actor, *, observation_request: ObservationRequest | None = None) -> None:
        self._actor = actor
        self._observation_request = observation_request if observation_request is not None else ObservationRequest()

    async def next_command(self, context: AgentContext) -> Command:
        if context.last_observation is None or not context.observation_is_fresh:
            return Observe(self._observation_request)

        proposal = await self._actor.propose(context)
        if isinstance(proposal, Finish):
            return proposal
        if isinstance(proposal, Action):
            return Execute(proposal)
        raise TypeError(f"actor returned an unsupported proposal: {type(proposal).__name__}")
