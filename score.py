#!/usr/bin/env python3
"""score.py [JOBS_DIR]: score every trial in JOBS_DIR (default jobs/) the way the paper does, print a per-arm summary
and the paired LSP/Plain ratios, and write JOBS_DIR/scores.json.

Navigation: the last SITES: ... END block of the final answer, at most 10 lines, matched against tasks/TASK/gold.json
(file by path suffix; a function site needs the same function name, without namespaces, template arguments or
parameters; optional gold sites count neither for recall nor as false positives; each task's denominator is capped at
10). Repairs: Harbor's verifier (post-cutoff tasks) or JOBS_DIR/mswe_grades.json from grade_mswe.py (Multi-SWE-bench).
Tokens and cost: every model call in the API proxy's log; cost at list prices (Opus: $4 input, $5 cache write, $0.20
cache read, $20 output per million tokens; Sonnet half), with the first call's cache reads priced as a write so a run
does not depend on whether an earlier run warmed the cache."""
import collections, glob, json, os, re, shlex, sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
JOBS = sys.argv[1] if len(sys.argv) > 1 else "jobs"
JOB = re.compile(r"^(plain|lsp-optional|lsp)-(nav|eo|e2e)-(.+)-\d{8}-\d{6}$")
PRICE = {"claude-opus-5-5": (4.0, 0.20, 5.0, 20.0), "claude-sonnet-5-5": (2.0, 0.10, 2.50, 10.0)}
CAP = 10
GREPS = {"grep", "rg", "ag", "egrep", "fgrep"}


def is_grep(cmd):
    """a Bash call with a grep, rg, ag or git grep command in any of its segments (the paper's grep count)"""
    for part in re.split(r"&&|\|\||;|\n", cmd or ""):
        try:
            toks = shlex.split(part.split("|")[0])
        except ValueError:
            toks = part.split("|")[0].split()
        while toks and (re.match(r"^\w+=", toks[0]) or toks[0] in ("cd", "timeout", "nice", "time", "env")):
            toks = toks[2:] if toks[0] == "cd" else toks[1:]
        if toks and (os.path.basename(toks[0]) in GREPS or (toks[0] == "git" and len(toks) > 1 and toks[1] == "grep")):
            return True
    return False


def norm_path(p):
    p = p.strip().strip("`*").strip()
    return re.sub(r"^(\./|/app/|app/)", "", p)


def norm_fn(f):
    f = (f or "").strip().strip("`*").strip().replace("(anonymous namespace)::", "")
    m = re.match(r"^(.*?\boperator\s*(?:\(\)|[^\s(]+))", f)
    f = m.group(1) if m else re.sub(r"\(.*$", "", f)
    while "<" in f and "operator" not in f:
        g = re.sub(r"<[^<>]*>", "", f)
        if g == f:
            break
        f = g
    return re.sub(r"\s+", "", re.split(r"::(?![^<]*>)", f)[-1])


def parse(text):
    text = re.sub(r"\*\*\s*(SITES:|END)\s*\*\*", r"\1", text or "")
    blocks = re.findall(r"SITES:\s*\n(.*?)\n\s*END\b", text, re.S)
    if not blocks:
        return None
    out = []
    for line in blocks[-1].splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line.strip()).strip("`").strip()
        line = re.sub(r"^([\w./+-]+\.[A-Za-z0-9]+)::(?=[A-Za-z_~])", r"\1:", line)
        if not line:
            continue
        m = re.match(r"^([\w./+-]+\.[A-Za-z0-9]+)(?::\d+(?:-\d+)?)?(?::(.+))?$", line) or re.match(r"^([\w./+-]+)(?::(.+))?$", line)
        if m:
            e = (norm_path(m.group(1)), (m.group(2) or "").strip())
            if e not in out:
                out.append(e)
    return out[:CAP]


def same_file(a, g):
    return a == g or g.endswith("/" + a) or a.endswith("/" + g)


def grade(ans, gold):
    match = lambda path, fn, s: same_file(path, s["file"]) and (s["function"] is None or (fn and norm_fn(fn) == norm_fn(s["function"])))
    used, hit, opt = set(), 0, 0
    for path, fn in ans:
        j = next((j for j, s in enumerate(gold) if j not in used and not s.get("optional") and match(path, fn, s)), None)
        if j is not None:
            used.add(j); hit += 1
        elif any(s.get("optional") and match(path, fn, s) for s in gold):
            opt += 1
    req = [s for s in gold if not s.get("optional")]
    n = min(len(req), CAP)
    den = len(ans) - opt
    return {"matched": len(used), "n_gold": n, "recall": len(used) / n if n else None,
            "precision": hit / den if den > 0 else 0.0, "complete": int(len(used) >= n)}


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None


