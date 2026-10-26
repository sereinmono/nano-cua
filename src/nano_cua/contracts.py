"""nano-cua core contracts.

The core loop only depends on this module. Concrete I/O (browser automation,
HTTP, model SDKs) lives behind the four ports below and is injected at
composition time; nothing here imports a browser driver, an HTTP client or a
model SDK, and nothing here hard-codes a concrete observation or action
vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Protocol, TypeAlias, runtime_checkable

JsonMapping: TypeAlias = Mapping[str, Any]
"""Free-form mapping carried across a port boundary."""


def frozen_mapping(data: Mapping[str, Any] | None = None) -> JsonMapping:
    """Return a read-only view so stale messages cannot expose live state."""
    return MappingProxyType(dict(data or {}))


class ActionValidationError(ValueError):
    """Raised by adapters when an inbound action message cannot be trusted."""


@dataclass(frozen=True, slots=True)
class ObservationRequest:
    """Explicit request for one observation.

    An empty request means "whatever the observer produces by default". The
    filters are hints for observers able to produce several observation kinds;
    an observer that cannot honour a filter must reject the request rather than
    substitute a different observation silently.
    """

    source: str | None = None
    kind: str | None = None
    metadata: JsonMapping = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Observation:
    """An immutable snapshot produced by an Observer.

    ``payload`` is kept in memory (image bytes for visual observations, text for
    DOM/a11y observations) and is never persisted by the core. Every capture
    returns a new Observation object; an existing one never changes.
    """

    source: str
    kind: str
    payload: bytes | str | None
    captured_at: float
    metadata: JsonMapping = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return self.payload is None or len(self.payload) == 0


@dataclass(frozen=True, slots=True)
class Action:
    """A generic action message. The core never interprets ``space``/``kind``."""

    space: str
    kind: str
    args: JsonMapping = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Outcome of one Execute command, including its own measured duration."""

    success: bool
    error: str | None = None
    duration_s: float = 0.0
    output: JsonMapping = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Finish:
    """Terminal proposal/command: end the task."""

    reason: str = ""
    success: bool = True


@dataclass(frozen=True, slots=True)
class Observe:
    """Command: fetch one explicit observation from the Observer."""

    request: ObservationRequest = field(default_factory=ObservationRequest)


@dataclass(frozen=True, slots=True)
class Execute:
    """Command: run one action through the ActionExecutor."""

    action: Action


Proposal: TypeAlias = Action | Finish
"""What an Actor may return: one concrete action or a terminal decision."""

Command: TypeAlias = Observe | Execute | Finish
"""What an AgentPolicy may return: the only three commands the Runner knows."""


@dataclass(frozen=True, slots=True)
class AgentContext:
    """Immutable task state handed to the policy/actor before each decision.

    The Runner replaces this object on every step instead of mutating it, and
    keeps at most a bounded number of recent observations. ``observation_is_fresh``
    says whether the latest observation was captured *after* the last executed
    action; a policy that only acts on fresh evidence can check it.
    """

    task: str
    observations: tuple[Observation, ...] = ()
    last_action: Action | None = None
    last_result: ActionResult | None = None
    step_count: int = 0
    observation_count: int = 0
    action_count: int = 0
    observation_is_fresh: bool = False
    started_at: float = 0.0

    @property
    def last_observation(self) -> Observation | None:
        return self.observations[-1] if self.observations else None


@runtime_checkable
class Observer(Protocol):
    """Produces observations on explicit request."""

    async def observe(self, request: ObservationRequest) -> Observation: ...


@runtime_checkable
class ActionExecutor(Protocol):
    """Executes generic action messages and reports the outcome."""

    async def execute(self, action: Action) -> ActionResult: ...


@runtime_checkable
class Actor(Protocol):
    """Turns the current context into a candidate action or a finish decision."""

    async def propose(self, context: AgentContext) -> Proposal: ...


@runtime_checkable
class AgentPolicy(Protocol):
    """Decides the next command for the Runner, step by step."""

    async def next_command(self, context: AgentContext) -> Command: ...
