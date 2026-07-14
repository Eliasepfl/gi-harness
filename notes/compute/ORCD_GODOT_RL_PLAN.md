# ORCD Godot+RL cluster plan — copy-paste executable

> ONE runbook for MIT ORCD "Engaging" (2026-07-14). Generation stays local/login-node (egress);
> G0–G4 certification, G3' RL-learnability, and curriculum rounds run on the cluster at scale.
> **The canonical certifier is a pinned Linux `.sif`** (`ORCD_DEPLOYMENT.md §4`): "cluster-certified"
> == "image-certified" by construction, so cross-OS float drift never has to be reconciled.
> Newly-resolved Linux-Godot facts footnoted §9.

---

## 0. Resolved open questions (the Linux-Godot unknowns)

1. **Linux headless Godot 4.7 artifact.** `Godot_v4.7-stable_linux.x86_64.zip` (~75.7 MB) → single
   binary `Godot_v4.7-stable_linux.x86_64`. **NO separate "console"/"server" download** — the Windows
   `*_console.exe` wrapper is Windows-only; the Linux binary streams stdout natively and runs headless
   with `--headless` (the 3.x "server" build is gone in 4.x). `GodotExecutor` points `HARNESS_GODOT_EXE`
   straight at it.
2. **Rapier `.so`.** The addon zip we already use — `godot-rapier-2d-single-simd-parallel.zip` **v0.8.39**
   — is **multi-platform**: it ships `bin/libgodot_rapier.linux.x86_64-unknown-linux-gnu.so`, auto-selected
   by `godot-rapier2d.gdextension` (`compatibility_minimum = 4.7`). **The Windows `tools/` doesn't transfer,
   but re-downloading the identical zip on Linux yields the `.so` — no separate build.** Stock Godot
   Physics 2D (float32) is the default, same-image byte-deterministic certifier; rapier
   `single-enhanced-determinism` is cross-checkout insurance only.
