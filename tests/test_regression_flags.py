"""
Unit tests for post_processing/regression/flags.py.

Tests cover:
- Each flag (H1, H2, H3, M1, M2, M3): STRONG/WEAK/VALID/boundaries/NOT_EVALUABLE
- One-directional: improvements never fire
- Zero-baseline rules (direction-dependent)
- NOT_EVALUABLE: missing field, non-numeric value
- Iodepth group helpers: numeric ordering, group sizes for n=10 and n=7,
  different start points, worst-depth selection
- Fewer than 3 common iodepths → H3/M3 NOT_EVALUABLE
"""

import unittest

from post_processing.post_processing_types import Band, FlagTier, Thresholds
from post_processing.regression.flags import (
    compute_h1_max_iops,
    compute_h2_max_bandwidth,
    compute_h3_low_depth_latency,
    compute_m1_max_cpu,
    compute_m2_max_memory,
    compute_m3_high_depth_latency,
)

_T = Thresholds(weak=0.10, strong=0.20)

# ---------------------------------------------------------------------------
# Minimal data builders
# ---------------------------------------------------------------------------


def _headline(**overrides: str) -> dict[str, object]:
    """Return a dict with headline fields only (no iodepth entries)."""
    base: dict[str, object] = {
        "maximum_iops": "1000",
        "maximum_bandwidth": "500000",
        "maximum_cpu_usage": "0.50",
        "maximum_memory_usage": "1024",
        "benchmark": "librbdfio",
    }
    base.update(overrides)
    return base


def _depths(pairs: list[tuple[str, str]]) -> dict[str, object]:
    """
    Return a dict with iodepth entries only.
    pairs is [(depth_key, latency_ns_str), ...].
    """
    data: dict[str, object] = {}
    for depth, latency in pairs:
        data[depth] = {
            "bandwidth_bytes": "1000000",
            "blocksize": "4096",
            "iops": "250",
            "latency": latency,
            "number_of_jobs": "1",
            "runtime_seconds": "60",
        }
    return data


# ---------------------------------------------------------------------------
# H1: maximum_iops — fires when drops
# ---------------------------------------------------------------------------


class TestH1MaxIops(unittest.TestCase):
    def test_strong_drop(self) -> None:
        result = compute_h1_max_iops(_headline(maximum_iops="1000"), _headline(maximum_iops="750"), _T)
        self.assertEqual(result.band, Band.STRONG, "25% drop should be STRONG")
        self.assertEqual(result.tier, FlagTier.H)

    def test_weak_drop(self) -> None:
        result = compute_h1_max_iops(_headline(maximum_iops="1000"), _headline(maximum_iops="850"), _T)
        self.assertEqual(result.band, Band.WEAK, "15% drop should be WEAK")

    def test_small_drop_is_valid(self) -> None:
        result = compute_h1_max_iops(_headline(maximum_iops="1000"), _headline(maximum_iops="955"), _T)
        self.assertEqual(result.band, Band.VALID, "4.5% drop should be VALID")

    def test_improvement_is_valid(self) -> None:
        result = compute_h1_max_iops(_headline(maximum_iops="1000"), _headline(maximum_iops="1300"), _T)
        self.assertEqual(result.band, Band.VALID, "Improvement must not fire H1")

    def test_exact_weak_boundary(self) -> None:
        result = compute_h1_max_iops(_headline(maximum_iops="1000"), _headline(maximum_iops="900"), _T)
        self.assertEqual(result.band, Band.WEAK, "Exactly -10% should be WEAK")

    def test_exact_strong_boundary(self) -> None:
        result = compute_h1_max_iops(_headline(maximum_iops="1000"), _headline(maximum_iops="800"), _T)
        self.assertEqual(result.band, Band.STRONG, "Exactly -20% should be STRONG")

    def test_zero_baseline_zero_candidate_valid(self) -> None:
        result = compute_h1_max_iops(_headline(maximum_iops="0"), _headline(maximum_iops="0"), _T)
        self.assertEqual(result.band, Band.VALID, "Both zero → VALID (no change)")

    def test_zero_baseline_nonzero_candidate_valid_for_drop_flag(self) -> None:
        # drop-fires flag: candidate > baseline is an improvement
        result = compute_h1_max_iops(_headline(maximum_iops="0"), _headline(maximum_iops="1000"), _T)
        self.assertEqual(
            result.band,
            Band.VALID,
            "Zero baseline, positive candidate → improvement → VALID for H1",
        )

    def test_nonzero_baseline_zero_candidate_strong(self) -> None:
        # IOPS dropped to zero — worst possible regression
        result = compute_h1_max_iops(_headline(maximum_iops="1000"), _headline(maximum_iops="0"), _T)
        self.assertEqual(result.band, Band.STRONG, "IOPS dropped to zero → STRONG")

    def test_missing_baseline_field_not_evaluable(self) -> None:
        result = compute_h1_max_iops({}, _headline(), _T)
        self.assertEqual(result.band, Band.NOT_EVALUABLE, "Missing baseline field → NOT_EVALUABLE")
        self.assertIsNotNone(result.reason)

    def test_non_numeric_field_not_evaluable(self) -> None:
        result = compute_h1_max_iops(_headline(maximum_iops="n/a"), _headline(), _T)
        self.assertEqual(result.band, Band.NOT_EVALUABLE, "Non-numeric field → NOT_EVALUABLE")


