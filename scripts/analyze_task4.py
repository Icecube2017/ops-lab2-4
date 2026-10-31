#!/usr/bin/env python3
"""Recalculate task 4 from the original CSVs without changing measurements."""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BASELINE_FSYNC_MEANS_MS = (1.512, 1.485, 1.465)


def number(row, column):
    value = float(row[column])
    if not math.isfinite(value):
        raise ValueError(f"step {row['step']}: {column} is not finite")
    return value


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    indexed = {}
    for row in rows:
        step = row["step"]
        if step in indexed:
            raise ValueError(f"{path}: duplicate step {step}")
        indexed[step] = row
    if not indexed:
        raise ValueError(f"{path}: no data")
    return indexed


def read_run(path):
    tables = {name: read_csv(path / f"{name}.csv") for name in ("steps", "red", "use")}
    steps = {key: row for key, row in tables["steps"].items() if key != "all"}
    if not steps:
        raise ValueError(f"{path}: no measured stages")
    for name in ("red", "use"):
        if set(tables[name]) != set(steps):
            raise ValueError(f"{path}: {name}.csv stages do not match steps.csv")
        for key in steps:
            if number(steps[key], "target_rps") != number(tables[name][key], "target_rps"):
                raise ValueError(f"{path}: {name}.csv target differs at step {key}")
    for row in steps.values():
        outcomes = sum(int(row[key]) for key in ("ok", "http_err", "timeout", "net_err"))
        if int(row["sent"]) != outcomes:
            raise ValueError(f"{path}: step {row['step']}: outcomes do not sum to sent")
        if number(row, "end_unix") <= number(row, "start_unix"):
            raise ValueError(f"{path}: step {row['step']}: nonpositive duration")
    if "all" not in tables["steps"]:
        raise ValueError(f"{path}: steps.csv has no all row")
    for column in ("sent", "ok", "http_err", "timeout", "net_err", "dropped"):
        if sum(int(row[column]) for row in steps.values()) != int(tables["steps"]["all"][column]):
            raise ValueError(f"{path}: all.{column} does not equal the stage totals")
    text = (path / "meta.txt").read_text(encoding="utf-8")
    _, marker, config = text.partition("enrolld /config:\n")
    if not marker:
        raise ValueError(f"{path}: meta.txt has no service configuration")
    return {
        "path": path,
        "steps": steps,
        "all": tables["steps"]["all"],
        "red": tables["red"],
        "use": tables["use"],
        "config": json.loads(config),
    }


def burn_rate(good, objective=0.99, days=30):
    if not 0 <= good <= 1 or not 0 < objective < 1 or days <= 0:
        raise ValueError("invalid SLI, objective, or period")
    burn = (1 - good) / (1 - objective)
    return {
        "client_slo_good": good,
        "bad_fraction": 1 - good,
        "objective": objective,
        "burn_rate": burn,
        "budget_exhaustion_days": days / burn if burn else None,
        "budget_exhaustion_hours": days * 24 / burn if burn else None,
        "assumption": "Full initial budget; constant traffic representative of the 30-day request volume.",
        "precision": "Approximate: client slo_good is rounded to four decimal places in steps.csv.",
    }


def latency_comparison(run):
    result = []
    for step, client in run["steps"].items():
        server = run["red"][step]
        result.append({
            "step": step,
            "target_rps": number(client, "target_rps"),
            "client_mean_ms": number(client, "mean_ms"),
            "server_mean_ms": number(server, "server_mean_ms"),
            "client_minus_server_mean_ms": number(client, "mean_ms") - number(server, "server_mean_ms"),
            "client_p99_ms": number(client, "p99_ms"),
            "server_p99_ms": number(server, "server_p99_ms"),
            "client_minus_server_p99_ms": number(client, "p99_ms") - number(server, "server_p99_ms"),
            "client_slo_good": number(client, "slo_good"),
            "server_slo_good": number(server, "server_slo_good"),
            "client_minus_server_sli_pp": 100 * (number(client, "slo_good") - number(server, "server_slo_good")),
        })
    return result


def little_law(run):
    result = []
    for step, server in run["red"].items():
        predicted = number(server, "server_rps") * number(server, "server_mean_ms") / 1000
        observed = number(server, "inflight_avg")
        result.append({
            "step": step,
            "target_rps": number(server, "target_rps"),
            "server_rps": number(server, "server_rps"),
            "server_mean_ms": number(server, "server_mean_ms"),
            "lambda_times_mean_seconds": predicted,
            "inflight_avg": observed,
            "relative_difference": (predicted - observed) / observed if observed else None,
        })
    return result


