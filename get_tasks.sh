#!/usr/bin/env bash
# get_tasks.sh [TASK...]: download sample tasks from the repository's tasks-v1 release into tasks/ (all of tasks.tsv by default).
# Each archive holds one repository snapshot (app.tar, plus deps.tar for some post-cutoff tasks) and one Harbor task
# folder per arm and tier; the snapshot is linked into every folder whose Dockerfile needs it.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
base=${TASKS_URL:-https://github.com/ByteAsk/lsp-claude-code-cpp/releases/download/tasks-v1}
mkdir -p "$here/tasks" "$here/.cache"
[ -f "$here/.cache/SHA256SUMS" ] || curl -fsSL "$base/SHA256SUMS" -o "$here/.cache/SHA256SUMS"
tasks=("$@"); [ ${#tasks[@]} -gt 0 ] || mapfile -t tasks < <(awk 'NR > 1 {print $1}' "$here/tasks.tsv")
for t in "${tasks[@]}"; do
  f="$here/.cache/$t.tar.gz"
  [ -f "$f" ] || curl -fL --retry 3 "$base/$t.tar.gz" -o "$f"
  (cd "$here/.cache" && grep " $t.tar.gz\$" SHA256SUMS | sha256sum -c --quiet -) || { echo "checksum mismatch: $t" >&2; exit 1; }
  rm -rf "$here/tasks/$t" && tar -xzf "$f" -C "$here/tasks"
  for d in "$here/tasks/$t"/*/; do
    for sub in environment tests; do
      for a in app.tar deps.tar; do
        if [ -f "$d$sub/Dockerfile" ] && grep -q "source=$a" "$d$sub/Dockerfile"; then ln -f "$here/tasks/$t/$a" "$d$sub/$a"; fi
      done
    done
  done
  echo "$t: $(ls -d "$here/tasks/$t"/*/ | xargs -n1 basename | tr '\n' ' ')"
done