# ---------------------------------------------------------------------------
# H2: maximum_bandwidth — fires when drops
# ---------------------------------------------------------------------------


class TestH2MaxBandwidth(unittest.TestCase):
    def test_strong_drop(self) -> None:
        result = compute_h2_max_bandwidth(
            _headline(maximum_bandwidth="500000"),
            _headline(maximum_bandwidth="350000"),
            _T,
        )
        self.assertEqual(result.band, Band.STRONG)
        self.assertEqual(result.tier, FlagTier.H)

    def test_weak_drop(self) -> None:
        result = compute_h2_max_bandwidth(
            _headline(maximum_bandwidth="500000"),
            _headline(maximum_bandwidth="430000"),
            _T,
        )
        self.assertEqual(result.band, Band.WEAK)

    def test_improvement_is_valid(self) -> None:
        result = compute_h2_max_bandwidth(
            _headline(maximum_bandwidth="500000"),
            _headline(maximum_bandwidth="700000"),
            _T,
        )
        self.assertEqual(result.band, Band.VALID)

    def test_zero_baseline_nonzero_candidate_valid(self) -> None:
        result = compute_h2_max_bandwidth(_headline(maximum_bandwidth="0"), _headline(maximum_bandwidth="500000"), _T)
        self.assertEqual(
            result.band,
            Band.VALID,
            "Drop-fires flag: zero→positive is improvement → VALID",
        )

    def test_nonzero_baseline_zero_candidate_strong(self) -> None:
        result = compute_h2_max_bandwidth(_headline(maximum_bandwidth="500000"), _headline(maximum_bandwidth="0"), _T)
        self.assertEqual(result.band, Band.STRONG, "Bandwidth dropped to zero → STRONG")

    def test_missing_field_not_evaluable(self) -> None:
        result = compute_h2_max_bandwidth({}, _headline(), _T)
        self.assertEqual(result.band, Band.NOT_EVALUABLE)


# ---------------------------------------------------------------------------
# M1: maximum_cpu_usage — fires when rises
# ---------------------------------------------------------------------------


