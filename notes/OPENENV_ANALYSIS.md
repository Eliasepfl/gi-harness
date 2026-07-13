# OpenEnv / Agent World Model — deep analysis and build recommendation

> Analysis date: 2026-07-13. All claims below were verified against the live repositories
> (file paths, class names and signatures quoted from source via the GitHub API and
> `raw.githubusercontent.com`). Where the source and the marketing/README differ, the
> **source** wins and the discrepancy is flagged.

## TL;DR recommendation

- **Do NOT reuse the AWM repo** (`Snowflake-Labs/agent-world-model`). It synthesizes
  *SQL-database-backed, MCP tool-use* environments and grades them with *LLM judges*.
  Nothing in it is physics; nothing in it is reusable for us; and it ships with **no
  license file**, so copying its code is legally unsafe. Its only relevance is that its
  infra was merged into OpenEnv (as one env among ~36).
- **DO evaluate and (selectively) adopt OpenEnv** (`huggingface/OpenEnv`, BSD-3-Clause).
  It is a clean, Gym-style **client/server environment standard** (`reset()`/`step()`/
  `state()`) with a real training story (TRL GRPO) and — crucially — it **already hosts
  physics/control environments** (`dm_control`/MuJoCo, `sumo_rl`, `atari`, `grid_world`,
  `maze`, `snake`). Our generated physics games fit its abstraction cleanly.
- Concretely: wrap a v2 game module as an OpenEnv `Environment` where
  `reset(seed)` = `World(seed)` + `game.build(world)`; `step(action)` = one CONTRACTS
  decision tick (`act` + K=6 `world.step(1)` + `on_step`); `observation` = `world.query`
  snapshot + latched checkpoints; `reward` = checkpoint-latch events (+ terminal success
  bonus); `done` = success/failure/budget. This is a ~4–6 day adapter for a working,
  locally-served (uvicorn, **no Docker needed** for local dev) eval env; full RL training
  integration is a separate, larger effort.
- **Adopt narrowly**: the plain `Environment` + `Action`/`Observation` types + `create_app`
  + `EnvClient`. **Skip**: the MCP path, the LLM-judge `Rubric` reward machinery, the
  Gradio web UI, and HF-Hub auto-discovery — we do not need them and they carry weight.

---

## 1. Repo 1 — `Snowflake-Labs/agent-world-model` (AWM)

### 1.1 Metadata / maturity
- Description: *"Agent World Model: Infinity Synthetic Environments for Agentic
  Reinforcement Learning"*.
- **License: none declared** (no `LICENSE` file; `pyproject.toml` has no `license`
  field). Treat all code as all-rights-reserved.
- Python `>=3.12`. Language: Python. ~407 stars, **3 open issues**, created 2026-02-08,
  last push 2026-05-28 (i.e. essentially dormant since the OpenEnv merge). Small repo.

### 1.2 Repo structure (complete)
```
awm/__init__.py  awm/cli.py  awm/gpt.py  awm/prompts.py  awm/tools.py
awm/core/{agent,check,db,env,pipeline,reset,sample,scenario,server,spec,task,
          test_env,verifier,verify}.py
awm/eval/{__init__,infer_eval}.py
mcp-adapted-bench   (git submodule, .gitmodules)
outputs/seed_scenario.jsonl
pyproject.toml  uv.lock  README.md  figures/logo.png
```

### 1.3 What the pipeline code actually implements
Per the README and `awm/core/env.py`, AWM is a **generation pipeline**, not a runtime env
library. The five stages: (1) scenario generation from seed embeddings, (2) 10 tasks per
scenario, (3) **SQL schema + initial DB state** synthesis, (4) **API spec + MCP-compliant
Python env code** synthesis, (5) **verifier** synthesis (SQL-augmented LLM-judge *or*
pure-code judge). `awm/core/env.py` is the *orchestrator* of code generation and testing —
it contains `generate_all_environments(args: Config)`, `test_all_environments`,
`batch_test_environments(...) -> list[tuple[bool, str, dict]]`, and an LLM-based
`summarize_errors(...)` feedback loop. It explicitly **does not implement a standard RL
environment**; return values are `(ok, err, config)` triplets, not
observation/action/reward.

