"""
End-to-end tests for the regression pipeline via run_comparison().

Uses real JSON files in temp directories — same pattern as test_regression_loader.py.
"""

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from post_processing.regression.runner import DEFAULT_THRESHOLDS, run_comparison


def _make_json(iodepths: list[str], iops: str = "1000", bw: str = "500000") -> dict[str, Any]:
    data: dict[str, Any] = {}
    for depth in iodepths:
        data[depth] = {
            "bandwidth_bytes": bw,
            "blocksize": "4096",
            "iops": "250",
            "latency": "4000000",
            "number_of_jobs": "1",
            "runtime_seconds": "60",
        }
    data.update(
        {
            "maximum_iops": iops,
            "maximum_bandwidth": bw,
            "maximum_cpu_usage": "0.50",
            "maximum_memory_usage": "1024",
            "benchmark": "librbdfio",
        }
    )
    return data


def _make_archive(base: Path, stems_and_data: dict[str, dict[str, Any]]) -> None:
    vis = base / "visualisation"
    vis.mkdir(parents=True, exist_ok=True)
    for stem, data in stems_and_data.items():
        (vis / f"{stem}.json").write_text(json.dumps(data), encoding="utf8")


class TestRunComparison(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_baseline_vs_itself_returns_0(self) -> None:
        """Comparing an archive against itself must return 0 (PASS)."""
        archive = self.tmp / "archive"
        _make_archive(archive, {"4096_1_randread": _make_json(["1", "2", "4"])})
        code = run_comparison(archive, archive, DEFAULT_THRESHOLDS)
        self.assertEqual(code, 0, "Baseline vs itself must return 0")

    def test_single_weak_workload_of_several_returns_0(self) -> None:
        """RC=1 out of 4 workloads → PASS → return 0."""
        baseline = self.tmp / "baseline"
        candidate = self.tmp / "candidate"
        _make_archive(
            baseline,
            {
                "4096_1_randread": _make_json(["1", "2", "4"], iops="1000"),
                "4096_1_randwrite": _make_json(["1", "2", "4"], iops="1000"),
                "256k_1_randread": _make_json(["1", "2", "4"], iops="1000"),
                "256k_1_randwrite": _make_json(["1", "2", "4"], iops="1000"),
            },
        )
        _make_archive(
            candidate,
            {
                "4096_1_randread": _make_json(["1", "2", "4"], iops="900"),  # -10% WEAK
                "4096_1_randwrite": _make_json(["1", "2", "4"], iops="1000"),
                "256k_1_randread": _make_json(["1", "2", "4"], iops="1000"),
                "256k_1_randwrite": _make_json(["1", "2", "4"], iops="1000"),
            },
        )
        code = run_comparison(baseline, candidate, DEFAULT_THRESHOLDS)
        self.assertEqual(code, 0, "RC=1 must return 0")

    def test_two_weak_workloads_returns_1(self) -> None:
        """RC=2 → REVIEW → return 1."""
        baseline = self.tmp / "baseline"
        candidate = self.tmp / "candidate"
        _make_archive(
            baseline,
            {
                "4096_1_randread": _make_json(["1", "2", "4"], iops="1000"),
                "4096_1_randwrite": _make_json(["1", "2", "4"], iops="1000"),
                "256k_1_randread": _make_json(["1", "2", "4"], iops="1000"),
            },
        )
        _make_archive(
            candidate,
            {
                "4096_1_randread": _make_json(["1", "2", "4"], iops="900"),  # -10% WEAK
                "4096_1_randwrite": _make_json(["1", "2", "4"], iops="900"),  # -10% WEAK
                "256k_1_randread": _make_json(["1", "2", "4"], iops="1000"),
            },
        )
        code = run_comparison(baseline, candidate, DEFAULT_THRESHOLDS)
        self.assertEqual(code, 1, "RC=2 must return 1")

    def test_strong_drop_returns_2(self) -> None:
        """FC >= fail_target → FAIL → return 2.
        N=2, fail_target=1; -25% IOPS on one workload → STRONG → FAIL."""
        baseline = self.tmp / "baseline"
        candidate = self.tmp / "candidate"
        _make_archive(
            baseline,
            {
                "4096_1_randread": _make_json(["1", "2", "4"], iops="1000"),
                "4096_1_randwrite": _make_json(["1", "2", "4"], iops="1000"),
            },
        )
        _make_archive(
            candidate,
            {
                "4096_1_randread": _make_json(["1", "2", "4"], iops="750"),  # -25% STRONG
                "4096_1_randwrite": _make_json(["1", "2", "4"], iops="1000"),
            },
        )
        code = run_comparison(baseline, candidate, DEFAULT_THRESHOLDS)
        self.assertEqual(code, 2, "FC >= fail_target must return 2")

    def test_nonexistent_baseline_returns_3(self) -> None:
        candidate = self.tmp / "candidate"
        _make_archive(candidate, {"4096_1_randread": _make_json(["1"])})
        code = run_comparison(self.tmp / "missing", candidate, DEFAULT_THRESHOLDS)
        self.assertEqual(code, 3)

    def test_no_intermediate_files_returns_3(self) -> None:
        empty = self.tmp / "empty"
        empty.mkdir()
        archive = self.tmp / "archive"
        _make_archive(archive, {"4096_1_randread": _make_json(["1"])})
        code = run_comparison(empty, archive, DEFAULT_THRESHOLDS)
        self.assertEqual(code, 3)


class TestSummaryFormattingHelpers(unittest.TestCase):
    """Test naming, sorting, and formatting helper functions."""

    def test_format_workload_name_standard(self) -> None:
        from post_processing.regression.summary import format_workload_name

        self.assertEqual(format_workload_name("4096_1_randread"), "4K randread")
        self.assertEqual(format_workload_name("1048576_1_write"), "1M write")
        self.assertEqual(format_workload_name("16384_1_70_30_randrw"), "16K 70/30 randrw")
        self.assertEqual(
            format_workload_name("4194304_1_randwrite (missing)"),
            "4M randwrite (missing)",
        )

    def test_sort_and_group_workload_decisions_cbt_order(self) -> None:
        from post_processing.post_processing_types import Decision, WorkloadDecision
        from post_processing.regression.summary import sort_and_group_workload_decisions

        items = [
            WorkloadDecision(stem="4096_1_randwrite", decision=Decision.PASS, flags=[]),
            WorkloadDecision(stem="1048576_1_read", decision=Decision.PASS, flags=[]),
            WorkloadDecision(stem="4096_1_read", decision=Decision.PASS, flags=[]),
            WorkloadDecision(stem="4096_1_randread", decision=Decision.PASS, flags=[]),
            WorkloadDecision(stem="1048576_1_write", decision=Decision.PASS, flags=[]),
        ]
        sections = sort_and_group_workload_decisions(items)
        titles = [title for title, _ in sections]

        # Sequential Read should come before Sequential Write before Random Read before Random Write
        self.assertIn("Sequential Read", titles)
        self.assertIn("Sequential Write", titles)
        self.assertIn("Random Read", titles)
        self.assertIn("Random Write", titles)

        read_sec = next(wds for t, wds in sections if t == "Sequential Read")
        self.assertEqual(read_sec[0].stem, "4096_1_read")
        self.assertEqual(read_sec[1].stem, "1048576_1_read")


if __name__ == "__main__":
    unittest.main()
