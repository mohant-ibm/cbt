"""
Unit tests for post_processing/regression/decisions.py.

Tests cover:
- Workload decision: table-driven for all flag combinations
- Run decision: K formula for various N, FC/RC combinations, N=0 error
"""

import unittest

from post_processing.post_processing_types import (
    Band,
    Decision,
    FlagResult,
    FlagTier,
    Thresholds,
)
from post_processing.regression.decisions import (
    _compute_fail_target,
    compute_run_decision,
    compute_workload_decision,
)

_T = Thresholds(weak=0.10, strong=0.20)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flag(tier: FlagTier, band: Band) -> FlagResult:
    return FlagResult(
        tier=tier,
        band=band,
        baseline_value=1.0,
        candidate_value=1.0,
        pct_change=0.0,
        reason=None,
        iodepth=None,
    )


def _h(band: Band) -> FlagResult:
    return _flag(FlagTier.H, band)


def _m(band: Band) -> FlagResult:
    return _flag(FlagTier.M, band)


# ---------------------------------------------------------------------------
# Workload decision
# ---------------------------------------------------------------------------


class TestWorkloadDecision(unittest.TestCase):
    def test_h_strong_gives_fail(self) -> None:
        flags = [_h(Band.STRONG), _m(Band.VALID), _m(Band.VALID)]
        result = compute_workload_decision(flags)
        self.assertEqual(result.decision, Decision.FAIL, "H STRONG must give FAIL")

    def test_h_weak_gives_review(self) -> None:
        flags = [_h(Band.WEAK), _m(Band.VALID)]
        result = compute_workload_decision(flags)
        self.assertEqual(result.decision, Decision.REVIEW, "H WEAK must give REVIEW")

    def test_m_strong_gives_review(self) -> None:
        flags = [_h(Band.VALID), _m(Band.STRONG)]
        result = compute_workload_decision(flags)
        self.assertEqual(result.decision, Decision.REVIEW, "M STRONG must give REVIEW")

    def test_m_weak_gives_review(self) -> None:
        flags = [_h(Band.VALID), _m(Band.WEAK)]
        result = compute_workload_decision(flags)
        self.assertEqual(result.decision, Decision.REVIEW, "M WEAK must give REVIEW")

    def test_all_valid_gives_pass(self) -> None:
        flags = [_h(Band.VALID), _h(Band.VALID), _m(Band.VALID), _m(Band.VALID)]
        result = compute_workload_decision(flags)
        self.assertEqual(result.decision, Decision.PASS, "All VALID must give PASS")

    def test_not_evaluable_gives_review(self) -> None:
        """NOT_EVALUABLE contributes to REVIEW"""
        flags = [_h(Band.VALID), _m(Band.NOT_EVALUABLE)]
        result = compute_workload_decision(flags)
        self.assertEqual(result.decision, Decision.REVIEW, "NOT_EVALUABLE must give REVIEW")

    def test_not_evaluable_does_not_override_fail(self) -> None:
        """H STRONG gives FAIL even when other flags are NOT_EVALUABLE."""
        flags = [_h(Band.STRONG), _m(Band.NOT_EVALUABLE)]
        result = compute_workload_decision(flags)
        self.assertEqual(result.decision, Decision.FAIL, "FAIL takes precedence over NOT_EVALUABLE")

    def test_h_strong_overrides_m_weak(self) -> None:
        flags = [_h(Band.STRONG), _m(Band.WEAK)]
        result = compute_workload_decision(flags)
        self.assertEqual(result.decision, Decision.FAIL)

    def test_workload_decision_stores_flags(self) -> None:
        flags = [_h(Band.VALID), _m(Band.VALID)]
        result = compute_workload_decision(flags)
        self.assertEqual(result.flags, flags)

    def test_empty_flags_gives_pass(self) -> None:
        result = compute_workload_decision([])
        self.assertEqual(result.decision, Decision.PASS)


# ---------------------------------------------------------------------------
# fail_target formula: max(1, min(3, ceil(N * 0.5)))
# ---------------------------------------------------------------------------


class TestFailTarget(unittest.TestCase):
    def test_n1(self) -> None:
        self.assertEqual(_compute_fail_target(1), 1)

    def test_n2(self) -> None:
        self.assertEqual(_compute_fail_target(2), 1)  # ceil(2*0.5)=1

    def test_n3(self) -> None:
        self.assertEqual(_compute_fail_target(3), 2)  # ceil(3*0.5)=2

    def test_n6(self) -> None:
        self.assertEqual(_compute_fail_target(6), 3)  # ceil(6*0.5)=3, capped at 3

    def test_n10(self) -> None:
        self.assertEqual(_compute_fail_target(10), 3)  # ceil(10*0.5)=5, capped at 3


# ---------------------------------------------------------------------------
# Run decision
# ---------------------------------------------------------------------------


class TestRunDecision(unittest.TestCase):
    def test_fc_ge_k_gives_fail(self) -> None:
        """FC=3, N=8 → fail_target=3 → FAIL (FC >= fail_target)."""
        result = compute_run_decision(num_workloads=8, fail_count=3, review_count=0)
        self.assertEqual(result.decision, Decision.FAIL)
        self.assertEqual(result.fail_target, 3)

    def test_fc_lt_k_but_gt_0_gives_review(self) -> None:
        """FC=1, N=8 → K=3 → FC>0 → REVIEW."""
        result = compute_run_decision(num_workloads=8, fail_count=1, review_count=0)
        self.assertEqual(result.decision, Decision.REVIEW)

    def test_rc_1_gives_pass(self) -> None:
        """RC=1, FC=0 → PASS (single REVIEW does not escalate run decision)."""
        result = compute_run_decision(num_workloads=8, fail_count=0, review_count=1)
        self.assertEqual(result.decision, Decision.PASS)

    def test_rc_2_gives_review(self) -> None:
        """RC=2, FC=0 → REVIEW (RC > 1)."""
        result = compute_run_decision(num_workloads=8, fail_count=0, review_count=2)
        self.assertEqual(result.decision, Decision.REVIEW)

    def test_all_pass_gives_pass(self) -> None:
        result = compute_run_decision(num_workloads=6, fail_count=0, review_count=0)
        self.assertEqual(result.decision, Decision.PASS)

    def test_n0_raises(self) -> None:
        with self.assertRaises(ValueError, msg="N=0 must raise ValueError"):
            compute_run_decision(num_workloads=0, fail_count=0, review_count=0)

    def test_run_decision_contains_counts(self) -> None:
        result = compute_run_decision(num_workloads=6, fail_count=1, review_count=0)
        self.assertEqual(result.num_workloads, 6)
        self.assertEqual(result.fail_count, 1)
        self.assertEqual(result.review_count, 0)
        self.assertEqual(result.fail_target, 3)


if __name__ == "__main__":
    unittest.main()