### 1.4 The environment abstraction they use
An AWM "environment" is a **FastAPI + `fastapi-mcp` server** exposing generated tools over
the **Model Context Protocol** (`list_tools` / `call_tool`), backed by a SQLAlchemy/SQLite
database. Dependencies: `fastapi==0.115.12`, `fastapi-mcp==0.4.0`, `mcp-agent==0.2.6`,
`sqlalchemy==2.0.41`, `openai`, `tiktoken`, `numpy`, `loguru`, etc. Serving is per-env
subprocess MCP servers; CLI is `awm env start`, `awm env check_all`. Verification runs
*after* an agent finishes and compares final DB state (LLM-judge or code).

### 1.5 Verdict on AWM
**Poorly matched to our project — as suspected.** The paper/repo is tool-use/API/SQL
oriented. It shares zero substrate with a pymunk physics harness, and our G0–G3
deterministic oracles + latched checkpoints are a *stronger, hack-resistant* verification
story than AWM's LLM judges. **We take nothing from AWM directly.** Its historical value
is only that its infra was folded into OpenEnv.

---

## 2. Repo 2 — `huggingface/OpenEnv`

### 2.1 Metadata / maturity
- Description: *"An interface library for RL post training with environments."*
- **License: BSD-3-Clause** (permissive — safe to depend on and to vendor).
- Version `0.4.2.dev0` — **pre-1.0, very active** (last push 2026-07-12, ~2.4k stars,
  **89 open issues**). Python `>=3.10` (we are on 3.12 — fine). Docs:
  https://huggingface.co/docs/openenv/index.
- Package layout: `src/` with `package-dir = {"" = "src"}`; the importable package is
  `openenv`, core lives in **`openenv.core`**.

### 2.2 Core framework — the actual Environment spec
Everything below is quoted from source under `src/openenv/core/`.

**Server-side base class** (`openenv/core/env_server/interfaces.py`):
```python
class Environment(ABC, Generic[ActT, ObsT, StateT]):
    SUPPORTS_CONCURRENT_SESSIONS: bool = False
    REQUIRES_SINGLE_THREAD_EXECUTOR: bool = False
    rubric: Optional["Rubric"]

    def __init__(self, transform=None, rubric=None): ...

    @abstractmethod
    def reset(self, seed: Optional[int] = None,
              episode_id: Optional[str] = None, **kwargs) -> ObsT: ...
    @abstractmethod
    def step(self, action: ActT, timeout_s: Optional[float] = None, **kwargs) -> ObsT: ...
    @property
    @abstractmethod
    def state(self) -> StateT: ...
    # + async variants reset_async / step_async
```
Note: server `step()` returns an **Observation** (`ObsT`) that itself carries `.done` and
`.reward`. There is no server-side `StepResult`.

**Types** (`openenv/core/env_server/types.py`) — all **Pydantic v2 `BaseModel`**:
```python
class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, arbitrary_types_allowed=True)
    metadata: Dict[str, Any] = Field(default_factory=dict)

class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", ...)
    done: bool = Field(default=False)
    reward: bool | int | float | None = Field(default=None)
    metadata: Dict[str, Any] = Field(default_factory=dict)

class State(BaseModel):
    model_config = ConfigDict(extra="allow", ...)
    episode_id: Optional[str] = None
    step_count: int = Field(default=0, ge=0)
```
(`extra="forbid"` on Action/Observation means we must **declare** our extra fields as
subclass fields — we cannot smuggle arbitrary keys.)

**Client-side result** (`openenv/core/client_types.py`):
```python
@dataclass
class StepResult(Generic[ObsT]):
    observation: ObsT
    reward: Optional[float] = None
    done: bool = False
    metadata: Optional[Dict[str, Any]] = None
```

**HTTP server** (`openenv/core/env_server/http_server.py`): `create_app(env_factory,
action_cls, observation_cls, env_name=..., max_concurrent_envs=...)` builds a FastAPI app
(`HTTPEnvServer` under the hood) exposing:
`POST /reset`, `POST /step`, `GET /state`, `GET /health`, `GET /metadata`,
`GET /schema`, `WS /ws`, `POST /mcp`. Actions are deserialized with `deserialize_action`,
observations serialized with `serialize_observation`. (`/reset` and `/step` register only
in "simulation" mode.)