def trial(t):
    res = json.load(open(f"{t}/result.json"))
    ag = res.get("agent_execution") or {}
    r = {"time_s": round((ts(ag.get("finished_at")) or 0) - (ts(ag.get("started_at")) or 0)),
         "error": (res.get("exception_info") or {}).get("exception_type") if res.get("exception_info") else None}
    calls = [json.loads(l) for l in open(f"{t}/artifacts/var/log/api-proxy/requests.jsonl", errors="replace")
             if '"forwarded"' in l and '"/v1/messages"' in l] if os.path.exists(f"{t}/artifacts/var/log/api-proxy/requests.jsonl") else []
    u = lambda e, k: int((e.get("usage") or {}).get(k) or 0)
    tin, cr, cw, out = (sum(u(e, k) for e in calls) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"))
    pi, pr, pw, po = PRICE.get((calls[0].get("model") if calls else None) or "claude-opus-5-5", PRICE["claude-opus-5-5"])
    raw = (tin * pi + cr * pr + cw * pw + out * po) / 1e6
    r.update(model_calls=len(calls), tokens=tin + cr + cw + out,
             cost=round(raw + ((u(calls[0], "cache_read_input_tokens") * (pw - pr)) / 1e6 if calls else 0), 5))
    ops, grep, final = collections.Counter(), 0, ""
    tx = f"{t}/agent/claude-code.txt"
    for l in open(tx, errors="replace") if os.path.exists(tx) else []:
        try:
            o = json.loads(l)
        except ValueError:
            continue
        if o.get("type") != "assistant":
            continue
        for b in o["message"].get("content", []):
            if b.get("type") == "text":
                final = b["text"]
            elif b.get("type") == "tool_use" and b["name"] == "LSP":
                ops[(b.get("input") or {}).get("operation", "?")] += 1
            elif b.get("type") == "tool_use" and b["name"] == "Bash" and is_grep((b.get("input") or {}).get("command", "")):
                grep += 1
    r.update(lsp_calls=sum(ops.values()), lsp_ops=dict(ops), grep_calls=grep, final=final)
    rw = f"{t}/verifier/reward.txt"
    r["reward"] = float(open(rw).read().strip()) if os.path.exists(rw) else None
    return r


def main():
    mswe = json.load(open(f"{JOBS}/mswe_grades.json")) if os.path.exists(f"{JOBS}/mswe_grades.json") else {}
    rows = []
    for res in sorted(glob.glob(f"{JOBS}/*/*/result.json")):
        t = os.path.dirname(res)
        job = os.path.basename(os.path.dirname(t))
        m = JOB.match(job)
        if not m:
            continue
        arm, tier, task = m.groups()
        r = {"job": job, "arm": arm, "tier": tier, "task": task, **trial(t)}
        if tier == "nav":
            gold = json.load(open(f"{HERE}/tasks/{task}/gold.json"))
            ans = parse(r["final"])
            r.update(grade(ans, gold) if ans is not None else {"matched": 0, "n_gold": min(CAP, sum(1 for s in gold if not s.get("optional"))),
                                                               "recall": 0.0, "precision": 0.0, "complete": 0})
            r["parsed"] = ans is not None
        else:
            g = mswe.get(job)
            r["resolved"] = (g or {}).get("resolved") if g is not None else (None if r["reward"] is None else r["reward"] >= 1.0)
        r.pop("final")
        rows.append(r)
    json.dump(rows, open(f"{JOBS}/scores.json", "w"), indent=1)
    by = collections.defaultdict(list)
    for r in rows:
        by[r["arm"]].append(r)
    print(f"{len(rows)} trials in {JOBS}\n")
    print(f"{'arm':13s} {'runs':>4s} {'nav recall':>10s} {'eo solved':>10s} {'e2e solved':>11s} {'LSP used':>9s} {'grep used':>9s} {'tokens':>8s} {'cost':>7s}")
    for arm in ("plain", "lsp", "lsp-optional"):
        rs = by.get(arm, [])
        if not rs:
            continue
        nav = [r for r in rs if r["tier"] == "nav"]
        rec = sum(r["matched"] for r in nav) / max(1, sum(r["n_gold"] for r in nav))
        sol = lambda ti: f"{sum(1 for r in rs if r['tier'] == ti and r.get('resolved'))}/{sum(1 for r in rs if r['tier'] == ti and r.get('resolved') is not None)}"
        print(f"{arm:13s} {len(rs):4d} {100 * rec:9.1f}% {sol('eo'):>10s} {sol('e2e'):>11s} {100 * sum(r['lsp_calls'] > 0 for r in rs) / len(rs):8.0f}%"
              f" {100 * sum(r['grep_calls'] > 0 for r in rs) / len(rs):8.0f}% {sum(r['tokens'] for r in rs) / len(rs) / 1e3:7.0f}k ${sum(r['cost'] for r in rs) / len(rs):.3f}")
    ops = collections.Counter()
    for r in by.get("lsp", []):
        ops.update(r["lsp_ops"])
    if ops:
        print(f"\nLSP operations (enforced arm): " + ", ".join(f"{k} {100 * v / sum(ops.values()):.0f}%" for k, v in ops.most_common()))
    # paired over (task, tier): each arm's mean over its trials, then the ratio of sums
    cell = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        cell[(r["task"], r["tier"])][r["arm"]].append(r)
    for other in ("lsp", "lsp-optional"):
        pairs = [c for c in cell if "plain" in cell[c] and other in cell[c]]
        if not pairs:
            continue
        mean = lambda c, a, k: sum(x[k] for x in cell[c][a]) / len(cell[c][a])
        for k in ("cost", "tokens", "time_s"):
            p, o = sum(mean(c, "plain", k) for c in pairs), sum(mean(c, other, k) for c in pairs)
            print(f"{other}/plain {k:7s} x{o / p:.3f} over {len(pairs)} task-tier pairs" if p else "")
    print(f"\nwrote {JOBS}/scores.json")


if __name__ == "__main__":
    main()