class TestM1MaxCpu(unittest.TestCase):
    def test_strong_rise(self) -> None:
        result = compute_m1_max_cpu(_headline(maximum_cpu_usage="0.50"), _headline(maximum_cpu_usage="0.65"), _T)
        self.assertEqual(result.band, Band.STRONG, "30% CPU rise should be STRONG")
        self.assertEqual(result.tier, FlagTier.M)

    def test_weak_rise(self) -> None:
        result = compute_m1_max_cpu(
            _headline(maximum_cpu_usage="0.50"),
            _headline(maximum_cpu_usage="0.575"),
            _T,
        )
        self.assertEqual(result.band, Band.WEAK, "15% rise should be WEAK")

    def test_improvement_is_valid(self) -> None:
        result = compute_m1_max_cpu(_headline(maximum_cpu_usage="0.50"), _headline(maximum_cpu_usage="0.30"), _T)
        self.assertEqual(result.band, Band.VALID, "CPU drop is an improvement → VALID")

    def test_zero_baseline_nonzero_candidate_strong(self) -> None:
        # rise-fires flag: zero baseline, positive candidate → STRONG
        result = compute_m1_max_cpu(_headline(maximum_cpu_usage="0"), _headline(maximum_cpu_usage="0.50"), _T)
        self.assertEqual(
            result.band,
            Band.STRONG,
            "Zero baseline, positive candidate → STRONG for rise-fires flag",
        )

    def test_zero_baseline_zero_candidate_valid(self) -> None:
        result = compute_m1_max_cpu(_headline(maximum_cpu_usage="0"), _headline(maximum_cpu_usage="0"), _T)
        self.assertEqual(result.band, Band.VALID, "Both zero → VALID")

    def test_missing_field_not_evaluable(self) -> None:
        result = compute_m1_max_cpu({}, _headline(), _T)
        self.assertEqual(result.band, Band.NOT_EVALUABLE)


# ---------------------------------------------------------------------------
# M2: maximum_memory_usage — fires when rises
# ---------------------------------------------------------------------------


class TestM2MaxMemory(unittest.TestCase):
    def test_strong_rise(self) -> None:
        result = compute_m2_max_memory(
            _headline(maximum_memory_usage="1024"),
            _headline(maximum_memory_usage="1300"),
            _T,
        )
        self.assertEqual(result.band, Band.STRONG)
        self.assertEqual(result.tier, FlagTier.M)

    def test_improvement_is_valid(self) -> None:
        result = compute_m2_max_memory(
            _headline(maximum_memory_usage="1024"),
            _headline(maximum_memory_usage="900"),
            _T,
        )
        self.assertEqual(result.band, Band.VALID)

    def test_zero_baseline_nonzero_candidate_strong(self) -> None:
        result = compute_m2_max_memory(
            _headline(maximum_memory_usage="0"),
            _headline(maximum_memory_usage="1024"),
            _T,
        )
        self.assertEqual(
            result.band,
            Band.STRONG,
            "Zero baseline, positive candidate → STRONG for rise-fires flag",
        )

    def test_missing_field_not_evaluable(self) -> None:
        result = compute_m2_max_memory({}, _headline(), _T)
        self.assertEqual(result.band, Band.NOT_EVALUABLE)


# ---------------------------------------------------------------------------
# Iodepth group tests (numeric ordering, group sizes, worst-depth)
# ---------------------------------------------------------------------------