**Client base** (`openenv/core/env_client.py`): `class EnvClient` (generic over
`[ActT, ObsT, StateT]`), `__init__(base_url=None, connect_timeout_s=10.0,
message_timeout_s=60.0, ..., provider=None, mode=None)`, plus factory classmethods
`from_docker_image(image, provider=None, **kw)` and
`from_env(repo_id, *, use_docker=True, provider=None, **kw)`. `reset(**kwargs)` and
`step(action, **kwargs)` dispatch over WebSocket/HTTP and call the subclass hooks
`_step_payload(action) -> dict` and `_parse_result(data) -> StepResult[ObsT]`. A `.sync()`
wrapper gives a synchronous client.

**So the real transport is FastAPI over HTTP + a WebSocket session channel** (the README's
"Gymnasium-style" framing is accurate; each env instance runs in its own process/container).

### 2.3 Concrete env template we should copy — `grid_world_env`
This is the cleanest **non-MCP, pure-Python** example (no heavy deps), and it is almost
exactly our shape. Per-env package layout (identical across all envs):
```
envs/grid_world_env/
  README.md  __init__.py  client.py  models.py  openenv.yaml  pyproject.toml  uv.lock
  server/{__init__.py, app.py, grid_world_environment.py, Dockerfile, requirements.txt}
```
`models.py`:
```python
class GridWorldAction(Action):
    action: MoveAction  # enum UP/DOWN/LEFT/RIGHT

class GridWorldObservation(Observation):   # inherits done, reward, metadata
    x: int; y: int; message: str = ""
    reward: float = 0.0
    done: bool = False
```
`client.py`:
```python
class GridWorldEnv(EnvClient[GridWorldAction, GridWorldObservation, State]):
    def _step_payload(self, action): return action.model_dump()
    def _parse_result(self, data) -> StepResult[GridWorldObservation]:
        return StepResult(observation=GridWorldObservation(**data["observation"]),
                          reward=data["reward"], done=data["done"],
                          metadata=data.get("metadata", {}))
```
`server/app.py` (pattern, confirmed on `agent_world_model_env`):
```python
app = create_app(env_factory, GIAction, GIObservation, env_name="...")
# uvicorn.run(app, host="0.0.0.0", port=8000)
```
The **Atari env** confirms the exact loop we need: `AtariAction(action_id: int)`;
`step()` maps the action, **runs it across `frameskip` frames accumulating reward**,
increments the step counter, and returns an `AtariObservation` carrying `done` + reward.
That "one decision → K inner sim ticks → accumulated reward → done" shape *is* our
`act` + 6× `world.step(1)` runner.

### 2.4 Packaging / serving / ecosystem
- **Per-env package** (`openenv init <name>` scaffolds it): `models.py`, `client.py`,
  `server/{app.py, <name>_environment.py, Dockerfile, requirements.txt}`, `openenv.yaml`,
  `pyproject.toml`. Core deps kept minimal; heavy deps (torch/numpy/etc.) live per-env.
- **~36 envs today**, incl. control/physics: `dm_control_env` (MuJoCo), `sumo_rl_env`,
  `atari_env`, `grid_world_env`, `maze_env`, `snake_env`, `connect4_env`, `chess_env`,
  `openspiel_env`, plus tool-use/agentic ones (`browsergym`, `coding`, `terminus`,
  `agent_world_model_env`, …).
- **CI/deploy**: GitHub Actions for docker builds, PyPI/TestPyPI publish, and
  `deploy-hf-env` / `manage-hf-collection` (envs distribute as **HF Spaces**).
- Container providers: `LocalDockerProvider`, `DockerSwarmProvider`, `KubernetesProvider`
  (for isolation + batched/parallel rollouts).

### 2.5 Training side
- Docs (`docs/source/guides/rl-integration.md`, tutorials `wordle-grpo`,
  `rl-training-2048`, `sft-warmup`) show **TRL / GRPO** as the primary, implemented
  integration; **torchforge, SkyRL** listed as planned. Rollout loop:
