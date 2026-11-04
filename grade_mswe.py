#!/usr/bin/env python3
"""grade_mswe.py JOBS_DIR --harness DIR --dataset DIR: grade the Multi-SWE-bench edit-only and end-to-end trials in
JOBS_DIR with the official harness, as the paper did, and write JOBS_DIR/mswe_grades.json.

  --harness  a checkout of github.com/multi-swe-bench/multi-swe-bench at commit 24f493f, installed in this Python
  --dataset  the folder with the official cpp/*_dataset.jsonl files (Hugging Face: ByteDance-Seed/Multi-SWE-bench)

Rules (the paper's section 8): grading runs only in the official instance images, pulled by the digests in
images.tsv, never in images the harness would build itself; files of the instance's test patch are removed from the
agent's patch (the harness applies the test patch on top); a fresh work folder per call; an instance appears once per
call. None of the sample instances tests a generated single header, so no regeneration step is needed here.
resolved: true / false, with the reason; null means the harness gave no usable answer (re-run the script)."""
import argparse, glob, json, os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DIGEST = dict(l.rstrip("\n").split("\t")[:2] for l in open(f"{HERE}/images.tsv") if l.strip() and not l.startswith("#"))
JOB = re.compile(r"^(plain|lsp)-(eo|e2e)-(.+)-\d{8}-\d{6}$")


def parts(task):
    org, rest = task.split("__", 1)
    repo, num = rest.rsplit("-", 1)
    return org, repo, int(num)


def image(org, repo, num):
    return f"mswebench/{org.lower()}_m_{repo.lower()}:pr-{num}"


def chunks(patch):
    out = []
    for c in re.split(r"(?m)^(?=diff --git )", patch):
        m = re.match(r"diff --git a/(\S+) b/(\S+)", c)
        if m:
            out.append((m.group(1), m.group(2), c))
    return out


def files(patch):
    return {p for a, b, _ in chunks(patch) for p in (a, b)}


def pull(tag):
    """the official image, by digest, tagged as the harness expects; False if it cannot be had"""
    ref = DIGEST.get(tag)
    if not ref:
        return False
    have = subprocess.run(["docker", "image", "inspect", "-f", "{{json .RepoDigests}}", tag], capture_output=True, text=True).stdout
    if ref not in have:
        if subprocess.run(["docker", "pull", "-q", ref], capture_output=True).returncode:
            return False
        subprocess.run(["docker", "tag", ref, tag], check=True)
    return ref in subprocess.run(["docker", "image", "inspect", "-f", "{{json .RepoDigests}}", tag], capture_output=True, text=True).stdout


def classify(rep, log):
    em = (rep or {}).get("error_msg") or ""
    if "no test results were captured" in em:
        if re.search(r"^error: (patch failed|corrupt patch|.*patch does not apply|[^:\s]+: No such file)", log, re.M):
            return False, "apply_failed"
        if re.search(r": error: |make(\[\d+\])?: \*\*\* |Error \d+$|undefined reference", log, re.M):
            return False, "compile_failed"
        # the parsers of fmt, json and simdjson record no failed test, so a run where every test fails reads as "no results"
        if re.search(r"\d+% tests passed, \d+ tests failed out of \d+|Errors while running CTest|FAILED TESTS|\*\*\*(Failed|Timeout|Exception|Not Run)", log):
            return False, "all_tests_failed"
        return None, "no_results_unexplained"
    if "test failed" in em and "Before applying the fix patch, the test passed" in em:
        return False, "regression"
    if "no test cases transitioned from failed to passed" in em:
        return False, "no_failing_test_fixed"
    if "anomalous" in em:
        return False, "anomalous"
    if not em:
        return False, "required_test_not_passing"
    return False, "unresolved_other"


