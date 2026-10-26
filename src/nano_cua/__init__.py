"""nano-cua: a minimal, replaceable async GUI-agent base.

Four injected ports (Observer / ActionExecutor / Actor / AgentPolicy) around one
command-driven loop (Runner). This package deliberately contains no concrete
observation space, action space, model SDK or CLI: those belong to external
compatibility groups (for example the companion ``nano-cua-extensions``
project) and are injected at composition time.
"""

from .contracts import (
    Action,
    ActionResult,
    ActionValidationError,
    Actor,
    AgentContext,
    AgentPolicy,
    Command,
    Execute,
    Finish,
    Observation,
    ObservationRequest,
    Observe,
    Observer,
    Proposal,
    frozen_mapping,
)
from .mock import MockActor
from .policy import BasicPolicy
from .runner import RunResult, Runner, StepRecord

__version__ = "0.2.0"

__all__ = [
    "Action",
    "ActionResult",
    "ActionValidationError",
    "Actor",
    "AgentContext",
    "AgentPolicy",
    "BasicPolicy",
    "Command",
    "Execute",
    "Finish",
    "MockActor",
    "Observation",
    "ObservationRequest",
    "Observe",
    "Observer",
    "Proposal",
    "RunResult",
    "Runner",
    "StepRecord",
    "frozen_mapping",
]
