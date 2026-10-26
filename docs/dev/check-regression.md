# CBT Regression Detection Tool

`tools/check_regression.py` compares two CBT benchmark archive directories (a baseline and a candidate) and reports whether performance regressions occurred.

---

## Prerequisites

- **Intermediate JSON files:** The tool expects intermediate JSON files in a `visualisation/` directory within each archive.
  - In teuthology runs with a `report:` section in the CBT YAML (e.g., `results_file_root: "output"`), these files are generated automatically.
  - For archives without pre-existing intermediate files, generate them first using `tools/fio_common_output_wrapper.py`.
- **Matching Workloads:** Baseline and candidate runs should use the same or comparable workload configurations.
- *Note:* Automatic generation of missing intermediate files may be added in a post-MVP update.

---

## Usage

```bash
PYTHONPATH=. python3 tools/check_regression.py \
  --baseline <path/to/baseline_archive> \
  --candidate <path/to/candidate_archive> \
  [--weak 0.08] \
  [--strong 0.12] \
  [--no-color]
```

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--baseline` | `Path` (required) | — | Path to the baseline CBT archive directory. |
| `--candidate` | `Path` (required) | — | Path to the candidate CBT archive directory. |
| `--weak` | `float` | `0.08` | Fractional worsening threshold for WEAK flags (default: 0.08 / 8%). Note: H3 low-depth latency uses fixed overriding thresholds (25% weak, 35% strong) due to higher noise floor. |
| `--strong` | `float` | `0.12` | Fractional worsening threshold for STRONG flags (default: 0.12 / 12%). |
| `--no-color` | `bool` | `False` | Disable ANSI terminal color codes in output. |

### Exit Codes

| Exit Code | Decision | Meaning |
|---|---|---|
| `0` | **PASS** | No significant regression detected. |
| `1` | **REVIEW** | Possible regression (or multiple weak signals) requiring human review. |
| `2` | **FAIL** | Clear regression detected exceeding the strong threshold. |
| `3` | **ERROR** | Input problem (e.g. missing directory, unparseable JSON, no matching workloads). |

---

## Example Input Layout and Output

### Input Directory Layout

```
archive_baseline/
└── results/
    └── 0/
        └── id-abc123/
            └── librbdfio/
                └── visualisation/
                    ├── 4096_1_randread.json
                    └── 4096_1_randwrite.json

archive_candidate/
└── results/
    └── 0/
        └── id-xyz789/
            └── librbdfio/
                └── visualisation/
                    ├── 4096_1_randread.json
                    └── 4096_1_randwrite.json
```

### Sample Output

```text
--- Sequential Read ---
  4K read                                  PASS
  1M read                                  REVIEW
    H1  baseline=7,241.5  candidate=6,447.6  change=-11.0%  WEAK
    H2  baseline=7.59 GB/s  candidate=6.76 GB/s  change=-11.0%  WEAK
    M3  baseline=8.83 ms  candidate=9.92 ms  change=+12.3%  STRONG  iodepth=64

--- Sequential Write ---
  32K write                                FAIL
    H1  baseline=125,000.2  candidate=109,599.4  change=-12.3%  STRONG
    H2  baseline=4.10 GB/s  candidate=3.59 GB/s  change=-12.3%  STRONG
    M3  baseline=6.14 ms  candidate=7.00 ms  change=+14.1%  STRONG  iodepth=768

Result: FAIL  workloads=44  fail_target=3  failed=3  review=11  (11 workloads need review)

Flag Key:
  H1: Maximum IOPS (drop)            M1: Maximum CPU Usage (rise)
  H2: Maximum Bandwidth (drop)       M2: Maximum Memory Usage (rise)
  H3: Low-depth Latency (rise)       M3: High-depth Latency (rise)
```