def harness_call(args, org, repo, items):
    """items: [(key, num, patch)] -> {key: {resolved, cat}}"""
    gd = tempfile.mkdtemp(prefix=f"mswe-{repo}-", dir=args.workdir)
    pf = f"{gd}/preds.jsonl"
    with open(pf, "w") as f:
        for _, num, patch in items:
            f.write(json.dumps({"org": org, "repo": repo, "number": num, "fix_patch": patch}) + "\n")
    cfg = {"mode": "evaluation", "workdir": f"{gd}/wd", "patch_files": [pf], "dataset_files": [f"{args.dataset}/{org}__{repo}_dataset.jsonl"],
           "force_build": False, "output_dir": f"{gd}/out", "specifics": [n for _, n, _ in items], "skips": [],
           "repo_dir": f"{args.workdir}/repos", "need_clone": True, "global_env": ["MAKEFLAGS=-j4"], "clear_env": True, "stop_on_error": False,
           "max_workers": args.workers, "max_workers_build_image": 1, "max_workers_run_instance": args.workers,
           "log_dir": f"{gd}/logs", "log_level": "INFO"}
    for k in ("workdir", "output_dir", "log_dir", "repo_dir"):  # the harness clones each repository once into repo_dir
        os.makedirs(cfg[k], exist_ok=True)
    json.dump(cfg, open(f"{gd}/config.json", "w"), indent=1)
    r = subprocess.run([sys.executable, "-m", "multi_swe_bench.harness.run_evaluation", "--config", f"{gd}/config.json"],
                       cwd=args.harness, capture_output=True, text=True, timeout=10 * 3600)
    open(f"{gd}/harness.log", "w").write(r.stdout[-30000:] + r.stderr[-30000:])
    fr = f"{gd}/out/final_report.json"
    rep = json.load(open(fr)) if os.path.exists(fr) else {}
    res, unres = set(rep.get("resolved_ids", [])), set(rep.get("unresolved_ids", []))
    out = {}
    for k, num, _ in items:
        iid = f"{org}/{repo}:pr-{num}"
        idir = f"{gd}/wd/{org}/{repo}/evals/pr-{num}"
        ir = json.load(open(f"{idir}/report.json")) if os.path.exists(f"{idir}/report.json") else None
        log = open(f"{idir}/fix-patch-run.log", errors="replace").read() if os.path.exists(f"{idir}/fix-patch-run.log") else ""
        if iid in res:
            out[k] = {"resolved": True, "cat": "resolved"}
        elif iid in unres:
            v, c = classify(ir, log)
            out[k] = {"resolved": v, "cat": c}
        else:
            out[k] = {"resolved": None, "cat": "harness_error"}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs")
    ap.add_argument("--harness", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--workdir", default=None)
    args = ap.parse_args()
    # the harness runs from its own folder, so every path it is given must be absolute
    args.jobs, args.harness, args.dataset = (os.path.abspath(x) for x in (args.jobs, args.harness, args.dataset))
    args.workdir = os.path.abspath(args.workdir or os.path.join(args.jobs, ".mswe-work"))
    os.makedirs(args.workdir, exist_ok=True)
    gf = os.path.join(args.jobs, "mswe_grades.json")
    grades = json.load(open(gf)) if os.path.exists(gf) else {}
    meta = {os.path.basename(os.path.dirname(p)): json.load(open(p)) for p in glob.glob(f"{HERE}/tasks/*/meta.json")}
    todo = {}
    for res in sorted(glob.glob(os.path.join(args.jobs, "*", "*", "result.json"))):
        trial = os.path.dirname(res)
        job = os.path.basename(os.path.dirname(trial))
        m = JOB.match(job)
        if not m or (grades.get(job) or {}).get("resolved") is not None:
            continue
        task = m.group(3)
        if (meta.get(task) or {}).get("verifier") != "multi-swe-bench":
            continue
        org, repo, num = parts(task)
        pp = os.path.join(trial, "artifacts", "logs", "artifacts", "agent.patch")
        raw = open(pp).read() if os.path.exists(pp) else ""
        ds = {d["number"]: d for d in map(json.loads, open(f"{args.dataset}/{org}__{repo}_dataset.jsonl"))}
        test_files = files(ds[num]["test_patch"])
        patch = "".join(c for a, b, c in chunks(raw) if a not in test_files and b not in test_files)
        if not patch.strip():
            grades[job] = {"task": task, "resolved": False, "cat": "empty_patch" if not raw.strip() else "only_test_patch_files"}
            continue
        if not pull(image(org, repo, num)):
            grades[job] = {"task": task, "resolved": None, "cat": "official_image_unavailable"}
            continue
        todo.setdefault((org, repo, job.split("-")[0]), []).append((job, num, patch))
    for (org, repo, arm), items in sorted(todo.items()):
        # one call per arm and repository, so an instance appears once per call
        seen, batch, rest = set(), [], []
        for it in items:
            (rest if it[1] in seen else batch).append(it); seen.add(it[1])
        for chunk in (batch, rest):
            if chunk:
                for job, v in harness_call(args, org, repo, chunk).items():
                    grades[job] = {"task": JOB.match(job).group(3), **v}
                json.dump(grades, open(gf, "w"), indent=1)
    json.dump(grades, open(gf, "w"), indent=1)
    done = [v for v in grades.values() if v.get("resolved") is not None]
    print(f"{len(done)} graded, {sum(1 for v in done if v['resolved'])} resolved, {len(grades) - len(done)} without an answer")


if __name__ == "__main__":
    main()
