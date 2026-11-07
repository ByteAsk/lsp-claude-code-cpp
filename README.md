# lsp-claude-code-cpp

Reproduce the experiments of **Why LSP, Why the LSP Plugin, and Why Claude Code Doesn't Save Tokens**
(Pratyush Saini, CTO, ByteAsk Research, pratyush@byteask.ai; Anirudh Saini, CEO, ByteAsk Research, anirudha@byteask.ai;
November 2026): Claude Code with and without Anthropic's clangd LSP plugin on C/C++ issues.

Paper: [byteask.ai/research/lsp-claude-code-cpp](https://byteask.ai/research/lsp-claude-code-cpp)

This repository runs both arms on 20 sample tasks in the [Harbor](https://github.com/laude-institute/harbor) task
format, with your own Anthropic API key, and scores the runs the way the paper does. The full task set (255 issues
plus the constructed overload requests) is available on request: contact@byteask.ai.

## What you need

- Linux with Docker (compose v2 and buildx), up to about 70 GB of free disk for images, 3 CPU cores per trial
- Python 3.11 or newer and [Harbor](https://github.com/laude-institute/harbor) 0.25.0: `uv tool install harbor==0.25.0`
- An Anthropic API key with access to `claude-opus-5-5`
- To grade the Multi-SWE-bench repairs: the official harness at commit `24f493f` and its C++ dataset files

```bash
python3 -m venv .venv && . .venv/bin/activate
git clone https://github.com/multi-swe-bench/multi-swe-bench && git -C multi-swe-bench checkout 24f493f
pip install -e multi-swe-bench huggingface_hub
hf download ByteDance-Seed/Multi-SWE-bench --repo-type dataset --include "cpp/*" --local-dir mswe-data
```

## Run one task

```bash
./get_tasks.sh fmtlib__fmt-4057          # downloads and checks one task (about 17 MB)
export ANTHROPIC_API_KEY=...
./run.sh plain nav fmtlib__fmt-4057      # Claude Code as shipped
./run.sh lsp   nav fmtlib__fmt-4057      # with the configured clangd workflow
python3 score.py                         # recall, tokens, cost, LSP and grep use, LSP/Plain ratios
```

The first run of a task builds its images (a few minutes; the LSP image also builds the clangd index). Every trial
runs on its own three cores (`CPUSET=0-2` by default); give parallel trials different `CPUSET` values.

Every task, arm and tier, one after another:

```bash
./get_tasks.sh                           # all 20 tasks, 1.3 GB
for t in $(awk 'NR > 1 {print $1}' tasks.tsv); do
  for v in tasks/$t/*/; do v=$(basename "$v"); ./run.sh "${v%-*}" "${v##*-}" "$t"; done
done
```

## Arms and tiers

| Arm | What it is |
|---|---|
| `plain` | Claude Code 2.1.312 as shipped, Claude Opus 5.5, default tool search |
| `lsp` | the same agent with clangd 21.1.8, a pre-built index, Anthropic's `clangd-lsp` plugin loaded from the first turn, and the instruction in `environment/ask-CLAUDE.md` to find code symbols with the server |
| `lsp-optional` | the `lsp` image with a neutral note instead of the instruction: the server is there, use it or grep as you see fit |

| Tier | What the agent may do | Graded by |
|---|---|---|
| `nav` | read only; list at most ten `path:function` sites to change | `score.py`, against `tasks/TASK/gold.json` |
| `eo` | edit, but no build, compile or test (a hook refuses them) | hidden tests |
| `e2e` | fix the issue normally | hidden tests |

The agent's container has no network except an API proxy that holds your key, forwards only Messages API calls for
the chosen model and logs their token usage (`environment/api-proxy/proxy.py`). Post-cutoff repairs are graded by
Harbor's verifier. Multi-SWE-bench repairs are graded with the official harness in the official images, pinned by
digest in `images.tsv`:

```bash
. .venv/bin/activate                     # the venv with the harness, from "What you need"
python3 grade_mswe.py jobs --harness multi-swe-bench --dataset mswe-data/cpp
python3 score.py
```

## The sample

`tasks.tsv` lists the 20 tasks: 16 issues in all three tiers, from nlohmann/json, fmt, Catch2, simdjson, QuantLib,
libuv and yaml-cpp, and 4 overload change requests (navigation only) whose answer is the callers of one overload
among functions with the same name.

The issues were chosen by a fixed rule from the paper's evaluation tasks (not the tuning or held-out sets): a
well-known public repository; all three tiers graded; the same repair outcome in every run of both arms, the same
navigation recall in both arms, and an LSP call in every LSP run; no fix code in the issue text; end-to-end runs
under 15 minutes; 12 that both arms always solved and 4 that both always failed, about the paper's pass rate;
repositories taken in turn, larger checkouts first. Cost was not a criterion. The sample is chosen for stable
outcomes, so it shows the paper's behaviour with little run-to-run noise; it is not a random draw.

One run of everything is 124 trials: 16 issues × 3 tiers × 2 arms, the 4 overload requests in both arms, and the 20
navigation tasks in `lsp-optional`. Running all 124 trials once cost us $27.64 in API usage. With the images built,
20 trials in parallel on 60 cores took 25 minutes; grading the 52 Multi-SWE-bench repairs with the default four
workers took 52 minutes more.

## What to expect

The paper's results on all 255 issues, and one run of this repository on the sample (November 2026, all 124 trials,
graded as above). Ratios pair each task and tier across arms; the paper's have 95% intervals.

| | Paper | This sample, one run |
|---|---|---|
| Repairs resolved, `lsp` against `plain` | 270 against 275 of 359 attempts (p = 0.41) | 23 against 25 of 32 |
| Tokens, `lsp` / `plain` | ×1.19 (1.11 to 1.27) | ×1.24 |
| API cost, `lsp` / `plain` | ×1.11 (1.08 to 1.15) | ×1.13 |
| Navigation recall, `lsp` against `plain` | no gain in any repository group | 71.9% in all three arms |
| `lsp` runs that called the server | 96% | 100% |
| Server calls that looked a name up | 89% | 85% |
| `lsp-optional` runs that called the server | 5% of 60 | 0 of 20 |
| API cost, `lsp-optional` / `plain` | ×1.05 (1.01 to 1.08) | ×1.13 |

## Layout

```
run.sh          one trial: ./run.sh ARM TIER TASK [JOBS_DIR]
get_tasks.sh    download tasks from the tasks-v1 release into tasks/ and check them
agent.py        Harbor's Claude Code agent plus one step: copy the image's ~/.claude (the plugin) into the session
score.py        score every trial in jobs/ and print the per-arm summary and the LSP/Plain ratios
grade_mswe.py   grade Multi-SWE-bench repairs with the official harness
tasks.tsv       the sample tasks
images.tsv      official Multi-SWE-bench images, by digest
```

Each downloaded task holds one repository snapshot (`app.tar`) and a Harbor task folder per arm and tier:
`instruction.md`, `task.toml`, `environment/` (Dockerfile, compose file, API proxy, the LSP files), `tests/` (hidden
tests and grader) and `solution/` (the reference fix).

## Citation

```bibtex
@misc{saini2026lsp,
  title        = {Why LSP, Why the LSP Plugin, and Why Claude Code Doesn't Save Tokens: Language Servers, Grep, and the Cost of Code Navigation for C/C++ Coding Agents},
  author       = {Saini, Pratyush and Saini, Anirudh},
  year         = {2026},
  month        = nov,
  institution  = {ByteAsk Research},
  howpublished = {\url{https://byteask.ai/research/lsp-claude-code-cpp}}
}
```

## License

The scripts are MIT licensed (`LICENSE`). Each repository snapshot keeps its project's own license (in the snapshot).
The Multi-SWE-bench dataset files and images are not redistributed here; get them from their official sources.