def read_summary(r1, r2, light):
    baseline = light["red"]["3"]
    r1_best = max(r1["steps"].values(), key=lambda row: number(row, "ok_rps"))
    r2_best = max(r2["steps"].values(), key=lambda row: number(row, "ok_rps"))
    accepted = [row for row in r1["steps"].values() if number(row, "p99_ms") <= 50]
    first_bad = next((row for row in r1["steps"].values() if number(row, "slo_good") < 0.99), None)
    pool_limit = r1["config"]["pool"] * 1000 / number(baseline, "db_query_mean_ms")
    r2_limit = r2["config"]["gomaxprocs"] * 1000 / number(r1["red"]["6"], "cpu_ms_per_req")
    p99s = [number(row, "p99_ms") for row in r1["steps"].values()]
    summary = {
        "r1_pool_limit_rps": pool_limit,
        "r1_cpu_limit_from_light_rps": r1["config"]["gomaxprocs"] * 1000 / number(baseline, "cpu_ms_per_req"),
        "r1_max_ok_rps": number(r1_best, "ok_rps"),
        "r1_max_ok_step": r1_best["step"],
        "r1_pool_prediction_relative_difference": (number(r1_best, "ok_rps") - pool_limit) / pool_limit,
        "r1_max_tested_p99_compliant_target_rps": max(number(row, "target_rps") for row in accepted) if accepted else None,
        "r1_p99_compliant_fraction_of_pool_limit": max(number(row, "target_rps") for row in accepted) / pool_limit if accepted else None,
        "r2_cpu_limit_from_r1_step6_rps": r2_limit,
        "r2_max_ok_rps": number(r2_best, "ok_rps"),
        "r2_max_ok_step": r2_best["step"],
        "r2_cpu_prediction_relative_difference": (number(r2_best, "ok_rps") - r2_limit) / r2_limit,
        "r1_arithmetic_mean_of_stage_p99_ms_INVALID_AS_GLOBAL_P99": sum(p99s) / len(p99s),
        "r1_success_weighted_mean_of_stage_p99_ms_ALSO_INVALID": sum(number(row, "ok") * number(row, "p99_ms") for row in r1["steps"].values()) / number(r1["all"], "ok"),
        "r1_actual_merged_p99_ms_from_loadgen_all": number(r1["all"], "p99_ms"),
        "global_p99_recomputable_from_stage_summaries": False,
    }
    summary["first_bad_slo"] = {
        "step": first_bad["step"],
        "target_rps": number(first_bad, "target_rps"),
        **burn_rate(number(first_bad, "slo_good")),
    } if first_bad else None
    return summary


def write_summary(run):
    best = max(run["steps"].values(), key=lambda row: number(row, "ok_rps"))
    step = best["step"]
    server, resource = run["red"][step], run["use"][step]
    fsync_rate = number(server, "fsync_per_s")
    return {
        "run": run["path"].name,
        "sync": run["config"]["sync"],
        "max_ok_step": step,
        "target_rps": number(best, "target_rps"),
        "max_ok_rps": number(best, "ok_rps"),
        "client_mean_ms": number(best, "mean_ms"),
        "client_p99_ms": number(best, "p99_ms"),
        "cpu_cores": number(server, "cpu_cores"),
        "fsync_per_s": fsync_rate,
        "fsync_mean_ms": number(server, "fsync_mean_ms"),
        "batch_mean": number(server, "batch_mean"),
        "device_writes_per_app_fsync": number(resource, "disk_w_per_s") / fsync_rate if fsync_rate else None,
        "device_flushes_per_app_fsync": number(resource, "disk_f_per_s") / fsync_rate if fsync_rate else None,
        **{key: number(resource, key) for key in (
            "mem_available_min_mb", "swap_in_per_s", "svc_cpu_busy", "cpu_iowait",
            "disk_w_per_s", "disk_w_await_ms", "disk_f_per_s", "disk_f_await_ms",
            "disk_aqu_sz", "disk_util",
        )},
    }


def compare_writes(first, second):
    pairs = []
    second_by_target = {number(row, "target_rps"): row for row in second["steps"].values()}
    for row in first["steps"].values():
        target = number(row, "target_rps")
        other = second_by_target.get(target)
        if other is None or int(row["ok"]) == 0 or int(other["ok"]) == 0:
            continue
        pairs.append({
            "first_run": first["path"].name,
            "second_run": second["path"].name,
            "target_rps": target,
            "first_step": row["step"],
            "second_step": other["step"],
            "first_ok_rps": number(row, "ok_rps"),
            "second_ok_rps": number(other, "ok_rps"),
            "first_mean_ms": number(row, "mean_ms"),
            "second_mean_ms": number(other, "mean_ms"),
            "mean_latency_speedup": number(row, "mean_ms") / number(other, "mean_ms"),
            "first_p99_ms": number(row, "p99_ms"),
            "second_p99_ms": number(other, "p99_ms"),
            "p99_latency_speedup": number(row, "p99_ms") / number(other, "p99_ms"),
            "second_to_first_throughput_ratio": number(other, "ok_rps") / number(row, "ok_rps"),
        })
    return pairs