```python
env = AutoEnv.from_env("my-env"); Action = AutoAction.from_env("my-env")
with env.sync() as client:
    result = client.reset()
    while not result.done:                 # StepResult.done
        action = policy(result.observation)
        result = client.step(action)
        learner.record(result.reward)
```
- Reward flows straight out of `StepResult.reward` into the trainer — which is exactly
  where our **checkpoint-latch dense reward** would land.

### 2.6 State of the AWM → OpenEnv merge
Complete and shipped. OpenEnv contains `envs/agent_world_model_env/` (full
`client.py` / `models.py` / `server/{app.py, awm_environment.py, ...}`) and
`docs/source/environments/agent_world_model.md`. In OpenEnv, the AWM env is implemented as
an **MCP env**: `AWMEnvironment(Environment)` in `server/awm_environment.py` imports from
`openenv.core.env_server.{interfaces,types,mcp_types}`, its `reset(...)` takes
`scenario/task_idx/reward_config/llm_*` kwargs, and `step()` dispatches
`ListToolsAction`/`CallToolAction` (with special `done`/`verify` tools); the client
`AWMEnv(MCPToolClient)` overrides `_parse_result`. The AWM README states the infra was
"merged into meta-pytorch/OpenEnv in May 2026" (OpenEnv is the Meta PyTorch + Hugging Face
effort now hosted at `huggingface/OpenEnv`). **Takeaway: the merge productized AWM as one
tool-use env; it did not add anything physics-related that we'd want.**

---

## 3. Build recommendation

### 3.1 How our v2 game modules wrap as an OpenEnv env
Create a **single generic env package** `envs/gi_physics_env/` whose `Environment` loads
*any* generated game module (path via `reset(**kwargs)` or env var) and drives it with the
CONTRACTS runner. Our `world.query()` already returns JSON-ready dicts, and our checkpoints
already are a dense programmatic signal — the fit is direct.

Adapter sketch **against the real interface** (verified signatures):

```python
# envs/gi_physics_env/models.py
from openenv.core.env_server.types import Action, Observation, State
from pydantic import Field

class GIAction(Action):
    action: str = Field(..., description="one of the game's declared ACTIONS")

class GIObservation(Observation):                 # inherits done, reward, metadata
    world: dict = Field(default_factory=dict)      # {name: world.query(name)} snapshot
    checkpoints: dict = Field(default_factory=dict)  # {name: latched_tick | None}
    actions: list[str] = Field(default_factory=list) # the game's action menu (from reset)
    tick: int = 0
    outcome: str | None = None                     # "success" | "failure" | None

class GIState(State):
    game_title: str = ""
```

```python
# envs/gi_physics_env/server/gi_environment.py
from openenv.core.env_server.interfaces import Environment
from ..models import GIAction, GIObservation, GIState
from harness.world import World          # our substrate (vendored or pip-installed)

K = 6            # physics steps per decision tick (CONTRACTS §2)
MAX_TICKS = 800  # step budget

class GIEnvironment(Environment[GIAction, GIObservation, GIState]):
    # each session gets its OWN instance via the factory -> own pymunk Space
    SUPPORTS_CONCURRENT_SESSIONS = True
    REQUIRES_SINGLE_THREAD_EXECUTOR = True   # pymunk C ext: keep each Space single-threaded

    def __init__(self, game_path: str):
        super().__init__()
        self._game = _load_game_module(game_path)   # TITLE/ACTIONS/build/act/on_step/...
        self._state = GIState(game_title=self._game.TITLE)
        self._latched, self._tick = {}, 0

    def reset(self, seed=None, episode_id=None, **kwargs) -> GIObservation:
        self.world = World(seed=seed or 0)
        self._game.build(self.world)                          # builds + world.control(...)
        self._tick = 0
        self._latched = {k: None for k in self._game.checkpoints(self.world)}
        self._state = GIState(episode_id=episode_id, step_count=0,
                              game_title=self._game.TITLE)
        return self._observe(reward=0.0, done=False, outcome=None)

    def step(self, action: GIAction, timeout_s=None, **kwargs) -> GIObservation:
        self._game.act(self.world, action.action)             # 1 action's effect
        on_step = getattr(self._game, "on_step", None)
        for _ in range(K):
            self.world.step(1)
            if on_step: on_step(self.world)
        self._tick += 1
        self._state.step_count = self._tick

        # runner-owned checkpoint latching (predicates stay pure) -> dense reward
        newly = 0
        for name, hit in self._game.checkpoints(self.world).items():
            if hit and self._latched.get(name) is None:
                self._latched[name] = self._tick; newly += 1

        failed = getattr(self._game, "failure", lambda w: False)(self.world)
        won    = self._game.success(self.world)
        done   = won or failed or self._tick >= MAX_TICKS
        reward = float(newly) + (10.0 if won else 0.0) + (-5.0 if failed else 0.0)
        outcome = "success" if won else "failure" if failed else None
        return self._observe(reward, done, outcome)

    @property
    def state(self) -> GIState:
        return self._state

    def _observe(self, reward, done, outcome) -> GIObservation:
        snap = {n: self.world.query(n) for n in self.world.entities()}
        return GIObservation(done=done, reward=reward, world=snap,
                             checkpoints=dict(self._latched),
                             actions=list(self._game.ACTIONS),
                             tick=self._tick, outcome=outcome)
```

