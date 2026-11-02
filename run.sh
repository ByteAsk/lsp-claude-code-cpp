#!/usr/bin/env bash
# run.sh ARM TIER TASK [JOBS_DIR]: one trial, as in the paper.
#   ARM   plain | lsp | lsp-optional      TIER  nav | eo | e2e   (overload tasks and lsp-optional: nav only)
#   TASK  a task id from tasks.tsv, fetched with ./get_tasks.sh
# Needs docker, harbor 0.25.0 and ANTHROPIC_API_KEY. CPUSET picks the three cores of the trial (default 0-2).
set -euo pipefail
arm=$1 tier=$2 task=$3 jobs=${4:-jobs}
here=$(cd "$(dirname "$0")" && pwd)
dir="$here/tasks/$task/$arm-$tier"
[ -d "$dir" ] || { echo "no $dir: run ./get_tasks.sh $task first (or this arm/tier does not exist for the task)" >&2; exit 1; }
: "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY}"
model=${MODEL:-claude-opus-5-5}

# The real key goes only to the api-proxy container (compose interpolation); the agent gets the proxy URL and a dummy key.
export UPSTREAM_API_KEY="$ANTHROPIC_API_KEY" ANTHROPIC_API_KEY=dummy-agent-key ANTHROPIC_BASE_URL=http://api-proxy:8080
export CPUSET=${CPUSET:-0-2} MODEL=$model HARBOR_TELEMETRY=0 PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}"
unset ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN CLAUDE_CODE_EFFORT_LEVEL MAX_THINKING_TOKENS ENABLE_TOOL_SEARCH

# Claude Code 2.1.312, one model for every slot, automatic effort, no web search.
args=(-a agent:ClaudeCodeWithImageConfig -m "anthropic/$model" --ak version=2.1.312 --ak disable_web_search=true
      --ae ANTHROPIC_MODEL=$model --ae ANTHROPIC_SMALL_FAST_MODEL=$model --ae ANTHROPIC_DEFAULT_HAIKU_MODEL=$model
      --ae ANTHROPIC_DEFAULT_SONNET_MODEL=$model --ae ANTHROPIC_DEFAULT_OPUS_MODEL=$model --ae DISABLE_AUTOUPDATER=1)
# Plain keeps Claude Code's default tool search. The LSP arms load the LSP tool from the first turn and drop the ten
# other deferred tools, so the tool list grows only by the LSP tool.
if [ "$arm" != plain ]; then
  args+=(--ae ENABLE_TOOL_SEARCH=false --ae ENABLE_LSP_TOOL=1
         --ak disallowed_tools=DesignSync,CronCreate,CronDelete,CronList,EnterWorktree,ExitWorktree,Monitor,NotebookEdit,SendMessage,TaskStop)
fi
# Navigation and Multi-SWE-bench repairs are graded offline (score.py, grade_mswe.py); post-cutoff repairs by Harbor.
verifier=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["verifier"])' "$here/tasks/$task/meta.json")
nv=(); if [ "$tier" = nav ] || [ "$verifier" != harbor ]; then nv=(--disable-verification); fi

exec harbor run -q -y -p "$dir" "${args[@]}" "${nv[@]}" -n 1 -o "$jobs" --job-name "$arm-$tier-$task-$(date +%Y%m%d-%H%M%S)"
