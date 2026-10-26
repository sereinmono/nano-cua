"""Core-loop tests: Fake ports only, no browser and no model.

These tests are the proof that ``Runner`` and ``BasicPolicy`` work against any
Observer / ActionExecutor / Actor: the fakes below replace the real Playwright
I/O entirely and the core sources are not touched.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import FrozenInstanceError

import pytest

from nano_cua import (
    Action,
    ActionResult,
    Actor,
    AgentContext,
    AgentPolicy,
    BasicPolicy,
    Execute,
    Finish,
    MockActor,
    Observation,
    ObservationRequest,
    Observe,
    Observer,
    RunResult,
    Runner,
)
from nano_cua.contracts import ActionExecutor


def make_observation(tag: str) -> Observation:
    return Observation(
        source="fake",
        kind="image",
        payload=tag.encode("utf-8"),
        captured_at=time.time(),
        metadata={"width": 100, "height": 50},
    )


class FakeObserver:
    def __init__(self, *, delay_s: float = 0.0) -> None:
        self.calls = 0
        self.requests: list[ObservationRequest] = []
        self.delay_s = delay_s

    async def observe(self, request: ObservationRequest) -> Observation:
        self.calls += 1
        self.requests.append(request)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return make_observation(f"frame-{self.calls}")


class FakeExecutor:
    def __init__(self, *, delay_s: float = 0.0, succeed: bool = True, error: str | None = None) -> None:
        self.calls: list[Action] = []
        self.delay_s = delay_s
        self.succeed = succeed
        self.error = error

    async def execute(self, action: Action) -> ActionResult:
        self.calls.append(action)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return ActionResult(success=self.succeed, error=self.error)


class ScriptedPolicy:
    """Returns a fixed command list, then finishes."""

    def __init__(self, commands: list) -> None:
        self._commands = list(commands)
        self.index = 0
        self.contexts: list[AgentContext] = []

    async def next_command(self, context: AgentContext):
        self.contexts.append(context)
        if self.index >= len(self._commands):
            return Finish(reason="script exhausted", success=True)
        command = self._commands[self.index]
        self.index += 1
        return command


class RecordingActor:
    """Actor double that records every context it was asked about."""

    def __init__(self, proposals: list) -> None:
        self._proposals = list(proposals)
        self.contexts: list[AgentContext] = []

    async def propose(self, context: AgentContext):
        self.contexts.append(context)
        return self._proposals[len(self.contexts) - 1]


def build_runner(policy, *, observer=None, executor=None, **kwargs) -> tuple[Runner, FakeObserver, FakeExecutor]:
    observer = observer if observer is not None else FakeObserver()
    executor = executor if executor is not None else FakeExecutor()
    runner = Runner(policy=policy, observer=observer, executor=executor, task="test task", **kwargs)
    return runner, observer, executor


async def test_command_loop_records_steps_and_context():
    observer = FakeObserver()
    executor = FakeExecutor()
    policy = ScriptedPolicy(
        [
            Observe(),
            Execute(Action(space="fake", kind="poke", args={"n": 1})),
            Finish(reason="done", success=True),
        ]
    )
    runner, observer, executor = build_runner(policy, observer=observer, executor=executor)
    result = await runner.run()

    assert isinstance(result, RunResult)
    assert result.success is True
    assert result.reason == "done"
    assert [step.command for step in result.steps] == ["observe", "execute", "finish"]
    assert result.observation_count == 1
    assert result.action_count == 1
    assert observer.calls == 1
    assert len(executor.calls) == 1
    assert result.context.last_observation is not None
    assert result.context.last_observation.payload == b"frame-1"
    assert result.context.last_result is not None and result.context.last_result.success
    # The action happened after the observation, so the evidence is now stale.
    assert result.context.observation_is_fresh is False


async def test_execute_never_triggers_observation():
    """Acceptance: screenshots happen only for explicit Observe commands."""
    policy = ScriptedPolicy([Execute(Action(space="fake", kind="poke")), Finish(reason="done")])
    runner, observer, executor = build_runner(policy)

    result = await runner.run()

    assert result.success is True
    assert observer.calls == 0
    assert result.observation_count == 0
    assert len(executor.calls) == 1
    assert [step.command for step in result.steps] == ["execute", "finish"]


async def test_observation_limit_terminates_the_task():
    policy = ScriptedPolicy([Observe() for _ in range(5)])
    observer = FakeObserver()
    runner, observer, _ = build_runner(policy, observer=observer, max_observations=2)

    result = await runner.run()

    assert result.success is False
    assert "observation limit" in result.reason
    assert observer.calls == 2
    assert [step.command for step in result.steps] == ["observe", "observe", "finish"]


async def test_action_limit_terminates_the_task():
    policy = ScriptedPolicy([Execute(Action(space="fake", kind="poke")) for _ in range(5)])
    executor = FakeExecutor()
    runner, _, executor = build_runner(policy, executor=executor, max_actions=2)

    result = await runner.run()

    assert result.success is False
    assert "action limit" in result.reason
    assert len(executor.calls) == 2


async def test_action_logs_do_not_echo_typed_text():
    policy = ScriptedPolicy(
        [
            Execute(Action(space="fake", kind="type", args={"text": "hunter2", "blob": "x" * 100})),
            Finish(reason="done"),
        ]
    )
    runner, _, _ = build_runner(policy)

    result = await runner.run()

    execute_step = next(step for step in result.steps if step.command == "execute")
    assert "hunter2" not in execute_step.detail
    assert "text=<7 chars>" in execute_step.detail
    assert "blob=<100 chars>" in execute_step.detail


async def test_observer_exception_is_reported_as_termination():
    class ExplodingObserver:
        async def observe(self, request: ObservationRequest) -> Observation:
            raise RuntimeError("capture failed")

    runner, _, _ = build_runner(ScriptedPolicy([Observe()]), observer=ExplodingObserver())

    result = await runner.run()

    assert result.success is False
    assert result.reason == "terminated by exception"
    assert result.error == "RuntimeError: capture failed"
    assert result.steps[-1].command == "finish"
    assert result.steps[-1].error == "RuntimeError: capture failed"


async def test_executor_exception_is_reported_as_termination():
    class ExplodingExecutor:
        async def execute(self, action: Action) -> ActionResult:
            raise RuntimeError("execution failed")

    policy = ScriptedPolicy([Execute(Action(space="fake", kind="poke"))])
    runner, _, _ = build_runner(policy, executor=ExplodingExecutor())

    result = await runner.run()

    assert result.success is False
    assert result.error == "RuntimeError: execution failed"


async def test_unsupported_command_is_reported_as_termination():
    class BrokenPolicy:
        async def next_command(self, context: AgentContext):
            return object()

    runner, _, _ = build_runner(BrokenPolicy())

    result = await runner.run()

    assert result.success is False
    assert result.error is not None and "TypeError" in result.error


async def test_context_keeps_only_recent_observations():
    policy = ScriptedPolicy([Observe(), Observe(), Observe(), Finish()])
    observer = FakeObserver()
    runner, observer, _ = build_runner(policy, observer=observer, max_context_observations=2)

    result = await runner.run()

    assert result.observation_count == 3
    assert len(result.context.observations) == 2
    assert [observation.payload for observation in result.context.observations] == [b"frame-2", b"frame-3"]
    # Earlier contexts were not mutated by later captures.
    assert policy.contexts[0].observations == ()
    assert [observation.payload for observation in policy.contexts[-1].observations] == [b"frame-2", b"frame-3"]


async def test_failed_action_result_is_feedback_for_the_policy():
    policy = ScriptedPolicy([Execute(Action(space="fake", kind="poke")), Finish(reason="done")])
    executor = FakeExecutor(succeed=False, error="element not found")
    runner, _, executor = build_runner(policy, executor=executor)

    await runner.run()

    assert policy.contexts[1].last_result is not None
    assert policy.contexts[1].last_result.success is False
    assert policy.contexts[1].last_result.error == "element not found"


async def test_timings_are_measured_separately():
    observer = FakeObserver(delay_s=0.01)
    executor = FakeExecutor(delay_s=0.01)
    policy = ScriptedPolicy([Observe(), Execute(Action(space="fake", kind="poke")), Finish()])
    runner, _, _ = build_runner(policy, observer=observer, executor=executor)

    result = await runner.run()

    assert result.observation_time_s >= 0.01
    assert result.execution_time_s >= 0.01
    assert result.decision_time_s >= 0.0
    assert result.total_time_s >= result.observation_time_s + result.execution_time_s


async def test_basic_policy_with_mock_actor_runs_observe_act_observe_finish():
    actor = RecordingActor(
        [
            Action(space="fake", kind="poke", args={"n": 1}),
            Finish(reason="mock done", success=True),
        ]
    )
    policy = BasicPolicy(actor)
    runner, observer, executor = build_runner(policy)

    result = await runner.run()

    assert result.success is True
    assert result.reason == "mock done"
    assert [step.command for step in result.steps] == ["observe", "execute", "observe", "finish"]
    assert observer.calls == 2
    assert len(executor.calls) == 1
    # The actor is only asked once fresh evidence exists.
    assert len(actor.contexts) == 2
    assert all(context.last_observation is not None for context in actor.contexts)
    assert all(context.observation_is_fresh for context in actor.contexts)
    assert actor.contexts[1].last_result is not None
    assert actor.contexts[1].last_result.success
    assert result.observation_count == 2


async def test_basic_policy_can_finish_without_acting():
    actor = MockActor([Finish(reason="nothing to do", success=True)])
    runner, observer, executor = build_runner(BasicPolicy(actor))

    result = await runner.run()

    assert result.success is True
    assert result.reason == "nothing to do"
    assert [step.command for step in result.steps] == ["observe", "finish"]
    assert observer.calls == 1
    assert executor.calls == []


async def test_mock_actor_is_offline_and_scripted():
    actor = MockActor([Finish(reason="done")])
    assert isinstance(actor, Actor)
    assert actor.proposals_left == 1
    proposal = await actor.propose(AgentContext(task="t"))
    assert isinstance(proposal, Finish)
    assert actor.proposals_left == 0
    assert (await actor.propose(AgentContext(task="t"))).reason == "mock script exhausted"


def test_fake_ports_satisfy_the_protocols():
    assert isinstance(FakeObserver(), Observer)
    assert isinstance(FakeExecutor(), ActionExecutor)
    assert isinstance(MockActor([Finish()]), Actor)
    assert isinstance(BasicPolicy(MockActor([Finish()])), AgentPolicy)


def test_agent_context_is_immutable():
    context = AgentContext(task="t")
    with pytest.raises(FrozenInstanceError):
        context.step_count = 1  # type: ignore[misc]


def test_runner_rejects_bad_limits():
    policy = ScriptedPolicy([])
    with pytest.raises(ValueError):
        Runner(policy=policy, observer=FakeObserver(), executor=FakeExecutor(), task="t", max_actions=0)
    with pytest.raises(ValueError):
        Runner(policy=policy, observer=FakeObserver(), executor=FakeExecutor(), task="t", max_observations=0)
    with pytest.raises(ValueError):
        Runner(policy=policy, observer=FakeObserver(), executor=FakeExecutor(), task="   ")
