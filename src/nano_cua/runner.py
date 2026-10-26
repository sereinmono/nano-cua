"""Command-driven agent loop.

``Runner`` is the only component that sequences Observe / Execute / Finish. It
never captures observations on its own, never talks to a model and never scores
a candidate action - those responsibilities belong to the injected policy and
adapters. This keeps "how to act" replaceable without touching the loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Callable

from .contracts import (
    Action,
    ActionExecutor,
    ActionResult,
    AgentContext,
    AgentPolicy,
    Execute,
    Finish,
    Observation,
    Observe,
    Observer,
)


@dataclass(frozen=True, slots=True)
class StepRecord:
    """One loop iteration: policy decision plus the work it triggered."""

    index: int
    command: str
    detail: str
    decision_s: float
    work_s: float
    success: bool | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    """Terminal report of a single task run."""

    task: str
    success: bool
    reason: str
    error: str | None
    steps: tuple[StepRecord, ...]
    observation_count: int
    action_count: int
    decision_time_s: float
    observation_time_s: float
    execution_time_s: float
    total_time_s: float
    context: AgentContext


class Runner:
    """Runs one task by interpreting commands from an injected policy."""

    def __init__(
        self,
        *,
        policy: AgentPolicy,
        observer: Observer,
        executor: ActionExecutor,
        task: str,
        max_actions: int = 8,
        max_observations: int = 8,
        max_context_observations: int = 2,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if not task.strip():
            raise ValueError("task must be a non-empty string")
        if max_actions < 1:
            raise ValueError("max_actions must be >= 1")
        if max_observations < 1:
            raise ValueError("max_observations must be >= 1")
        if max_context_observations < 1:
            raise ValueError("max_context_observations must be >= 1")
        self._policy = policy
        self._observer = observer
        self._executor = executor
        self._task = task
        self._max_actions = max_actions
        self._max_observations = max_observations
        self._max_context_observations = max_context_observations
        self._clock = clock

    async def run(self) -> RunResult:
        """Execute the policy loop until Finish, a limit, or an exception."""
        started = self._clock()
        context = AgentContext(task=self._task, started_at=time.time())
        steps: list[StepRecord] = []
        decision_time_s = 0.0
        observation_time_s = 0.0
        execution_time_s = 0.0
        outcome: Finish | None = None
        error: str | None = None

        try:
            while outcome is None:
                decision_started = self._clock()
                command = await self._policy.next_command(context)
                decision_s = self._clock() - decision_started
                decision_time_s += decision_s

                if isinstance(command, Finish):
                    outcome = command
                    steps.append(
                        StepRecord(
                            index=len(steps),
                            command="finish",
                            detail=command.reason,
                            decision_s=decision_s,
                            work_s=0.0,
                            success=command.success,
                        )
                    )
                elif isinstance(command, Observe):
                    if context.observation_count >= self._max_observations:
                        outcome = Finish(
                            reason=f"observation limit reached ({self._max_observations})",
                            success=False,
                        )
                        steps.append(self._limit_step(steps, outcome, decision_s))
                        continue
                    work_started = self._clock()
                    observation = await self._observer.observe(command.request)
                    work_s = self._clock() - work_started
                    observation_time_s += work_s
                    context = self._apply_observation(context, observation)
                    steps.append(
                        StepRecord(
                            index=len(steps),
                            command="observe",
                            detail=self._describe_observation(observation),
                            decision_s=decision_s,
                            work_s=work_s,
                            success=True,
                        )
                    )
                elif isinstance(command, Execute):
                    if context.action_count >= self._max_actions:
                        outcome = Finish(
                            reason=f"action limit reached ({self._max_actions})",
                            success=False,
                        )
                        steps.append(self._limit_step(steps, outcome, decision_s))
                        continue
                    work_started = self._clock()
                    result = await self._executor.execute(command.action)
                    work_s = self._clock() - work_started
                    execution_time_s += work_s
                    context = self._apply_action_result(context, command.action, result)
                    steps.append(
                        StepRecord(
                            index=len(steps),
                            command="execute",
                            detail=self._describe_action(command.action),
                            decision_s=decision_s,
                            work_s=work_s,
                            success=result.success,
                            error=result.error,
                        )
                    )
                else:
                    raise TypeError(f"policy returned an unsupported command: {type(command).__name__}")
        except Exception as exc:  # noqa: BLE001 - terminate the task, report the cause
            error = f"{type(exc).__name__}: {exc}"
            outcome = Finish(reason="terminated by exception", success=False)
            steps.append(
                StepRecord(
                    index=len(steps),
                    command="finish",
                    detail=error,
                    decision_s=0.0,
                    work_s=0.0,
                    success=False,
                    error=error,
                )
            )

        assert outcome is not None
        return RunResult(
            task=self._task,
            success=outcome.success,
            reason=outcome.reason,
            error=error,
            steps=tuple(steps),
            observation_count=context.observation_count,
            action_count=context.action_count,
            decision_time_s=decision_time_s,
            observation_time_s=observation_time_s,
            execution_time_s=execution_time_s,
            total_time_s=self._clock() - started,
            context=context,
        )

    def _apply_observation(self, context: AgentContext, observation: Observation) -> AgentContext:
        kept = (*context.observations, observation)[-self._max_context_observations :]
        return replace(
            context,
            observations=kept,
            observation_count=context.observation_count + 1,
            step_count=context.step_count + 1,
            observation_is_fresh=True,
        )

    def _apply_action_result(self, context: AgentContext, action: Action, result: ActionResult) -> AgentContext:
        return replace(
            context,
            last_action=action,
            last_result=result,
            action_count=context.action_count + 1,
            step_count=context.step_count + 1,
            observation_is_fresh=False,
        )

    @staticmethod
    def _limit_step(steps: list[StepRecord], outcome: Finish, decision_s: float) -> StepRecord:
        return StepRecord(
            index=len(steps),
            command="finish",
            detail=outcome.reason,
            decision_s=decision_s,
            work_s=0.0,
            success=False,
        )

    @staticmethod
    def _describe_observation(observation: Observation) -> str:
        size = len(observation.payload) if observation.payload is not None else 0
        return f"{observation.source}/{observation.kind} ({size} B)"

    @staticmethod
    def _describe_action(action: Action) -> str:
        """Describe an action without echoing user-typed text into logs."""
        parts: list[str] = []
        for key, value in action.args.items():
            if isinstance(value, (bool, int, float)):
                parts.append(f"{key}={value}")
            elif isinstance(value, str):
                parts.append(f"{key}=<{len(value)} chars>")
            else:
                parts.append(f"{key}=<{type(value).__name__}>")
        text = f"{action.space}.{action.kind}({', '.join(parts)})"
        return text if len(text) <= 160 else text[:157] + "..."
