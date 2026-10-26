# nano-cua：极简、可替换的异步 GUI Agent 基座

按 `HARNESS_MINIMAL_AGENT_BASE_PLAN.md`（V0.2）实现。核心只有一个**命令驱动的循环**（`Runner`）：观测、动作执行、决策（Actor）与策略（Policy）全部通过最小的 `typing.Protocol` 端口在启动时注入。当前唯一的具体实现是 **Playwright + Chromium + 页面截图 + 可替换 VLM**，完整跑通「观察 → 决策 → 执行 → 再观察 → 结束」的单任务闭环。

设计优先级：**正确的可替换边界 > 可控性与测试 > 代码简单 > 性能微优化 > 功能丰富性**。这里不是实验平台：没有收益评分、DVC、反事实回放、Wait Control、Benchmark、数据库、Web UI、动态插件框架或多智能体编排。

## 快速开始

要求：Python ≥ 3.12、[uv](https://docs.astral.sh/uv/)、git。

```bash
cd nano-cua
uv sync
uv run playwright install chromium      # 首次安装浏览器（约 200 MB）
uv run nano-cua                         # MockActor + 内置本地页面，无需 API Key
```

实际输出（本机一次真实运行）：

```text
nano-cua - minimal CUA base
  task   : Click the big button in the centre of the page, then finish.
  page   : file:///.../src/nano_cua/demo_page.html
  actor  : mock (scripted, 2 proposals, no API key)
  limits : max_actions=8, max_observations=8

steps:
   #  command  decision_ms   work_ms  detail
   0  observe         0.00    181.53  playwright/screenshot (259431 B)
   1  execute         0.01      8.13  browser.click(x=640, y=360)
   2  observe         0.00    112.19  playwright/screenshot (259933 B)
   3  finish          0.00      0.00  mock: clicked the centre button at (640, 360)

result : SUCCESS - mock: clicked the centre button at (640, 360)
timings: total=0.302s decision(policy+actor)=0.000s observation=0.294s execution=0.008s
counts : observations=2 actions=1 steps=4
```

`decision` = Policy 决策耗时（对 `BasicPolicy` 即 Actor/VLM 调用耗时），`observation` = 截图采集耗时，`execution` = 动作执行耗时，`total` = 整任务墙钟时间。

常用参数：

```bash
uv run nano-cua --headed                                  # 可视化观察流程
uv run nano-cua --save-screenshot .artifacts/demo.png     # 显式落盘最终截图（默认只在内存）
uv run nano-cua --url https://example.com --task "..."    # 打开任意 URL
uv run nano-cua --max-steps 4 --max-observations 6
uv run pytest -v                                          # 全部测试
```

如果本机没有安装 Playwright 的 Chromium，CLI 会自动回退到系统 Edge/Chrome（`--browser-channel msedge|chrome`，或环境变量 `NANO_CUA_BROWSER_CHANNEL`）。

### 接入真实 VLM（可选，阶段 C）

`VLMActor` 只依赖 OpenAI-compatible `POST /chat/completions`，不绑定任何模型平台：

```bash
export NANO_CUA_API_BASE="https://your-host/v1"   # 也可直接给完整 .../chat/completions
export NANO_CUA_MODEL="your-model-name"
export NANO_CUA_API_KEY="..."                     # 本地服务可留空
uv run nano-cua --actor vlm
```

没有 `NANO_CUA_API_BASE` + `NANO_CUA_MODEL` 时 `--actor auto`（默认）自动走 `MockActor`；设置后自动走 VLM。也可显式传 `--api-base/--model/--json-mode`，API Key 只从环境变量读取（避免进入 shell 历史）。

模型只能输出以下 JSON（白名单），其余内容在 `VLMActor` 边界被严格拒绝、不会执行：

```json
{"kind": "click", "x": 640, "y": 360}
{"kind": "type_text", "text": "hello"}
{"kind": "press_key", "key": "Control+A"}
{"kind": "finish", "reason": "task complete"}
```

不会执行模型生成的 Python / JavaScript / Shell；坐标越界、字段多余、类型错误、`press_key` 非法键名都会被拒绝。

## 架构

```text
                 AgentContext（不可变，观测历史上限可配）
                          │
     Policy.next_command(context) -> Observe | Execute | Finish
                          │
                ┌─────────▼──────────┐
                │       Runner       │  只解释命令：计数 / 上限 / 计时 / 异常终止
                └───┬───────────┬────┘
     Observe command│           │Execute command
                    ▼           ▼
              Observer     ActionExecutor          ← 两个独立端口
                    │           │
                    └─────┬─────┘
                 PlaywrightIO（一个对象实现两个端口，共享浏览器会话）

Actor.propose(context) -> Action | Finish
     BasicPolicy：首次 Observe；每次 Execute 后再 Observe；Actor 给出 Action 则
     Execute，给出 Finish 则结束
```

关键约束：**Runner 只有收到 `Observe` 命令时才调用 Observer**。Actor 和 Executor 都不能暗中刷新截图，`BasicPolicy` 的"每步观察"只是默认策略行为，可整体替换。

消息类型（`nano_cua/contracts.py`，全部 frozen dataclass）：

| 类型 | 字段 | 说明 |
| --- | --- | --- |
| `ObservationRequest` | `source`, `kind`, `metadata` | 显式观测请求；空 request = 默认观测 |
| `Observation` | `source`, `kind`, `payload`, `captured_at`, `metadata` | 不可变快照；截图/图片字节在内存，不自动落盘 |
| `Action` | `space`, `kind`, `args` | 通用动作消息；核心不解释语义 |
| `ActionResult` | `success`, `error`, `duration_s`, `output` | 执行结果与耗时 |
| `Observe` / `Execute` / `Finish` | — | Runner 认识的三种命令（`Finish` 也可由 Actor 返回） |
| `AgentContext` | `task`, 有界 `observations`, `last_action`, `last_result`, 计数, `observation_is_fresh`, `started_at` | 每步替换而非原地修改；不无上限累积截图 |

## 四个接口如何替换

四个端口都是 `typing.Protocol`（`@runtime_checkable`），构造时显式注入，无注册表、无反射、无 DI 容器。下面的替换都**不需要修改 Runner 或 BasicPolicy**。

### 1. Observer / 2. ActionExecutor

```python
from nano_cua import Action, ActionResult, Observation, ObservationRequest

class DomObserver:                       # 换观测来源：DOM / a11y / 桌面截图 / 复合观测
    async def observe(self, request: ObservationRequest) -> Observation:
        html = await page.content()
        return Observation(source="dom", kind="html", payload=html,
                           captured_at=time.time(), metadata={"url": page.url})

class DesktopExecutor:                   # 换动作空间：桌面操作 / 工具调用
    async def execute(self, action: Action) -> ActionResult:
        if action.space != "desktop":
            return ActionResult(success=False, error=f"unsupported space {action.space!r}")
        ...

runner = Runner(policy=my_policy, observer=DomObserver(), executor=DesktopExecutor(), task="...")
```

`tests/test_runner.py` 里的 `FakeObserver` / `FakeExecutor` 就是以这种方式整体替换真实 `PlaywrightIO` 的，核心源码零改动。

### 3. Actor

```python
from nano_cua import Action, AgentContext, Finish, Proposal

class MyActor:
    async def propose(self, context: AgentContext) -> Proposal:
        # 读取 context.task / last_observation / last_result，返回 Action 或 Finish
        return Finish(reason="done")
```

### 4. AgentPolicy（默认是 `BasicPolicy`）

```python
from nano_cua import AgentContext, Command, Execute, Finish, Observe, ObservationRequest

class MyPolicy:
    async def next_command(self, context: AgentContext) -> Command:
        if context.last_observation is None:
            return Observe(ObservationRequest())
        ...
```

替换 Policy 是改变「何时观察 / 何时决策 / 何时结束」的唯一正确位置；Runner 不需要认识任何具体策略。

## 未来「评分策略」的接入点（只保留插槽，未实现）

评分系统需要在「拿到候选动作」和「实际执行动作」之间插入，并且可能追加观测。本仓库**只通过可替换的 `AgentPolicy` 保留这个边界**，没有实现任何评分逻辑：

```python
class ScoringPolicy:                    # 未来扩展示意，本仓库未实现
    def __init__(self, actor, scorer):
        self.actor, self.scorer = actor, scorer

    async def next_command(self, context):
        if context.last_observation is None or not context.observation_is_fresh:
            return Observe(ObservationRequest())      # 追加观测仍然只是 Observe 命令
        candidate = await self.actor.propose(context)  # 候选动作
        if isinstance(candidate, Finish):
            return candidate
        cost = await self.scorer.evaluate(context, candidate)
        if cost > threshold:
            return Observe(ObservationRequest())      # 不执行，先补充观测
        return Execute(candidate)
```

要点：Runner 无需改动；Observer 仍然只在 `Observe` 命令下被调用；不存在的 `ScoreProvider`、复杂 Controller、收益计算逻辑一律不写。

## 目录

```text
nano-cua/
├── pyproject.toml
├── uv.lock
├── README.md
├── src/nano_cua/
│   ├── contracts.py          # Protocol、Observation/Action/Command/Result/Context
│   ├── runner.py             # 命令驱动循环 + 计数/上限/计时/异常反馈
│   ├── policy.py             # BasicPolicy：观察—决策—执行
│   ├── mock.py               # MockActor：离线脚本化 Actor
│   ├── cli.py                # 组合根：装配 adapter + 运行 demo
│   ├── demo_page.html        # 内置本地演示页面
│   └── adapters/
│       ├── browser_space.py  # 浏览器动作白名单与参数校验（两端共用）
│       ├── playwright_io.py  # Playwright 实现 Observer + ActionExecutor
│       └── vlm_actor.py      # OpenAI-compatible VLM → 严格结构化动作
└── tests/
    ├── test_runner.py        # Fake ports：循环、上限、异常、计时、上下文
    ├── test_browser_space.py # 动作白名单/参数校验
    ├── test_vlm_actor.py     # httpx.MockTransport，离线（无 Key/无网络）
    ├── test_separation.py    # 核心与 adapter 的静态隔离检查
    └── test_smoke.py         # 真实 Chromium + 本地 HTML + MockActor
```

## 测试

```bash
uv run pytest -v
```

本机结果：**93 passed**（约 6 秒，含 3 个真实 Chromium 冒烟测试）。覆盖与验收标准对应关系：

| 验收点 | 证据 |
| --- | --- |
| Fake 端口替换真实 I/O，核心零修改 | `tests/test_runner.py` 全部用例只用 Fake Observer/Executor |
| Execute 不会隐式截图 | `test_execute_never_triggers_observation`（Observer 调用次数为 0） |
| 只有 Observe 才取观测 | `test_command_loop_records_steps_and_context`、Runner 源码结构 |
| 核心无 Playwright/VLM SDK/动作语义耦合 | `tests/test_separation.py`（AST 检查 import + 字面量；子进程验证 `import nano_cua` 不加载 playwright/httpx） |
| 无 API Key 可运行 Demo 与单测 | `MockActor` 默认路径；VLM 单测使用 `httpx.MockTransport` |
| 四段耗时可见 | `RunResult.decision_time_s / observation_time_s / execution_time_s / total_time_s`，CLI 打印 |
| 无浏览器时可离线跑单测 | `tests/test_smoke.py` 在无浏览器时 `pytest.skip` |

## 明确未实现（按计划停在三阶段）

- 收益/评分算法、反事实重放、Wait Control、Benchmark、DVC、实验数据收集、数据库、人工协助、Dashboard/UI 控制台
- 多智能体编排、复杂 Planner/Memory、RAG、训练代码
- 观测仅单页截图（未来新增 Observer 即可接 DOM/a11y/桌面截图/复合观测）
- 动作仅 `click` / `type_text` / `press_key`（新增 ActionExecutor 或扩展 `browser_space.py` 白名单即可）
- 无 VLM 输出重试/修复循环：非法输出会让任务以异常终止并在结果中报告，而不是猜测执行
- 无元素定位、无自动等待策略；坐标动作按模型输出原样执行（合法性由边界校验）
- 单浏览器、单页面、单任务串行，无并发

## 已知限制与说明

- **耗时口径**：`decision` 包含 Policy 调用 + Actor（VLM HTTP）耗时；`observation` 仅截图采集；`execution` 仅动作执行；`total` 为整任务时间。
- **截图**：PNG 原图在内存中传递，未自动缩放/压缩；接真实 VLM 时 token 成本由 Observer/Actor 实现决定（可替换 Observer 做缩放）。
- **落盘**：默认不写截图；只有显式 `--save-screenshot` 才写单个文件，不开启 tracing。
- **环境代理**：某些 Windows 环境 `NO_PROXY` 含 `[::1]` 等条目时 httpx 0.28 无法解析环境代理，`VLMActor` 会自动退化为忽略环境代理的 HTTP client，并在 CLI 的 actor 行标注 `env proxy vars ignored`；需要代理时请修正环境变量。
- **浏览器**：优先 Playwright Chromium；缺失时 CLI 回退系统 Edge/Chrome，可用 `--browser-channel` 固定。
- **MockActor**：只按固定脚本行动（演示用：点击页面中心按钮 → finish），不代表任何策略能力。
