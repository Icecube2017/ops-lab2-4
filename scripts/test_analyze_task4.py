import csv
import contextlib
import importlib.util
import io
import json
import math
from pathlib import Path
import tempfile
import tarfile
import unittest


SPEC = importlib.util.spec_from_file_location("analyze_task4", Path(__file__).with_name("analyze_task4.py"))
analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analysis)


class Task4Tests(unittest.TestCase):
    def test_burn_rate(self):
        result = analysis.burn_rate(0.9676)
        self.assertAlmostEqual(result["burn_rate"], 3.24)
        self.assertAlmostEqual(result["budget_exhaustion_days"], 30 / 3.24)

    def test_perfect_sli_has_no_finite_exhaustion(self):
        result = analysis.burn_rate(1)
        self.assertEqual(result["burn_rate"], 0)
        self.assertIsNone(result["budget_exhaustion_days"])

    def test_reject_invalid_sli(self):
        for value in (-0.1, 1.1, math.nan):
            with self.assertRaises(ValueError):
                analysis.burn_rate(value)

    def test_little_law_converts_milliseconds(self):
        run = {"red": {"1": {
            "step": "1", "target_rps": "100", "server_rps": "100",
            "server_mean_ms": "20", "inflight_avg": "2",
        }}}
        row = analysis.little_law(run)[0]
        self.assertEqual(row["lambda_times_mean_seconds"], 2)
        self.assertEqual(row["relative_difference"], 0)

    def test_reject_duplicate_csv_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "steps.csv"
            with path.open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerows((("step", "value"), ("1", "2"), ("1", "3")))
            with self.assertRaises(ValueError):
                analysis.read_csv(path)

    def test_actual_read_evidence(self):
        runs = analysis.ROOT / "runs"
        light, r1, r2 = (analysis.read_run(runs / name) for name in ("t1-light", "r1-pool4", "r2-pool44"))
        result = analysis.read_summary(r1, r2, light)
        self.assertEqual(result["r1_max_ok_step"], "7")
        self.assertEqual(result["r2_max_ok_step"], "6")
        self.assertEqual(result["first_bad_slo"]["step"], "6")
        self.assertEqual(result["r1_actual_merged_p99_ms_from_loadgen_all"], 1011.22)
        self.assertFalse(result["global_p99_recomputable_from_stage_summaries"])
        self.assertEqual(len(analysis.latency_comparison(r1)), 9)
        self.assertAlmostEqual(analysis.little_law(r1)[5]["lambda_times_mean_seconds"], 7.383719498)

    def test_missing_writes_cannot_be_reported_as_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                status = analysis.main([
                    "--out", directory,
                    "--w1", "test-not-measured-each",
                    "--w2", "test-not-measured-group",
                    "--w3", "test-not-measured-interval",
                ])
            summary = json.loads((Path(directory) / "summary.json").read_text())
            self.assertEqual(status, 2)
            self.assertFalse(summary["complete"])
            self.assertEqual(len(summary["missing_write_runs"]), 3)
            self.assertEqual(summary["write_paths"], [])

    def test_averaging_p99_is_not_merging(self):
        def p99(values):
            return sorted(values)[math.ceil(0.99 * len(values)) - 1]

        a, b = [1] * 100, [100] * 100
        self.assertEqual((p99(a) + p99(b)) / 2, 50.5)
        self.assertEqual(p99(a + b), 100)

    def test_actual_write_evidence(self):
        runs = analysis.ROOT / "runs"
        each, group, interval = (analysis.read_run(runs / name) for name in ("w1-each", "w2-group", "w3-interval"))
        result = analysis.write_analysis(each, group, interval)
        self.assertAlmostEqual(result["baseline_fsync_mean_ms"], 4.462 / 3)
        self.assertEqual(analysis.write_summary(each)["max_ok_step"], "6")
        self.assertEqual(analysis.write_summary(each)["max_ok_rps"], 668.4)
        self.assertEqual(result["similar_disk_util"]["group_step"], "7")
        self.assertAlmostEqual(result["similar_disk_util"]["throughput_ratio"], 2683.2 / 668.4)
        self.assertFalse(result["prediction_timing_independently_verified"])
        self.assertFalse(result["group_and_interval_saturated_upper_bound_observed"])

    def test_same_load_comparison_is_not_a_capacity_claim(self):
        runs = analysis.ROOT / "runs"
        group, interval = (analysis.read_run(runs / name) for name in ("w2-group", "w3-interval"))
        pairs = analysis.compare_writes(group, interval)
        highest = max(pairs, key=lambda row: row["target_rps"])
        self.assertEqual(highest["target_rps"], 2692)
        self.assertEqual(highest["first_step"], "7")
        self.assertEqual(highest["second_step"], "3")
        self.assertAlmostEqual(highest["mean_latency_speedup"], 5.15)
        self.assertAlmostEqual(highest["p99_latency_speedup"], 5.968)
        self.assertLess(highest["second_to_first_throughput_ratio"], 1.001)

    def test_imported_writes_match_original_archive(self):
        archive = analysis.ROOT / "runs" / "task4-analysis" / "task3-source-6041c14.tar.gz"
        with tarfile.open(archive) as source:
            self.assertEqual(source.pax_headers["comment"], "6041c148488a5be92c70d09e39f93b6223f4cd27")
            for member in source.getmembers():
                if not member.isfile():
                    continue
                target = analysis.ROOT / member.name
                if member.name == "LAB2.md":
                    target = archive.with_name("task3-source-LAB2.md")
                with source.extractfile(member) as handle:
                    self.assertEqual(handle.read(), target.read_bytes(), member.name)

    def test_strict_run_succeeds_with_all_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                status = analysis.main(["--out", directory])
            summary = json.loads((Path(directory) / "summary.json").read_text())
            self.assertEqual(status, 0)
            self.assertTrue(summary["complete"])
            self.assertEqual(summary["missing_write_runs"], [])
            self.assertEqual(len(summary["write_paths"]), 3)
            self.assertTrue((Path(directory) / "write-matched-loads.csv").is_file())


if __name__ == "__main__":
    unittest.main()