def write_analysis(each, group, interval):
    each_best = write_summary(each)
    group_best = write_summary(group)
    group_step = min(
        group["use"],
        key=lambda step: abs(number(group["use"][step], "disk_util") - each_best["disk_util"]),
    )
    group_at_util = group["steps"][group_step]
    baseline_mean = sum(BASELINE_FSYNC_MEANS_MS) / len(BASELINE_FSYNC_MEANS_MS)
    predicted = 1000 / baseline_mean
    return {
        "baseline_source": "runs/task4-analysis/task3-source-LAB2.md, section 2 (existing transcript, not a new run)",
        "baseline_fsync_means_ms": BASELINE_FSYNC_MEANS_MS,
        "baseline_fsync_mean_ms": baseline_mean,
        "baseline_serial_tps": predicted,
        "each_peak_relative_difference_from_baseline": (each_best["max_ok_rps"] - predicted) / predicted,
        "each_peak_inverse_current_fsync_ms": 1000 / each_best["fsync_mean_ms"],
        "similar_disk_util": {
            "each_step": each_best["max_ok_step"],
            "group_step": group_step,
            "each_disk_util": each_best["disk_util"],
            "group_disk_util": number(group["use"][group_step], "disk_util"),
            "each_ok_rps": each_best["max_ok_rps"],
            "group_ok_rps": number(group_at_util, "ok_rps"),
            "throughput_ratio": number(group_at_util, "ok_rps") / each_best["max_ok_rps"],
            "difference_rps": number(group_at_util, "ok_rps") - each_best["max_ok_rps"],
        },
        "each_vs_group_same_target": compare_writes(each, group),
        "group_vs_interval_same_target": compare_writes(group, interval),
        "group_and_interval_saturated_upper_bound_observed": False,
        "prediction_timing_independently_verified": False,
        "source_report_note": (
            "Section 5.3 records about 670 RPS, batch 1-2 and p99 10-50 ms at target 673 RPS. "
            "The only exported task-3 commit is after the measurements, so the prediction timing is unverified. "
            "The report's W1 step-6 reference to 1264 ms disagrees with the original steps.csv (389.23 ms)."
        ),
        "group_peak_ok_rps": group_best["max_ok_rps"],
    }


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--r1", default="r1-pool4")
    parser.add_argument("--r2", default="r2-pool44")
    parser.add_argument("--w1", default="w1-each")
    parser.add_argument("--w2", default="w2-group")
    parser.add_argument("--w3", default="w3-interval")
    parser.add_argument("--out", default="task4-analysis")
    parser.add_argument("--allow-missing-write", action="store_true")
    args = parser.parse_args(argv)
    runs = args.root / "runs"
    paths = [runs / name for name in ("t1-light", args.r1, args.r2)]
    loaded = [read_run(path) for path in paths]
    summary = read_summary(loaded[1], loaded[2], loaded[0])
    write_results, missing, write_runs = [], [], []
    for name, expected_mode in ((args.w1, "each"), (args.w2, "group"), (args.w3, "interval")):
        path = runs / name
        if not path.is_dir():
            missing.append(name)
            continue
        run = read_run(path)
        if run["config"]["sync"] != expected_mode:
            raise ValueError(f"{path}: expected sync={expected_mode}")
        paths.append(path)
        write_runs.append(run)
        write_results.append(write_summary(run))
    summary["missing_write_runs"] = missing
    summary["complete"] = not missing
    summary["write_paths"] = write_results
    summary["write_analysis"] = write_analysis(*write_runs) if not missing else None
    summary["input_sha256"] = {
        str(path.relative_to(args.root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for directory in paths
        for path in sorted(directory.iterdir())
        if path.is_file()
    }
    for name in ("task3-source-LAB2.md", "task3-source-6041c14.tar.gz"):
        path = runs / "task4-analysis" / name
        if path.is_file():
            summary["input_sha256"][str(path.relative_to(args.root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    out = runs / args.out
    if out.resolve() in [path.resolve() for path in paths]:
        raise ValueError("output directory must not be an input run")
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8",
    )
    write_csv(out / "r1-latency-comparison.csv", latency_comparison(loaded[1]))
    write_csv(out / "r2-latency-comparison.csv", latency_comparison(loaded[2]))
    write_csv(out / "r1-little-law.csv", little_law(loaded[1]))
    write_csv(out / "write-comparison.csv", write_results)
    if not missing:
        write_csv(out / "write-matched-loads.csv", [
            *summary["write_analysis"]["each_vs_group_same_target"],
            *summary["write_analysis"]["group_vs_interval_same_target"],
        ])
    print(json.dumps({key: value for key, value in summary.items() if key != "input_sha256"}, indent=2))
    if missing and not args.allow_missing_write:
        print("Missing write-path evidence; task 4 is not complete.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as exc:
        print(f"analyze_task4: {exc}", file=sys.stderr)
        sys.exit(1)
