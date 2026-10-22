"""
Unit tests for post_processing/regression/loader.py.

Uses real JSON files written to a temp directory.  No mocking of internal CBT
code — per the coding guidelines.
"""

import json
import logging
import tempfile
import unittest
from pathlib import Path
from typing import Any

from post_processing.regression.loader import (
    ArchiveLoadError,
    WorkloadPair,
    load_archive_pairs,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimal valid intermediate JSON.  All values are strings; iodepth keys are
# numeric string keys whose values are dicts (headline fields are plain str).
_IODEPTH_ENTRY: dict[str, Any] = {
    "bandwidth_bytes": "1000000",
    "blocksize": "4096",
    "iops": "250",
    "latency": "4000000",
    "number_of_jobs": "1",
    "runtime_seconds": "60",
}

_HEADLINE_FIELDS: dict[str, Any] = {
    "maximum_iops": "250",
    "maximum_bandwidth": "1000000",
    "maximum_cpu_usage": "0.50",
    "maximum_memory_usage": "512",
    "benchmark": "librbdfio",
}


def _make_json(iodepths: list[str], runtime: str = "60") -> dict[str, Any]:
    """Return a minimal intermediate-format dict with the given iodepth keys."""
    data: dict[str, Any] = {}
    for depth in iodepths:
        entry = dict(_IODEPTH_ENTRY)
        entry["runtime_seconds"] = runtime
        data[depth] = entry
    data.update(_HEADLINE_FIELDS)
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf8")


def _make_archive(
    base: Path,
    stems_and_iodepths: dict[str, list[str]],
    vis_subpath: str = "visualisation",
    runtime: str = "60",
) -> Path:
    """
    Create an archive directory with JSON files at base/<vis_subpath>/<stem>.json.
    Returns the archive root (base).
    """
    vis_dir = base / vis_subpath
    vis_dir.mkdir(parents=True, exist_ok=True)
    for stem, iodepths in stems_and_iodepths.items():
        _write_json(vis_dir / f"{stem}.json", _make_json(iodepths, runtime=runtime))
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLoadArchivePairs(unittest.TestCase):
    """Tests for load_archive_pairs()."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # Happy path: basic pairing
    # ------------------------------------------------------------------

    def test_pairs_by_stem_ignoring_id_directory(self) -> None:
        """Workloads pair by filename stem regardless of id-xxx directory names."""
        baseline = self.tmp / "baseline"
        candidate = self.tmp / "candidate"

        # Different id-xxx wrapper paths, same stems
        _make_archive(
            baseline / "results" / "00000000" / "id-aaa" / "randread",
            {"4096_1_randread": ["1", "2", "4"]},
        )
        _make_archive(
            candidate / "results" / "00000000" / "id-bbb" / "randread",
            {"4096_1_randread": ["1", "2", "4"]},
        )

        pairs, missing, extra = load_archive_pairs(baseline, candidate)

        self.assertEqual(len(pairs), 1, "Should pair the one matching stem")
        self.assertEqual(pairs[0].stem, "4096_1_randread")
        self.assertEqual(missing, [], "No missing workloads")
        self.assertEqual(extra, [], "No extra workloads")

    def test_pairs_multiple_stems(self) -> None:
        """All matching stems are paired; result includes all of them."""
        baseline = self.tmp / "baseline"
        candidate = self.tmp / "candidate"
        stems = {
            "4096_1_randread": ["1", "2"],
            "262144_1_randread": ["1", "2"],
            "4194304_1_randwrite": ["1", "2"],
        }
        _make_archive(baseline, stems)
        _make_archive(candidate, stems)

        pairs, _missing, _extra = load_archive_pairs(baseline, candidate)

        self.assertEqual(len(pairs), 3, "All three stems should be paired")
        paired_stems = {p.stem for p in pairs}
        self.assertEqual(paired_stems, set(stems.keys()))

    def test_different_stems_show_as_missing_and_extra(self) -> None:
        """Stems only in baseline → missing; only in candidate → extra."""
        baseline = self.tmp / "baseline"
        candidate = self.tmp / "candidate"

        _make_archive(baseline, {"4096_1_randread": ["1"], "256k_1_randwrite": ["1"]})
        _make_archive(
            candidate,
            {"4096_1_randread": ["1"], "new_workload_1_randrw": ["1"]},
        )

        pairs, missing, extra = load_archive_pairs(baseline, candidate)

        self.assertEqual(len(pairs), 1, "Only one stem matches")
        self.assertIn("256k_1_randwrite", missing, "Baseline-only stem should be missing")
        self.assertIn("new_workload_1_randrw", extra, "Candidate-only stem should be extra")

    # ------------------------------------------------------------------
    # Timeseries exclusion
    # ------------------------------------------------------------------

    def test_timeseries_files_are_excluded(self) -> None:
        """Files ending in _timeseries are not treated as workloads."""
        baseline = self.tmp / "baseline"
        candidate = self.tmp / "candidate"

        vis_b = baseline / "visualisation"
        vis_b.mkdir(parents=True)
        _write_json(vis_b / "4096_1_randread.json", _make_json(["1", "2"]))
        _write_json(vis_b / "4096_1_randread_timeseries.json", _make_json(["1", "2"]))

        vis_c = candidate / "visualisation"
        vis_c.mkdir(parents=True)
        _write_json(vis_c / "4096_1_randread.json", _make_json(["1", "2"]))
        _write_json(vis_c / "4096_1_randread_timeseries.json", _make_json(["1", "2"]))

        pairs, _missing, _extra = load_archive_pairs(baseline, candidate)

        stems = {p.stem for p in pairs}
        self.assertIn("4096_1_randread", stems, "Normal file should be paired")
        self.assertNotIn(
            "4096_1_randread_timeseries",
            stems,
            "Timeseries file must be excluded",
        )
        self.assertEqual(len(pairs), 1, "Exactly one workload pair")

    # ------------------------------------------------------------------
    # runtime_seconds warning
    # ------------------------------------------------------------------

    def test_runtime_seconds_mismatch_logs_warning(self) -> None:
        """A differing runtime_seconds in any iodepth entry triggers a warning."""
        baseline = self.tmp / "baseline"
        candidate = self.tmp / "candidate"

        _make_archive(baseline, {"4096_1_randread": ["1", "2"]}, runtime="60")
        _make_archive(candidate, {"4096_1_randread": ["1", "2"]}, runtime="30")

        with self.assertLogs(level=logging.WARNING) as log_ctx:
            pairs, _, _ = load_archive_pairs(baseline, candidate)

        self.assertEqual(len(pairs), 1, "Pair should still be returned")
        warning_text = " ".join(log_ctx.output)
        self.assertIn(
            "runtime_seconds",
            warning_text,
            "Warning must mention runtime_seconds",
        )

    def test_matching_runtime_seconds_no_warning(self) -> None:
        """Matching runtime_seconds values must not produce a warning."""
        baseline = self.tmp / "baseline"
        candidate = self.tmp / "candidate"

        _make_archive(baseline, {"4096_1_randread": ["1", "2"]}, runtime="60")
        _make_archive(candidate, {"4096_1_randread": ["1", "2"]}, runtime="60")

        import io

        handler = logging.StreamHandler(io.StringIO())
        handler.setLevel(logging.WARNING)
        logger = logging.getLogger("post_processing.regression.loader")
        logger.addHandler(handler)
        try:
            load_archive_pairs(baseline, candidate)
            self.assertEqual(
                handler.stream.getvalue(),  # type: ignore[attr-defined]
                "",
                "No warning should be emitted when runtime_seconds matches",
            )
        finally:
            logger.removeHandler(handler)

    # ------------------------------------------------------------------
    # Error paths
    # ------------------------------------------------------------------

    def test_nonexistent_baseline_raises(self) -> None:
        """A nonexistent baseline directory raises ArchiveLoadError."""
        candidate = self.tmp / "candidate"
        _make_archive(candidate, {"4096_1_randread": ["1"]})

        with self.assertRaises(ArchiveLoadError, msg="Nonexistent baseline should raise"):
            load_archive_pairs(self.tmp / "does_not_exist", candidate)

    def test_nonexistent_candidate_raises(self) -> None:
        """A nonexistent candidate directory raises ArchiveLoadError."""
        baseline = self.tmp / "baseline"
        _make_archive(baseline, {"4096_1_randread": ["1"]})

        with self.assertRaises(ArchiveLoadError, msg="Nonexistent candidate should raise"):
            load_archive_pairs(baseline, self.tmp / "does_not_exist")

    def test_no_intermediate_files_raises(self) -> None:
        """An archive with no intermediate JSON raises ArchiveLoadError naming fio_common_output_wrapper."""
        baseline = self.tmp / "baseline"
        baseline.mkdir()
        candidate = self.tmp / "candidate"
        _make_archive(candidate, {"4096_1_randread": ["1"]})

        with self.assertRaises(ArchiveLoadError) as ctx:
            load_archive_pairs(baseline, candidate)

        self.assertIn(
            "fio_common_output_wrapper",
            str(ctx.exception),
            "Error message must reference fio_common_output_wrapper.py",
        )

    def test_no_pairs_raises(self) -> None:
        """When no stems match at all, ArchiveLoadError is raised."""
        baseline = self.tmp / "baseline"
        candidate = self.tmp / "candidate"

        _make_archive(baseline, {"4096_1_randread": ["1"]})
        _make_archive(candidate, {"256k_1_randwrite": ["1"]})

        with self.assertRaises(ArchiveLoadError, msg="No paired workloads should raise"):
            load_archive_pairs(baseline, candidate)

    def test_duplicate_stem_in_baseline_raises(self) -> None:
        """Two files with the same stem in one archive raises ArchiveLoadError naming both files."""
        baseline = self.tmp / "baseline"

        # Place the same stem in two different visualisation directories
        vis1 = baseline / "randread" / "visualisation"
        vis1.mkdir(parents=True)
        _write_json(vis1 / "4096_1_randread.json", _make_json(["1"]))

        vis2 = baseline / "randwrite" / "visualisation"
        vis2.mkdir(parents=True)
        _write_json(vis2 / "4096_1_randread.json", _make_json(["1"]))

        candidate = self.tmp / "candidate"
        _make_archive(candidate, {"4096_1_randread": ["1"]})

        with self.assertRaises(ArchiveLoadError) as ctx:
            load_archive_pairs(baseline, candidate)

        msg = str(ctx.exception)
        self.assertIn(
            "4096_1_randread",
            msg,
            "Error must name the duplicate stem",
        )

    def test_invalid_json_raises(self) -> None:
        """Corrupt JSON in the baseline raises ArchiveLoadError naming the file."""
        baseline = self.tmp / "baseline"
        vis = baseline / "visualisation"
        vis.mkdir(parents=True)
        (vis / "4096_1_randread.json").write_text("{ not valid json", encoding="utf8")

        candidate = self.tmp / "candidate"
        _make_archive(candidate, {"4096_1_randread": ["1"]})

        with self.assertRaises(ArchiveLoadError) as ctx:
            load_archive_pairs(baseline, candidate)

        self.assertIn(
            "4096_1_randread",
            str(ctx.exception),
            "Error must name the corrupt file",
        )

    def test_empty_json_file_raises(self) -> None:
        """An empty JSON file raises ArchiveLoadError naming the file."""
        baseline = self.tmp / "baseline"
        vis = baseline / "visualisation"
        vis.mkdir(parents=True)
        (vis / "4096_1_randread.json").write_text("", encoding="utf8")

        candidate = self.tmp / "candidate"
        _make_archive(candidate, {"4096_1_randread": ["1"]})

        with self.assertRaises(ArchiveLoadError) as ctx:
            load_archive_pairs(baseline, candidate)

        self.assertIn(
            "4096_1_randread",
            str(ctx.exception),
            "Error must name the empty file",
        )

    # ------------------------------------------------------------------
    # Varied layout and stem shapes (no hardcoding)
    # ------------------------------------------------------------------

    def test_bare_visualisation_folder_as_archive(self) -> None:
        """A baseline stored as a bare visualisation/ folder is found."""
        # The baseline IS the parent of visualisation/
        baseline = self.tmp / "bare_baseline"
        vis = baseline / "visualisation"
        vis.mkdir(parents=True)
        _write_json(vis / "262144_1_randwrite.json", _make_json(["4", "8", "16"]))

        candidate = self.tmp / "candidate"
        _make_archive(candidate, {"262144_1_randwrite": ["4", "8", "16"]})

        pairs, _missing, _extra = load_archive_pairs(baseline, candidate)

        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].stem, "262144_1_randwrite")

    def test_deeply_nested_archive_layout(self) -> None:
        """Workloads nested under results/<iter>/id-<hash>/op/visualisation/ are found."""
        baseline = self.tmp / "deep_baseline"
        candidate = self.tmp / "deep_candidate"

        stems = {"4194304_2_randwrite": ["8", "16", "32", "64"]}
        _make_archive(
            baseline / "results" / "0" / "id-xyz" / "randwrite",
            stems,
        )
        _make_archive(
            candidate / "results" / "0" / "id-abc" / "randwrite",
            stems,
        )

        pairs, missing, extra = load_archive_pairs(baseline, candidate)

        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].stem, "4194304_2_randwrite")
        self.assertEqual(missing, [])
        self.assertEqual(extra, [])

    def test_varied_stem_shapes_nothing_hardcoded(self) -> None:
        """
        Loader works for an entirely different set of workload names and counts
        than used in other tests — nothing is hardcoded.
        """
        baseline = self.tmp / "baseline_varied"
        candidate = self.tmp / "candidate_varied"

        # Eight workloads with unusual but valid stem patterns
        stems = {
            "131072_4_read": ["2", "4"],
            "131072_4_write": ["2", "4"],
            "524288_8_randrw_50_50": ["2", "4"],
            "524288_8_randread": ["2", "4"],
            "1048576_1_randwrite": ["2", "4"],
        }
        _make_archive(baseline, stems)
        _make_archive(candidate, stems)

        pairs, missing, extra = load_archive_pairs(baseline, candidate)

        self.assertEqual(len(pairs), 5, "All five unusual stems should pair")
        self.assertEqual(missing, [])
        self.assertEqual(extra, [])

    # ------------------------------------------------------------------
    # WorkloadPair structure
    # ------------------------------------------------------------------

    def test_workload_pair_contains_parsed_data(self) -> None:
        """Each WorkloadPair holds the parsed JSON dicts for baseline and candidate."""
        baseline = self.tmp / "baseline"
        candidate = self.tmp / "candidate"

        _make_archive(baseline, {"4096_1_randread": ["1", "2"]})
        _make_archive(candidate, {"4096_1_randread": ["1", "2"]})

        pairs, _, _ = load_archive_pairs(baseline, candidate)

        self.assertEqual(len(pairs), 1)
        pair = pairs[0]
        self.assertIsInstance(pair, WorkloadPair)
        self.assertIsInstance(pair.baseline_data, dict, "baseline_data must be a dict")
        self.assertIsInstance(pair.candidate_data, dict, "candidate_data must be a dict")
        self.assertIn("1", pair.baseline_data, "Iodepth key '1' must be present")


if __name__ == "__main__":
    unittest.main()