3. **runner.gd + `--import` on Linux.** Identical, with ONE quirk (#77508/#77466): headless `--import`
   with `--quit-after 1` can silently fail to import → `.godot/extension_list.cfg` (rapier) absent →
   silent stock fallback. **Fix: bare `--headless --import`, never `--quit-after 1`.** No X/Xvfb needed
   (`--headless` = dummy video+audio), but the ELF dlopen's libX11/libGL at load — those `.so`s **must
   be in the image** (§1 `%post`) or the binary won't start on a minimal container.

---

## 1. The canonical certifier — `gi-certifier.def` (verbatim)

Build on an **x86-64 login node** (`apptainer build --fakeroot`; login-node internet is granted —
`ORCD_DEPLOYMENT.md §2`). "Images built on one architecture cannot be used on a different
architecture" — build where you run.

```singularity
Bootstrap: docker
From: ubuntu:22.04

%labels
    org.gi.role   canonical-certifier
    org.gi.godot  4.7-stable
    org.gi.rapier v0.8.39
    org.gi.pymunk 7.3.0

%environment
    export PATH=/opt/conda/envs/gi/bin:/opt/conda/bin:/usr/local/bin:$PATH
    export HARNESS_GODOT_EXE=/opt/godot/godot
    export HARNESS_NODE=/opt/conda/envs/gi/bin/node
    export NODE_PATH=/opt/nodeworld/node_modules
    export PYTHONHASHSEED=0
    export PYTHONNOUSERSITE=1          # never leak ~/.local into the env (ORCD §2 rule)

%post
    set -eux
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    # Godot headless still dlopen's these even with --headless; dos2unix for CRLF hygiene.
    apt-get install -y --no-install-recommends \
        curl ca-certificates git unzip xz-utils dos2unix \
        libx11-6 libxcursor1 libxinerama1 libxrandr2 libxi6 libxext6 \
        libgl1 libglu1-mesa libglx-mesa0 libasound2 libpulse0 \
        fontconfig libfontconfig1 libfreetype6
    rm -rf /var/lib/apt/lists/*

    # --- miniforge + the gi env (Python 3.12; pin pymunk to the certifying engine) ---
    curl -fsSL -o /tmp/mf.sh https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
    bash /tmp/mf.sh -b -p /opt/conda && rm /tmp/mf.sh
    /opt/conda/bin/mamba create -y -n gi -c conda-forge \
        python=3.12 pillow pytest numpy nodejs=22
    /opt/conda/envs/gi/bin/pip install --no-cache-dir pymunk==7.3.0
    /opt/conda/envs/gi/bin/pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

    # --- nodeworld runtime dep, baked at a repo-independent path (runner resolves via NODE_PATH) ---
    mkdir -p /opt/nodeworld && cd /opt/nodeworld
    /opt/conda/envs/gi/bin/npm install --no-audit --no-fund planck@1.5.0
    # If nodeworld/package.json grows past planck, run `npm ci` in the home checkout on the login node.

    # --- Godot 4.7 headless + rapier addon (multi-platform zip → Linux .so is inside) ---
    mkdir -p /opt/godot && cd /opt/godot
    curl -fsSL -o g.zip https://github.com/godotengine/godot/releases/download/4.7-stable/Godot_v4.7-stable_linux.x86_64.zip
    unzip -q g.zip && rm g.zip && mv Godot_v4.7-stable_linux.x86_64 godot && chmod +x godot
    ln -sf /opt/godot/godot /usr/local/bin/godot
    mkdir -p /opt/rapier && cd /opt/rapier
    curl -fsSL -o r.zip https://github.com/appsinacup/godot-rapier-physics/releases/download/v0.8.39/godot-rapier-2d-single-simd-parallel.zip
    unzip -q r.zip && rm r.zip   # → /opt/rapier/addons/godot-rapier2d/{bin,godot-rapier2d.gdextension}

    /opt/godot/godot --headless --version    # boot/lib-deps smoke (fails the build if libs missing)

%runscript
    exec "$@"
```

Notes: **`--import` cannot be baked** — it writes `res://.godot/` into the Godot project, which lives
in the home checkout (`godotworld/`), not the image; it is a one-time per-checkout login-node step
(§2 step 6) that `GodotExecutor` also runs automatically on first use. The rapier addon is baked at
`/opt/rapier/addons/`; stage it into the project only for the enhanced-determinism cross-machine build.

---

## 2. Day-1 login-node checklist (`ssh orcd-login.mit.edu`)

```bash
# 1. Modules (put loads in job scripts, NOT ~/.bashrc — ORCD §2)
module load apptainer/1.4.2

# 2. Clone with CRLF hygiene BEFORE checkout touches node/bash files (ORCD §2 Windows gotcha)
git config --global core.autocrlf input
git clone <deploy-url> ~/gi && cd ~/gi
printf '* text=lf\n' > .gitattributes && git add --renormalize . >/dev/null 2>&1 || true
find . -name '*.sh' -print0 | xargs -0 -r dos2unix -q

# 3. Build the canonical certifier (login node has internet)
apptainer build --fakeroot ~/gi/gi-certifier.sif ~/gi/notes/compute/gi-certifier.def

# 4. Egress probe — record the verdict (decides whether generation can ever sbatch; ORCD §3c)
srun -p mit_quicktest -t 5 curl -sS -m 10 https://openrouter.ai/api/v1/models >/dev/null \
     && echo "EGRESS: compute-node internet OK" || echo "EGRESS: none — generate on login/local"

# 5. Smoke the stack in-image (pure local code, no egress, no GPU)
apptainer exec ~/gi/gi-certifier.sif bash -lc 'cd ~/gi && python -m pytest tests/ -q'          # expect ~394 passed
apptainer exec ~/gi/gi-certifier.sif bash -lc 'cd ~/gi && python -m pytest tests/test_godot_exec.py tests/test_rl_env.py -q'

# 6. Godot Linux GATE: one-time --import (bare, no --quit-after 1 — quirk #77508) then one verify
apptainer exec ~/gi/gi-certifier.sif bash -lc \
  'cd ~/gi && $HARNESS_GODOT_EXE --headless --path godotworld --import'
apptainer exec ~/gi/gi-certifier.sif bash -lc \
  'cd ~/gi && python -m harness game verify godotworld/examples/traverse.spec.json --json'      # expect passed=true, ~5-7s

# 7. JS + Py lane gate (auto-detected engine) + a G3' micro-probe (reduced budget)
apptainer exec ~/gi/gi-certifier.sif bash -lc \
  'cd ~/gi && python -m harness game verify scenes/games/v23_showcase/gem_cavern/game.js --json'
apptainer exec ~/gi/gi-certifier.sif bash -lc 'cd ~/gi && python - <<PY
from harness.rl.certify import g3_prime
r=g3_prime("scenes/games/v23_showcase/gem_cavern/game.js", budget_steps=200_000)
print("learnable",r["learnable"],"stoch",r["stochastic_success_rate"],"bridge",r["bridge_ok"])
PY'

# 8. Determinism GATE: replay a KNOWN Windows-emitted witness in-image; it must reach success.
#    (Confirms the pinned image is a valid canonical certifier for existing witnesses — ORCD §4.)
```

Every step is a hard gate. Step 6 (Godot `--import` + verify) and step 8 (cross-OS witness replay)
are the **Linux-Godot day-1 gates** flagged in §0/§8.

---

## 3. Slurm array templates (verbatim)

Shared discipline (`ORCD_DEPLOYMENT.md §3/§5`): `-c 1` (pymunk `Space` + Godot main-loop are
single-thread → one core/task); each task loops a **disjoint slice** `i ≡ TASK_ID (mod TASK_COUNT)`
and writes its **own ledger shard** (NFS append isn't atomic cross-node); seeds derive from `TASK_ID`.

### (a) Certify farm — G0–G3 verify + G4 attack (all engines, one command routes py/js/godot)

```bash
#!/bin/bash
#SBATCH -p mit_normal                 # ≤96c/12h; swap mit_preemptable for 512c/48h fan-out
#SBATCH -a 0-95%96
#SBATCH -c 1
#SBATCH --mem-per-cpu=4G
#SBATCH -t 04:00:00
#SBATCH -o /home/$USER/orcd/scratch/gi/logs/cert-%A-%a.log
module load apptainer/1.4.2
SIF=~/gi/gi-certifier.sif
OUT=~/orcd/scratch/gi/runs/$SLURM_ARRAY_JOB_ID ; mkdir -p "$OUT/logs"
# Materialize the game manifest ONCE (host side) so every in-container task reads the same list.
MANIFEST="$OUT/games.txt"
[ -f "$MANIFEST" ] || ( cd ~/gi && git ls-files 'scenes/games/**/game.js' 'godotworld/examples/*.spec.json' > "$MANIFEST" )
apptainer exec "$SIF" bash -lc '
  cd ~/gi
  mapfile -t G < "'"$MANIFEST"'"; N=${#G[@]}
  LEDGER="'"$OUT"'/ledger.'"$SLURM_ARRAY_JOB_ID"'.'"$SLURM_ARRAY_TASK_ID"'.jsonl"
  for ((i='"$SLURM_ARRAY_TASK_ID"'; i<N; i+='"$SLURM_ARRAY_TASK_COUNT"')); do
    g="${G[$i]}"; d="'"$OUT"'/$(echo "$g" | tr "/." "__")"; mkdir -p "$d"
    python -m harness game verify "$g" --json > "$d/report.json" || true
    python -m harness game attack "$g" --tier 1 --json > "$d/g4.json" || true
    python -m harness game replay "$g" --frames "$d/frames.json" >/dev/null 2>&1 || true
    python -c "import json,sys;print(json.dumps({\"game\":\"$g\",\"report\":json.load(open(\"$d/report.json\"))},separators=(\",\",\":\")))" >> "$LEDGER" || true
  done'
```

### (b) G3' probe farm — RL-learnability (screen at 200k, or full 2M)

`g3_prime` has no CLI verb; a 6-line driver invokes it. Stage it once to scratch:

```bash
cat > ~/orcd/scratch/gi/rl_probe.py <<'PY'
import json, os, sys
from harness.rl.certify import g3_prime
game, out = sys.argv[1], sys.argv[2]
r = g3_prime(game, budget_steps=int(os.environ.get("GIP_BUDGET", "200000")))
open(out, "w").write(json.dumps(r))                     # g3_prime returns a plain JSON-able dict
print(game, r["learnable"], r.get("stochastic_success_rate"), r["bridge_ok"])
PY
```

```bash
#!/bin/bash
#SBATCH -p mit_preemptable            # short, seeded, idempotent → preemption = re-run one game
#SBATCH -a 0-511%512
#SBATCH -c 1
#SBATCH --mem-per-cpu=4G
#SBATCH -t 08:00:00
#SBATCH -o /home/$USER/orcd/scratch/gi/logs/g3p-%A-%a.log
module load apptainer/1.4.2
SIF=~/gi/gi-certifier.sif
OUT=~/orcd/scratch/gi/runs/$SLURM_ARRAY_JOB_ID ; mkdir -p "$OUT"
export GIP_BUDGET=${GIP_BUDGET:-200000}               # 200k screen; export 2000000 for the full rung
apptainer exec "$SIF" bash -lc "cd ~/gi
mapfile -t G < <(git ls-files 'scenes/games/**/game.js')
N=\${#G[@]}
for ((i=$SLURM_ARRAY_TASK_ID; i<N; i+=$SLURM_ARRAY_TASK_COUNT)); do
  d=\"$OUT/\$(echo \${G[\$i]} | tr '/.' '__')\"; mkdir -p \"\$d\"
  python ~/orcd/scratch/gi/rl_probe.py \"\${G[\$i]}\" \"\$d/g3p.json\" \
    >> \"$OUT/g3p.$SLURM_ARRAY_JOB_ID.$SLURM_ARRAY_TASK_ID.jsonl\" || true
done"
```

### (c) Curriculum batch round — one round of N games in parallel, then STOP for the local LLM

```bash
#!/bin/bash
#SBATCH -p mit_normal
#SBATCH -a 0-31%32                     # 32 curricula advanced in parallel this round
#SBATCH -c 1
#SBATCH -t 02:00:00
#SBATCH -o /home/$USER/orcd/scratch/gi/logs/curr-%A-%a.log
# ROUND and BATCH passed via --export=ROUND=k,BATCH=name; games for round k live under
# ~/orcd/scratch/gi/curr/$BATCH/round$ROUND/games/ (pushed by the LOCAL LLM step, see §4/§6).
module load apptainer/1.4.2
SIF=~/gi/gi-certifier.sif
IN=~/orcd/scratch/gi/curr/$BATCH/round$ROUND
apptainer exec "$SIF" bash -lc "cd ~/gi
mapfile -t G < <(ls $IN/games/*/game.js 2>/dev/null)
N=\${#G[@]}
for ((i=$SLURM_ARRAY_TASK_ID; i<N; i+=$SLURM_ARRAY_TASK_COUNT)); do
  d=\"$IN/out/\$(basename \$(dirname \${G[\$i]}))\"; mkdir -p \"\$d\"
  python -m harness game verify \"\${G[\$i]}\" --json > \"\$d/report.json\" || true
  GIP_BUDGET=2000000 python ~/orcd/scratch/gi/rl_probe.py \"\${G[\$i]}\" \"\$d/g3p.json\" || true
done"
# Round metrics (learnable? difficulty AUC, steps-to-solve, plateau) now sit in $IN/out/*/g3p.json
# → rsync to local → LOCAL LLM emits round k+1 directive/edits → push round$((ROUND+1))/games → resubmit.
```

---

## 4. Throughput plan

Measured anchors: Godot verify 5–7 s/game; JS verify 2–6 s; G4 attack batches seeds per task; G3'
200k probe ≈ 2–3 min; full 2M ≈ 13–20 min at ~2 500 env-steps/s (IPC-bound, single core).

| Stage | per-game | per-core/hr | **mit_normal (96c)/hr** | **mit_preempt (512c)/hr** |
|---|---|---|---|---|
| Godot G0–G3 verify | 5–7 s | ~550 | ~53 000 | ~280 000 |
| JS G0–G3 verify | 2–6 s | ~900 | ~86 000 | ~460 000 |
| G4 attack (batched fuzz) | ~30–90 s | ~50 | ~4 800 | ~26 000 |
| **G3' probe (200k screen)** | 2–3 min | ~24 | ~2 300 | ~12 000 |
| **G3' full (2M)** | 13–20 min | 3–4.6 | ~290–440 | ~1 500–2 400 |

**Reading it:** verify/attack are **bank-size-bound, not core-bound** — the whole current bank clears
in minutes on 96 cores. The one binding constraint is **G3' full-budget RL**, which is why the
scale-out targets `mit_preemptable` (512+ cores, checkpoint/idempotent discipline → preemption just
re-runs one game). Screen-then-deepen: run the **200k probe over everything** (cheap, ~12k/hr on 512c),
then spend the 2M budget only on games that are learnable-but-not-sharp (the `two_switch_vault` band,
G3_PRIME_SPIKE §3).

**Curriculum serial dependency.** Round k+1 needs round k's metrics **plus a LOCAL LLM call** (the
designer directive/edits) — and the compute node has no assumed egress, so that call lives on the
login node / your box. This forces a **local↔cluster ping-pong per round**. Amortize two ways:
(1) **batch N games' curricula per round** (template c) so the one LLM directive call is spread over
N games; (2) **pipeline across batches** — while the local LLM reasons about batch A's round k+1,
the cluster runs batch B's round k. Because designer calls are rare (EnvGen: ~4 total per curriculum),
the loop stays cluster-dominated; the LLM latency is hidden behind the next batch's compute.

---

## 5. Data flow

```
 LOCAL / login node                         CLUSTER (egress-free)                      BACK
 ─────────────────                          ─────────────────────                      ────
 harness game new  ──git push──►  ~/gi (home, 200GB, backed up: code + certified games)
   (OpenRouter)                          │ git pull
                                         ▼
                          sbatch arrays (a)/(b)/(c)  ──►  ~/orcd/scratch/gi/runs/$JOBID/
                                                             <game_id>/report.json     (G0–G3)
                                                             <game_id>/g4.json          (G4)
                                                             <game_id>/g3p.json         (G3' cert)
                                                             <game_id>/frames.json       (replayer)
                                                             ledger.$JOBID.$TASKID.jsonl (shard)
                                         │
 rsync -avz --partial orcd-login.mit.edu:~/orcd/scratch/gi/runs/ ./runs/  ◄────────────┘
                                         │
 harness ledger merge  (dedupe on (game_id, seed, verdict_hash)) ─► runs/ledger.jsonl
 git add certified games + merged ledger ; frames.json ─► gi-site/_src/dayN/ replayer blocks
 curriculum: LOCAL LLM reads round-k g3p.json metrics ─► round k+1 games ─git push─► resubmit (c)
```

Per-task artifact layout is fixed: one dir per game (`<game_id>/` = the path with `/`,`.`→`_`),
holding `report.json` (G0–G3 funnel), `g4.json` (adversarial), `g3p.json` (learnability + RL witness),
`frames.json` (the replayer-ready `{meta, frames}` substrate — results come back as JSON, **never
GIFs**, per WEB_REPLAYER.md), and a per-task ledger shard. What returns to the site pipeline is
`frames.json`, inlined into a `<script type="application/json">` block in a `gi-site/_src/dayN/`
page (no `_build.py` change; still AES-encryptable).

---

## 6. Risk table (top 5)

| # | Risk | Impact | Mitigation | Gate |
|---|------|--------|------------|------|
| 1 | **Godot Linux headless lib deps** — the ELF dlopen's libX11/libGL/libasound even with `--headless`; on a minimal container the binary won't start | whole Godot lane dead on cluster | bake the runtime `.so`s in the `.sif` `%post` (§1); `godot --headless --version` smoke fails the build early | **DAY-1**: §2 step 6 first line |
| 2 | **`--import` quirk #77508** — `--headless --import` with `--quit-after 1` silently fails to import → `.godot/extension_list.cfg` absent → rapier silent stock fallback / spec-load errors | non-deterministic or wrong Godot verifies, no error printed | run **bare** `--headless --import` (never `--quit-after 1`); assert `godotworld/.godot/` exists post-import | **DAY-1**: §2 step 6 |
| 3 | **Cross-OS float determinism** — Windows-emitted witnesses may not replay bit-exact on Linux (double-precision Chipmunk/pymunk; Godot float32 quantizes but is per-build) | "locally-certified" ≠ "cluster-certified" | **canonical certifier = the pinned `.sif`**; witnesses are candidates until they replay under the image; re-certify the bank once in-image; keep `policy_weights_hash+seed` in each witness | **DAY-1**: §2 step 8 replays a known Windows witness in-image |
| 4 | **Compute-node egress not guaranteed** — `sbatch` tasks may have no internet → no OpenRouter/curriculum LLM in-job | curriculum + generation can't run in arrays | generate + designer LLM calls on login/local; egress probe records the truth; batch+pipeline the ping-pong (§4) | **DAY-1**: §2 step 4 |
| 5 | **Ledger race + seed collision** — cross-node NFS append is not atomic; wall-clock seeds repeat work | corrupt ledger lines, duplicated/missing games | per-task shard `ledger.$JOBID.$TASKID.jsonl`; disjoint seed ranges from `TASK_ID`; `harness ledger merge` dedupes on `(game_id, seed, verdict_hash)` | continuous (built into templates §3) |

(CRLF is handled in the clone recipe, §2 step 2 — a `\r` on a node/bash shebang breaks on Linux;
Python tolerates it. Not a top-5 risk once `core.autocrlf input` + `.gitattributes * text=lf` are set.)

---

## 7. GO checklist (tick in a terminal)

```
[ ] ssh orcd-login.mit.edu ; module load apptainer/1.4.2
[ ] git clone with core.autocrlf=input + .gitattributes * text=lf ; dos2unix *.sh
[ ] apptainer build --fakeroot gi-certifier.sif gi-certifier.def   (build succeeds → §1 %post gate passed)
[ ] EGRESS probe recorded (OK / none)                              [DAY-1 GATE, risk 4]
[ ] pytest tests/ green in-image (~394 passed)
[ ] Godot: bare --headless --import succeeds ; godotworld/.godot/ present   [DAY-1 GATE, risk 2]
[ ] Godot: game verify traverse.spec.json → passed=true, 5-7s              [DAY-1 GATE, risk 1]
[ ] JS: game verify gem_cavern/game.js → passed=true
[ ] G3' micro-probe (200k) → learnable + bridge_ok=true
[ ] Determinism: a known Windows witness replays to success in-image       [DAY-1 GATE, risk 3]
[ ] sbatch certify farm (template a, -a 0-15 on mit_normal) over the bank → shards written
[ ] harness ledger merge → runs/ledger.jsonl ; rsync runs/ back ; git add certified games
[ ] scale: G3' probe farm (template b) on mit_preemptable ; then full-2M on the not-sharp band
```

---

## 8. Phasing (composes ORCD_DEPLOYMENT §6 + GODOT_RL_MERGE §3)

**Day 1** §2 checklist + one certify array over the bank → **Next** G3' 200k screen on
`mit_preemptable`, deepen the not-sharp band to 2M, make the `.sif` canonical, add `harness ledger
merge` → **Then** curriculum rounds (template c) + local↔cluster ping-pong, `frames.json` → site →
**Later (outer rung)** `godot_rl_agents` Sync/TCP on the SAME frozen `runner.gd` to serve *certified*
games to external trainers/ONNX (pin the git commit into a variant image); GPU only for a
batched-Planck+torch or generalist-policy experiment.

## 9. Sources (Linux-Godot facts newly verified 2026-07-14)

- Godot 4.7 Linux asset `Godot_v4.7-stable_linux.x86_64.zip` — GitHub API `releases/tags/4.7-stable`;
  https://godotengine.org/download/archive/4.7-stable/
- Rapier addon multi-platform (`bin/libgodot_rapier.linux.x86_64-unknown-linux-gnu.so`,
  `compatibility_minimum=4.7`) — `godot-rapier2d.gdextension` on `appsinacup/godot-rapier-physics@main`;
  release asset `godot-rapier-2d-single-simd-parallel.zip` v0.8.39 (GitHub API `releases/tags/v0.8.39`)
- Headless `--import` / `--quit-after 1` quirk — godotengine/godot issues #77508, #77466
- All ORCD partition/quota/egress facts inherited from `notes/compute/ORCD_DEPLOYMENT.md` (source-cited there)
- Local synthesis: `GODOT_LANE.md`, `godotworld/SPEC.md`+`SPIKE_REPORT.md`, `G3_PRIME_SPIKE.md`,
  `GODOT_RL_MERGE.md`, `WATCHABLE_DEMOS.md`, `WEB_REPLAYER.md`, `harness/rl/{env,certify}.py`, `harness/cli.py`