class TestIodepthGroups(unittest.TestCase):
    def test_numeric_ordering_not_lexicographic(self) -> None:
        """
        '128' sorts after '16' numerically but before it lexicographically.
        With 8 depths the high group is ['64','128'], so a rise at '128' fires M3.
        """
        base = _depths(
            [
                ("1", "1000"),
                ("2", "2000"),
                ("4", "3000"),
                ("8", "4000"),
                ("16", "5000"),
                ("32", "6000"),
                ("64", "7000"),
                ("128", "8000"),
            ]
        )
        cand = _depths(
            [
                ("1", "1000"),
                ("2", "2000"),
                ("4", "3000"),
                ("8", "4000"),
                ("16", "5000"),
                ("32", "6000"),
                ("64", "7000"),
                ("128", "12000"),
            ]
        )  # +50% at 128
        result = compute_m3_high_depth_latency(base, cand, _T)
        self.assertNotEqual(result.band, Band.NOT_EVALUABLE)
        self.assertEqual(result.iodepth, "128", "Worst high-depth must be '128' (numeric sort)")

    def test_group_size_10_depths(self) -> None:
        """10 depths → group=3; low=[1,2,4], high=[96,128,256]."""
        base = _depths(
            [
                ("1", "1000"),
                ("2", "1100"),
                ("4", "1200"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
                ("48", "5000"),
                ("96", "6000"),
                ("128", "7000"),
                ("256", "8000"),
            ]
        )
        cand = _depths(
            [
                ("1", "1000"),
                ("2", "1100"),
                ("4", "2400"),
                ("8", "2000"),
                ("16", "3000"),  # +100% at 4
                ("32", "4000"),
                ("48", "5000"),
                ("96", "6000"),
                ("128", "7000"),
                ("256", "8000"),
            ]
        )
        result = compute_h3_low_depth_latency(base, cand, _T)
        self.assertEqual(result.band, Band.STRONG, "100% rise in low group → STRONG")
        self.assertEqual(result.iodepth, "4", "Worst low-depth should be '4'")

    def test_group_size_7_depths(self) -> None:
        """7 depths → group=2; low=[1,2], high=[32,64]."""
        base = _depths(
            [
                ("1", "1000"),
                ("2", "1000"),
                ("4", "1000"),
                ("8", "1000"),
                ("16", "1000"),
                ("32", "1000"),
                ("64", "1000"),
            ]
        )
        cand = _depths(
            [
                ("1", "1000"),
                ("2", "1000"),
                ("4", "1000"),
                ("8", "1000"),
                ("16", "1000"),
                ("32", "1000"),
                ("64", "1250"),
            ]
        )  # +25% at 64
        result = compute_m3_high_depth_latency(base, cand, _T)
        self.assertEqual(result.band, Band.STRONG)
        self.assertEqual(result.iodepth, "64")

    def test_sweep_starting_at_4(self) -> None:
        """Sweep starting at 4 (not 1): 6 depths → group=2; low=[4,8], high=[64,128]."""
        base = _depths(
            [
                ("4", "1000"),
                ("8", "1200"),
                ("16", "1500"),
                ("32", "2000"),
                ("64", "3000"),
                ("128", "4000"),
            ]
        )
        cand = _depths(
            [
                ("4", "1400"),
                ("8", "1200"),
                ("16", "1500"),
                ("32", "2000"),
                ("64", "3000"),
                ("128", "4000"),
            ]
        )  # +40% at 4
        result = compute_h3_low_depth_latency(base, cand, _T)
        self.assertEqual(result.band, Band.STRONG)
        self.assertEqual(result.iodepth, "4")

    def test_worst_depth_selected_from_group(self) -> None:
        """Low group [1,2,4]; biggest rise at depth '2' → reported as iodepth '2'."""
        base = _depths(
            [
                ("1", "1000"),
                ("2", "1000"),
                ("4", "1000"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
                ("48", "5000"),
                ("96", "6000"),
                ("128", "7000"),
                ("256", "8000"),
            ]
        )
        cand = _depths(
            [
                ("1", "1050"),
                ("2", "1500"),
                ("4", "1100"),
                ("8", "2000"),
                ("16", "3000"),  # biggest at 2
                ("32", "4000"),
                ("48", "5000"),
                ("96", "6000"),
                ("128", "7000"),
                ("256", "8000"),
            ]
        )
        result = compute_h3_low_depth_latency(base, cand, _T)
        self.assertEqual(result.band, Band.STRONG)
        self.assertEqual(result.iodepth, "2")

    def test_fewer_than_3_common_depths_not_evaluable(self) -> None:
        """H3 and M3 require ≥3 common depths; fewer → NOT_EVALUABLE."""
        b = _depths([("1", "1000"), ("2", "2000")])
        c = _depths([("1", "5000"), ("2", "10000")])
        self.assertEqual(compute_h3_low_depth_latency(b, c, _T).band, Band.NOT_EVALUABLE)
        self.assertEqual(compute_m3_high_depth_latency(b, c, _T).band, Band.NOT_EVALUABLE)

    def test_zero_common_depths_not_evaluable(self) -> None:
        b = _depths([("1", "1000"), ("2", "2000")])
        c = _depths([("4", "1000"), ("8", "2000")])
        self.assertEqual(compute_h3_low_depth_latency(b, c, _T).band, Band.NOT_EVALUABLE)

    def test_one_common_depth_not_evaluable(self) -> None:
        b = _depths([("1", "1000"), ("2", "2000"), ("4", "3000")])
        c = _depths([("4", "6000"), ("8", "2000"), ("16", "3000")])  # only "4" in common
        self.assertEqual(compute_h3_low_depth_latency(b, c, _T).band, Band.NOT_EVALUABLE)


# ---------------------------------------------------------------------------
# H3: low-depth latency — fires on rise
# ---------------------------------------------------------------------------


class TestH3LowDepthLatency(unittest.TestCase):
    def test_strong_rise_in_low_group(self) -> None:
        base = _depths(
            [
                ("1", "1000"),
                ("2", "1000"),
                ("4", "1000"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
            ]
        )
        cand = _depths(
            [
                ("1", "1400"),
                ("2", "1400"),
                ("4", "1400"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
            ]
        )  # +40% rise
        result = compute_h3_low_depth_latency(base, cand, _T)
        self.assertEqual(result.band, Band.STRONG, "40% rise in low group should be STRONG (>= 35%)")
        self.assertEqual(result.tier, FlagTier.H)
        self.assertIsNotNone(result.iodepth)

    def test_weak_rise_in_low_group(self) -> None:
        base = _depths(
            [
                ("1", "1000"),
                ("2", "1000"),
                ("4", "1000"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
            ]
        )
        cand = _depths(
            [
                ("1", "1300"),
                ("2", "1300"),
                ("4", "1300"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
            ]
        )  # +30% rise
        result = compute_h3_low_depth_latency(base, cand, _T)
        self.assertEqual(result.band, Band.WEAK, "30% rise in low group should be WEAK (>= 25% and < 35%)")

    def test_small_rise_in_low_group_is_valid(self) -> None:
        base = _depths(
            [
                ("1", "1000"),
                ("2", "1000"),
                ("4", "1000"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
            ]
        )
        cand = _depths(
            [
                ("1", "1200"),
                ("2", "1200"),
                ("4", "1200"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
            ]
        )  # +20% rise
        result = compute_h3_low_depth_latency(base, cand, _T)
        self.assertEqual(result.band, Band.VALID, "20% rise in low group should be VALID (< 25%)")

    def test_exact_weak_boundary(self) -> None:
        base = _depths(
            [
                ("1", "1000"),
                ("2", "1000"),
                ("4", "1000"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
            ]
        )
        cand = _depths(
            [
                ("1", "1250"),
                ("2", "1250"),
                ("4", "1250"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
            ]
        )  # +25% rise
        result = compute_h3_low_depth_latency(base, cand, _T)
        self.assertEqual(result.band, Band.WEAK, "Exactly +25% rise should be WEAK")

    def test_exact_strong_boundary(self) -> None:
        base = _depths(
            [
                ("1", "1000"),
                ("2", "1000"),
                ("4", "1000"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
            ]
        )
        cand = _depths(
            [
                ("1", "1350"),
                ("2", "1350"),
                ("4", "1350"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
            ]
        )  # +35% rise
        result = compute_h3_low_depth_latency(base, cand, _T)
        self.assertEqual(result.band, Band.STRONG, "Exactly +35% rise should be STRONG")

    def test_general_thresholds_do_not_override_h3_fixed_thresholds(self) -> None:
        # Even with strict thresholds (e.g. weak=0.05, strong=0.10),
        # H3 maintains its overriding 25% weak and 35% strong thresholds.
        strict_t = Thresholds(weak=0.05, strong=0.10)
        base = _depths(
            [
                ("1", "1000"),
                ("2", "1000"),
                ("4", "1000"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
            ]
        )
        cand_20 = _depths(
            [
                ("1", "1200"),
                ("2", "1200"),
                ("4", "1200"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
            ]
        )  # +20% rise -> VALID for H3 despite strict_t having weak=0.05
        result = compute_h3_low_depth_latency(base, cand_20, strict_t)
        self.assertEqual(result.band, Band.VALID, "+20% rise must be VALID for H3 even with strict general thresholds")

    def test_high_depth_rise_does_not_fire_h3(self) -> None:
        base = _depths(
            [
                ("1", "1000"),
                ("2", "1000"),
                ("4", "1000"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
            ]
        )
        cand = _depths(
            [
                ("1", "1000"),
                ("2", "1000"),
                ("4", "1000"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "8000"),
            ]
        )
        result = compute_h3_low_depth_latency(base, cand, _T)
        self.assertEqual(result.band, Band.VALID, "High-depth rise must not fire H3")

    def test_improvement_in_low_group_is_valid(self) -> None:
        base = _depths(
            [
                ("1", "5000"),
                ("2", "5000"),
                ("4", "5000"),
                ("8", "8000"),
                ("16", "9000"),
                ("32", "10000"),
            ]
        )
        cand = _depths(
            [
                ("1", "3000"),
                ("2", "3000"),
                ("4", "3000"),
                ("8", "8000"),
                ("16", "9000"),
                ("32", "10000"),
            ]
        )
        result = compute_h3_low_depth_latency(base, cand, _T)
        self.assertEqual(result.band, Band.VALID)

    def test_zero_baseline_latency_nonzero_candidate_strong(self) -> None:
        """Rise-fires flag: zero baseline latency, nonzero candidate → STRONG."""
        base = _depths(
            [
                ("1", "0"),
                ("2", "0"),
                ("4", "0"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
            ]
        )
        cand = _depths(
            [
                ("1", "1000"),
                ("2", "1000"),
                ("4", "1000"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
            ]
        )
        result = compute_h3_low_depth_latency(base, cand, _T)
        self.assertEqual(result.band, Band.STRONG)


# ---------------------------------------------------------------------------
# M3: high-depth latency — fires on rise
# ---------------------------------------------------------------------------


class TestM3HighDepthLatency(unittest.TestCase):
    def test_strong_rise_in_high_group(self) -> None:
        base = _depths(
            [
                ("1", "1000"),
                ("2", "1000"),
                ("4", "1000"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
            ]
        )
        cand = _depths(
            [
                ("1", "1000"),
                ("2", "1000"),
                ("4", "1000"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "5000"),
            ]
        )
        result = compute_m3_high_depth_latency(base, cand, _T)
        self.assertEqual(result.band, Band.STRONG)
        self.assertEqual(result.tier, FlagTier.M)
        self.assertIsNotNone(result.iodepth)

    def test_low_depth_rise_does_not_fire_m3(self) -> None:
        base = _depths(
            [
                ("1", "1000"),
                ("2", "1000"),
                ("4", "1000"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
            ]
        )
        cand = _depths(
            [
                ("1", "5000"),
                ("2", "5000"),
                ("4", "5000"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
            ]
        )
        result = compute_m3_high_depth_latency(base, cand, _T)
        self.assertEqual(result.band, Band.VALID, "Low-depth rise must not fire M3")

    def test_improvement_in_high_group_is_valid(self) -> None:
        base = _depths(
            [
                ("1", "1000"),
                ("2", "1000"),
                ("4", "1000"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "4000"),
            ]
        )
        cand = _depths(
            [
                ("1", "1000"),
                ("2", "1000"),
                ("4", "1000"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "2000"),
            ]
        )
        result = compute_m3_high_depth_latency(base, cand, _T)
        self.assertEqual(result.band, Band.VALID)

    def test_zero_baseline_latency_nonzero_candidate_strong(self) -> None:
        """Rise-fires flag: zero baseline in high group, nonzero candidate → STRONG."""
        base = _depths(
            [
                ("1", "1000"),
                ("2", "1000"),
                ("4", "1000"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "0"),
            ]
        )
        cand = _depths(
            [
                ("1", "1000"),
                ("2", "1000"),
                ("4", "1000"),
                ("8", "2000"),
                ("16", "3000"),
                ("32", "5000"),
            ]
        )
        result = compute_m3_high_depth_latency(base, cand, _T)
        self.assertEqual(result.band, Band.STRONG)


if __name__ == "__main__":
    unittest.main()
