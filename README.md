# nano-cua：极简、可替换的异步 GUI Agent 基座

`nano-cua` 只做一件事：用**四个注入端口 + 一个命令驱动循环**跑单任务 Agent。基座里**不包含**任何具体观察空间（浏览器/OSWorld/桌面截图）或操作空间（鼠标/键盘/bash），也不包含 VLM SDK、CLI 或 demo——这些兼容层全部放在外部项目里，通过调用注入。

```text
观点：正确的可替换边界 > 可控性与测试 > 代码简单 > 性能微优化 > 功能丰富性
```

## 基座包含什么

```text
nano-cua/
├── pyproject.toml         # 运行时依赖为空（仅标准库）
├── src/nano_cua/
│   ├── contracts.py       # Protocol 端口 + Observation/Action/Command/Result/Context
│   ├── runner.py          # 只解释 Observe / Execute / Finish 的命令循环
│   ├── policy.py          # BasicPolicy：观察→Actor→执行
│   └── mock.py            # MockActor：离线脚本化 Actor（测试/最小 demo 用）
└── tests/
    ├── test_runner.py     # Fake 端口驱动核心：计数/上限/异常/计时/上下文/组注入
    └── test_separation.py # 静态证明基座无具体 I/O、动作语义与第三方依赖
```

基座**不包含**：

- 任何观察实现（Playwright 截图、OSWorld 截图、DOM/a11y、桌面截屏……）；
- 任何操作实现（click、type_text、press_key、鼠标、键盘、bash、pyautogui……）；
- 任何模型客户端 / system prompt / 动作解析器；
- CLI、demo 页面、浏览器依赖（`playwright`）或 HTTP 依赖（`httpx`）。

`pyproject.toml` 中 `dependencies = []`，只有 dev 依赖 `pytest` / `pytest-asyncio`。

## 四个端口

| 端口 | 协议 | 职责 |
| --- | --- | --- |
| `Observer` | `async observe(ObservationRequest) -> Observation` | 按显式命令提供观测 |
| `ActionExecutor` | `async execute(Action) -> ActionResult` | 执行通用动作消息 |
| `Actor` | `async propose(AgentContext) -> Action \| Finish` | 把上下文/图片变成候选动作或结束 |
| `AgentPolicy` | `async next_command(AgentContext) -> Command` | 决定下一步：观察 / 执行 / 结束 |

Runner 只解释 `Observe / Execute / Finish` 三类命令，负责计数、上限、分阶段计时与异常终止反馈；**只有收到 `Observe` 命令才调用 Observer**，Actor/Executor 不能暗中刷新观测。

## 快速开始（基座本身）

```bash
cd nano-cua
uv sync
uv run pytest -q
```

基座测试不需要浏览器、不需要模型、不需要 API Key。

## 通过外部兼容层接入任意观察/操作空间

兼容层按“组”接入：一个组通常同时提供该环境的观察和动作实现（可以是同一个对象实现两个端口，也可以是一对 observer/executor），再加上模型需要的 system prompt 和动作解析器。基座只依赖 Protocol，因此不修改任何基座代码。

```python
from nano_cua import BasicPolicy, Runner

# 外部项目提供的组（示意）：Playwright/Chromium 截图 + 浏览器动作
from nano_cua_extensions.playwright_browser import PlaywrightIO
from nano_cua_extensions.vlm import VLMActor
from nano_cua_extensions.playwright_browser import BROWSER_SYSTEM_PROMPT, browser_proposal_parser

group = await PlaywrightIO.launch(headless=True, url="file:///demo.html")
actor = VLMActor(
    base_url="https://your-host/v1",
    model="your-model",
    api_key="...",
    system_prompt=BROWSER_SYSTEM_PROMPT,      # 由动作组提供
    proposal_parser=browser_proposal_parser,  # 由动作组提供
)
runner = Runner(policy=BasicPolicy(actor), observer=group, executor=group, task="...")
result = await runner.run()
await group.close()
```

要接 OSWorld、桌面鼠标键盘、bash 或别的环境，只需在基座外新增一个组：

1. 实现 `observe()`，把该环境的截图/无障碍树/文本包成 `Observation(source, kind, payload, captured_at, metadata)`；
2. 实现 `execute()`，把通用 `Action(space, kind, args)` 翻译成该环境动作；
3. 给 VLM Actor 提供该动作空间的 system prompt 和 `proposal_parser(payload, observation) -> Action | Finish`；
4. 组合时注入即可——Runner、BasicPolicy、contracts 均不改。

消息类型（`contracts.py`，frozen dataclass，mapping 只读）：

| 类型 | 字段 | 说明 |
| --- | --- | --- |
| `ObservationRequest` | `source`, `kind`, `metadata` | 显式观测请求 |
| `Observation` | `source`, `kind`, `payload`, `captured_at`, `metadata` | 不可变快照；图片/文本在内存 |
| `Action` | `space`, `kind`, `args` | 通用动作消息；基座不解释语义 |
| `ActionResult` | `success`, `error`, `duration_s`, `output` | 执行结果与耗时 |
| `Observe` / `Execute` / `Finish` | — | Runner 认识的三种命令 |
| `AgentContext` | `task`, 有界 `observations`, `last_action`, `last_result`, 计数, `observation_is_fresh` | 每步替换而非原地修改 |

## 评分策略的接入点

`AgentPolicy` 是插入评分器的唯一位置，已保留、无需改 Runner：

```python
class ScoringPolicy:                  # 未来扩展示意，本仓库未实现评分
    async def next_command(self, context):
        if context.last_observation is None or not context.observation_is_fresh:
            return Observe(ObservationRequest())
        candidate = await self.actor.propose(context)
        if isinstance(candidate, Finish):
            return candidate
        # 在这里打分/否决候选动作；需要更多证据时返回 Observe 而不是 Execute
        return Execute(candidate) if acceptable(candidate) else Observe(ObservationRequest())
```

基座只保留这个边界，不包含收益/评分逻辑、Benchmark、数据库、Web UI 或多智能体编排。

## 外部兼容层放在哪里

具体实现已从基座仓库**解耦并移出**，放在同级的伴随项目 `nano-cua-extensions/`（不进入本仓库、不需要修改基座）：

| 位置 | 内容 |
| --- | --- |
| `nano_cua_extensions.playwright_browser` | Playwright/Chromium 组：截图 Observer + 浏览器动作 Executor + 动作校验 + prompt/parser |
| `nano_cua_extensions.vlm` | 通用 OpenAI-compatible `VLMActor`（动作空间无关，prompt/parser 注入） |
| `nano_cua_extensions.demo_cli` | 组合 demo（`uv run nano-cua-demo`，MockActor 默认可跑，无需 Key） |

这样基座保持零具体兼容层、零运行时依赖；Playwright/VLM/CLI 能力没有丢失，只是变成了外部依赖基座的可替换组。新的 OSWorld/桌面/bash 组也按同样方式放在外部项目中。

## 与上一版的差异（v0.2.0）

- 删除基座内的 `nano_cua.adapters.*`、`nano_cua.cli`、demo 页面与 `nano-cua` console script；
- 运行时依赖由 `playwright` + `httpx` 变为 **0**；
- 具体兼容层完整移到伴随项目 `nano-cua-extensions`（代码保留、测试迁移）；
- `VLMActor` 改为**动作空间无关**：system prompt 与 proposal parser 由动作组注入。