```python
# envs/gi_physics_env/server/app.py
import uvicorn
from openenv.core.env_server.http_server import create_app
from ..models import GIAction, GIObservation
from .gi_environment import GIEnvironment

GAME = "scenes/games/<generated>.py"     # or read from reset kwargs / env var
app = create_app(lambda: GIEnvironment(GAME), GIAction, GIObservation,
                 env_name="gi_physics_env")
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)   # Windows-friendly; no Docker required
```

```python
# envs/gi_physics_env/client.py
from openenv.core.env_client import EnvClient
from openenv.core.client_types import StepResult
from openenv.core.env_server.types import State
from .models import GIAction, GIObservation

class GIPhysicsEnv(EnvClient[GIAction, GIObservation, State]):
    def _step_payload(self, action): return action.model_dump()
    def _parse_result(self, data) -> StepResult[GIObservation]:
        return StepResult(observation=GIObservation(**data["observation"]),
                          reward=data["reward"], done=data["done"],
                          metadata=data.get("metadata", {}))
```

Usage (identical to `grid_world`/TRL loop):
```python
with GIPhysicsEnv(base_url="http://localhost:8000").sync() as env:
    r = env.reset(seed=0)
    while not r.done:
        a = policy(r.observation.world, r.observation.actions)
        r = env.step(GIAction(action=a))
        learner.record(r.reward)     # dense checkpoint reward + terminal bonus
```

Notes on faithfulness to CONTRACTS: this **reuses our `run_episode` semantics exactly**
(act → K=6 steps → on_step, check failure then success, latch checkpoints in the runner).
Ideally the env should *call* `harness.gameverify.run_episode` internally so there is a
single runner implementation shared by G1/G3, the replay renderer, and this OpenEnv adapter
— avoiding a second, drifting copy of the tick loop.

### 3.2 What this buys us
- **The missing "agent training" rung of the pyramid.** Our loop today is
  env-creation → verification. OpenEnv gives the standard socket for the next rung:
  verified game → served env → **RL training** (TRL GRPO) → signal back into the next
  generation. Our checkpoints were *designed* as the dense reward — they drop straight into
  `StepResult.reward`.
- **A clean, framework-neutral eval harness.** Batched rollouts, a stable `reset/step/state`
  contract, per-episode reward/checkpoint logging — reusable across policies without us
  writing rollout plumbing.
- **HF ecosystem compatibility** (optional, later): publish certified games as HF Spaces
  envs, discoverable via `AutoEnv.from_env(...)`; instant compatibility with TRL and the
  other listed trainers.

### 3.3 Effort estimate
- **Core adapter** (`models.py`, `gi_environment.py`, `app.py`, `client.py`, `openenv.yaml`,
  per-env `pyproject`), single game served over uvicorn + local client round-trip, tested:
  **~2–3 days**.
