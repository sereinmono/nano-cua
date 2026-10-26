"""nano-cua: a minimal, replaceable async GUI-agent base.

Four injected ports (Observer / ActionExecutor / Actor / AgentPolicy) around one
command-driven loop (Runner). Only ``runtime`` dependencies are imported here;
concrete adapters live in ``nano_cua.adapters`` and are wired by ``nano_cua.cli``.
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

__version__ = "0.1.0"

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