- **Generalize to any generated game** (dynamic `game_path`, action-menu in observation,
  witness-replay parity with our renderer, reuse `run_episode`): **~1–2 days**.
- **Eval harness** (batched rollouts over N certified games, checkpoint/reward CSV, compare
  against the G3 witness as a baseline policy): **~1–2 days**.
- **Total to a usable OpenEnv-wrapped eval env: ~4–6 days.**
- **Full RL training integration** (LLM policy, observation tokenization/prompting, reward
  normalization, GRPO config): **separate, +1–2 weeks**, and **out of current scope** —
  not needed to capture most of the value.

### 3.4 Risks
- **Dependency weight.** OpenEnv *core* pulls FastAPI, uvicorn, Pydantic v2, requests/httpx,
  typer, rich, PyYAML, `huggingface_hub`, `openai`, `websockets`, **`fastmcp`**, **`gradio`**.
  `fastmcp`/`gradio`/`openai` are irrelevant to us but are core deps → larger install
  surface and more version-conflict risk against our lean harness (pymunk + PIL). Mitigate
  by installing OpenEnv in a *separate* venv/extra, not into the frozen base harness.
- **API churn.** `0.4.2.dev0`, pre-1.0, pushed daily, 89 open issues. Interfaces (esp.
  `create_app` kwargs, MCP surface, providers) can move. **Pin an exact version/commit.**
- **Windows compat.** FastAPI/uvicorn/pymunk all run natively on Windows, so **local dev
  needs no Docker** — run the server with `uvicorn` and connect via `base_url`. Docker is
  only required for `from_docker_image` / `from_env(use_docker=True)` and the container
  providers (isolated batched rollouts / HF Spaces, which are Linux). This keeps our
  day-to-day loop Windows-native; reserve Docker for scale-out/publishing.
- **pymunk concurrency.** The C-extension `Space` is not shareable across threads. The env
  factory gives each session its own `World`/`Space` (good), but set
  `REQUIRES_SINGLE_THREAD_EXECUTOR = True` and prefer process-level parallelism for batched
  rollouts.
- **Two-runner drift.** If the adapter re-implements the tick loop instead of calling
  `run_episode`, verifier and training env can silently diverge. Reuse the one runner.
- **License hygiene.** OpenEnv is BSD-3-Clause (safe). **AWM is unlicensed — do not copy any
  AWM code** into our repo, even the OpenEnv-merged `agent_world_model_env` if it carries
  AWM provenance; build our env fresh from the `grid_world`/`atari` templates.

### 3.5 What NOT to adopt (and why)
- **The entire AWM repo / pipeline** (`awm/*`): SQL/MCP tool-use synthesis + LLM judges;
  orthogonal to physics, weaker than our deterministic G0–G3 + checkpoints, and unlicensed.
- **The MCP env path** (`MCPEnvironment`, `MCPToolClient`, `/mcp`): our action space is a
  fixed 2–8 discrete strings, not a tool catalogue. Use the plain `Environment` + `Action`
  (as `grid_world` does), not MCP.
- **OpenEnv `Rubric` / LLM-judge reward machinery**: we already have a hack-resistant
  programmatic reward (checkpoints + binary `success`). Adding an LLM judge would reintroduce
  the exact fragility our verifier avoids (and drag in `openai`).
- **Gradio web UI / `web_interface`**: demo sugar; we already generate our own replay GIFs.
- **HF-Hub `AutoEnv`/`AutoAction` auto-discovery + Spaces deploy**: nice for public
  distribution later, but requires publishing to HF. Start with a local `base_url`; adopt
  only if/when we want the games public.

### 3.6 Bottom line
AWM is a dead end for us; OpenEnv is a genuinely good fit *on its own merits* — it already
proves physics/control games belong in its abstraction, its `reset/step/state` contract maps
1:1 onto our runner, and `StepResult.reward` is the natural home for our checkpoint signal.
Adopt a thin slice of it (plain `Environment` + types + `create_app` + `EnvClient`, pinned),
reuse our single `run_episode`, keep Docker optional, and we get the training/eval rung of
the pyramid for ~a week of work without compromising the frozen verification core.
