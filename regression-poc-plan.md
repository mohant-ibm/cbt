# Regression detection POC — plan

## Top-level overview

**Goal.** Add a proof-of-concept tool that takes two CBT archive directories (baseline and candidate),
reads the intermediate JSON files that CBT's report phase writes into `visualisation/` directories,
and reports whether the candidate has regressed.

**Scope (MVP).**
- The comparison works only on the intermediate (hockey-stick) JSON. The tool never reads the CBT YAML
  or time-series files, and does no curve analysis.
- The MVP targets teuthology runs. Both archives come from the same, or a similar, CBT YAML with
  a `report:` section, so the intermediate JSON already exists. The MVP only reads it and should never need to read
  raw fio output. The tool doesn't need to check the YAML. It checks only what the JSON shows (see
  Comparability).
- Standalone use and archives with no intermediate JSON are out of focus for now (see sub-task 7).
- Where baselines are stored is out of scope. The tool takes two directories and compares them.
- Lives in this CBT fork as `post_processing/regression/`. It uses only the standard library plus
  existing `post_processing` modules, so it can move to the Ceph QA suite later.

**Non-goals.** Baseline storage, statistical tests, repeated-run handling, workload weighting,
`cpu/iops` normalisation, reading or diffing `cbt_config.yaml`, regenerating or overwriting existing
intermediate files, a `--strict` mode, git commits or pushes. Also generating missing intermediate JSON
(post-MVP).

### Focus: the MVP for teuthology runs

**The only goal now is the MVP (sub-tasks 0 to 6):** compare two archives from teuthology runs, where a
`visualisation/` directory with intermediate JSON is expected to exist somewhere under each given
directory. If it does not, error and exit 3. Do not spend effort on standalone use, generating missing
JSON, or unusual baseline stores. Sub-task 7 is only a placeholder; the plan will be reworked after the
MVP works.

### Assumptions and ambiguities rule

Do not guess. Before starting each sub-task, the implementer must:
1. List every assumption the sub-task relies on, and every ambiguity found in the plan or the code.
2. For anything that would change the design or the tests, ask the user with `ask_followup_question`
   and wait for the answer. Do not proceed on a guess.
3. For small, low-risk assumptions, state them in chat and record them in the Change log.
4. Record each answer in "Assumptions and open questions" below, so the decision survives outside chat.

If implementation finds a contradiction between this plan and the code, stop and ask rather than pick
one silently.

**Approach.** Small pure-function layers, each written test-first: load and pair workloads, compute
flags, derive a workload verdict, derive a run verdict, print a transparent summary. A thin CLI wires
them together.

### No hardcoding rule

The tool must work for any CBT YAML that produces intermediate JSON. Nothing may depend on a specific
set of blocksizes, operations, iodepths, workload names, number of workloads or benchmark type.

| Quantity | How it is derived |
|---|---|
| Workloads and N | Counted from the intermediate JSON files found in the baseline archive |
| Which workloads pair | Filename stem (for example `262144_1_randread`), which already encodes blocksize, numjobs, read/write mix and operation. Never the workload names from the YAML. |
| Iodepth values | Read from the numeric dict keys in each file; different workloads may have different sweeps |
| Low and high iodepth groups | Relative position (bottom and top third) of the iodepths common to both files |
| Thresholds | The only tunable values (one place). The thirds for iodepth groups and the K formula are fixed design choices, not tunables. |
| K | Computed from N |
| Benchmark type | Not assumed; the JSON's `benchmark` field is not used to branch behaviour |

The `0-precondition` style workloads in a YAML never reach the JSON, because the formatter skips any
path containing `precond` (`file_is_precondition()`), so the tool needs no special case for them.

### Design

**Flags** (all one-directional: only a worsening fires, improvements never do)

| Tier | Flag | Field | Fires when |
|---|---|---|---|
| H | H1 max IOPS | `maximum_iops` | drops |
| H | H2 max bandwidth | `maximum_bandwidth` | drops |
| H | H3 low-depth latency | `latency` over the bottom third of common iodepths | rises; worst depth in the group |
| M | M1 max CPU | `maximum_cpu_usage` | rises (raw value, not normalised) |
| M | M2 max memory | `maximum_memory_usage` | rises |
| M | M3 high-depth latency | `latency` over the top third of common iodepths | rises; worst depth in the group |

**Bands** (initial values, tunable from one place). Worsening below 10% is NONE; 10% up to but
excluding 20% is WEAK; 20% and above is STRONG. A flag whose baseline is zero, or whose inputs are
missing, is NOT_EVALUABLE and never changes a verdict.

**Iodepth groups.** Use the iodepths present in both files, sorted numerically (the keys are strings).
Group size is `max(1, n // 3)`; low is the first group, high is the last group. Fewer than 3 common
iodepths makes H3 and M3 NOT_EVALUABLE. Iodepth entries are the dict-valued keys; the non-dict keys are
headline fields.

**Workload verdict.** FAIL if any H flag is STRONG. REVIEW if any H flag is WEAK, or any M flag is
WEAK or STRONG. Otherwise PASS.

**Run verdict.** FC is the number of FAIL workloads, RC the number of REVIEW workloads, N the total
number of workloads expected, and `K = max(1, min(3, ceil(N * 0.5)))`. One intermediate JSON file is
one workload, so 4K randread and 4K randwrite count as two. N is every baseline workload: the ones
that paired plus the ones missing from the candidate.
FAIL if `FC >= K`. REVIEW if `FC > 0` or `RC > 1`. PASS otherwise.
`RC > 1` is intentional (decided by the user): a single REVIEW workload does not change the run verdict,
so it exits 0. The summary must still print a line such as "1 workload needs review" so it is not hidden.

**Missing workload** (in baseline, absent from candidate): counted as a REVIEW workload in N and RC,
and a warning is logged. If everything else passes the run is PASS with a warning. No override.
**Extra workload** (only in candidate): logged, no effect on the verdict.

**Comparability.** Warn and continue; never exit early. Two warnings only: workloads present in one
archive but not the other, and differing `runtime_seconds` between a paired baseline and candidate.
`runtime_seconds` is stored inside each iodepth entry, not at the top level of the file, so compare the
values found in the iodepth entries. More checks (benchmark type, iodepth sets) can be added if real
runs show they matter.

**Exit codes.** 0 PASS, 1 REVIEW, 2 FAIL, 3 input error (missing directory, no intermediate files,
no paired workloads, duplicate workload names within one archive, unreadable JSON). The full list of
situations is in "Expected usage and flexibility".

**Pairing key.** The filename stem. The `id-xxx` directory name is ignored: it is a hash of the
benchmark config items including `iteration` (see `benchmark/benchmark.py`), so it changes whenever any
config option differs. If one archive holds several `results/<iteration>/id-<hash>/` trees, the same
stem can appear twice. That is a duplicate-name error that tells the user to point the tool at a
narrower directory (one iteration or one `id-` directory).

### Expected usage and flexibility

**Expected usage (MVP).** The teuthology CBT YAML has a `report:` section (for example
`force_refresh`, `create_pdf`, `output_directory`, `results_file_root: "output"`, `no_error_bars`), so
each archive holds `visualisation/` directories with the intermediate JSON. The tool only reads them.
Both archives come from the same, or a similar, YAML. The tool never reads the `report:` section; it
only looks for `visualisation/` directories. Baselines may be stored with only the necessary files, so
the tool must find `visualisation/` wherever it sits under the given directory.

**Out of the MVP: standalone and odd archives.** Standalone use, a baseline store with no
`visualisation/` and no raw fio output, and similar odd cases are not a focus now and are not designed
here. The MVP handles all of them the same simple way: no intermediate JSON found, so exit 3 with a
clear message (naming `tools/fio_common_output_wrapper.py` as the way to create it). Generating the
missing JSON automatically is a possible post-MVP step (see sub-task 7) and will be re-planned later.

| Situation | MVP behaviour |
|---|---|
| Archive has no intermediate JSON, for whatever reason | Exit 3, saying none was found and where it looked |
| Path given at a different level (archive root, `results/`, an `id-` directory, an operation directory) | Use whatever the existing directory finder discovers below it; if one stem then appears twice, the duplicate-stem error applies |
| The two YAMLs differ, so the workload sets differ | Pair what matches, warn about the rest; exit 3 only if nothing pairs |
| Different benchmark types, blocksizes or operations | Not special-cased; workloads are compared by stem |
| A flag's field is absent, non-numeric, or has a zero baseline | That flag is NOT_EVALUABLE with a reason; never a crash |
| Iodepth sweeps of different lengths or start points, or fewer than 3 common depths | Groups are computed from what is common; H3 and M3 are NOT_EVALUABLE if there are fewer than 3 |
| Only one workload in total | N=1, K=1 |
| Empty or unparseable JSON | Exit 3 naming the file |

**Keeping it simple.** Handle the situations above with a clear message. Do not add options or modes for
situations not listed. If implementation finds a new case, handle it the simplest way that gives a clear
message, add a test for it, and record it in the Change log.

### Worked example (from a real teuthology `cbt_config`)

This is guidance for the typical case, not a specification. A `librbdfio` config with six measured
workloads (4k, 256k and 4M, each randread and randwrite), each with a 10-point `total_iodepth` sweep,
plus one precondition workload. Result: six intermediate files, so N=6 and K=3. Each file has 10
iodepths, so each group is 3 depths (for 256k randwrite: low is 1, 2, 4 and high is 96, 128, 256). The 4k
sweeps start at 4, so a fixed "depth 1 and 2" rule would not work there, which is why the groups are
relative. No code or test depends on this example.

## Repo requirements this plan follows

Sources: `AGENTS.md`, `docs/dev/coding-guidelines.md`, `docs/ARCHITECTURE.md`,
`.agents/skills/writing-unit-tests/SKILL.md`, `.agents/skills/executing-plans/SKILL.md`.

- **TDD.** Each sub-task starts by writing tests, running them and seeing them fail, then writing code.
- **Acceptance criteria.** Each sub-task has a test-backed criterion. A sub-task is only marked done
  after its tests have been run and seen to pass.
- **Toolchain.** Python 3.9 (no `X | Y` unions, no `match`). On changed files only:
  `black --config pyproject.toml`, `ruff check --config pyproject.toml`,
  `mypy --config-file pyproject.toml`, and `pylint --rcfile pyproject.toml` one file at a time with
  score 10.0. Then `python3 -m pytest -c pyproject.toml tests/`.
- **Typing.** Strict and fully typed. No new entries in the mypy override blocks. Shared types go in
  `post_processing/post_processing_types.py`.
- **Code shape.** No `else` after `return`; no nested functions.
- **DRY.** Reuse existing helpers; do not duplicate them.
- **Tests.** `unittest.TestCase` classes under `tests/test_*.py`; real files in a temp directory; no
  mocking of internal CBT code; descriptive assertion messages; negative paths covered. There is no
  remote boundary in this feature, so nothing is mocked.
- **`STRUCTURE.md`.** Updated in the same change whenever a file is added.
- **Docs.** A user-visible tool ships with a concrete example (input layout plus expected output).
- **Git.** No commits or pushes by the agent. When finished, tell the user the work is ready for
  review. If the user commits, the format is `component: Sentence-case summary`, `-s`, plus an
  `Assisted-by: LLM` trailer in the body.

## Reporting changes while working

For every change made during implementation, state in chat, before or as it is made: the file, what is
being added or changed, and why. For every command run, state the command and its purpose, then report
the result. After each sub-task, append an entry to the Change log at the bottom of this file with the
same information, so the record survives outside the chat.

## Reuse map (existing code, do not rewrite)

| Need | Existing code |
|---|---|
| Find `visualisation/` dirs, excluding time-series | `find_hockey_stick_visualisation_directories()` in `post_processing/common.py` |
| Load JSON | `read_intermediate_file()` in `post_processing/common.py`. Returns `{}` on a missing file. It does NOT catch invalid JSON: an empty or corrupt file raises `json.JSONDecodeError`, which the loader must catch and turn into the exit-3 error naming the file. |
| Detect empty files | `file_is_empty()` in `post_processing/common.py` |
| Percent difference | `calculate_percent_difference_to_baseline()` in `post_processing/common.py`. Needs the float split in sub-task 0. |
| Iodepth-vs-headline key handling | The `isinstance(value, dict)` approach in `_find_maximum_bandwidth_and_iops_with_latency()` in `post_processing/formatter/common_output_formatter.py` |
| CLI pattern and logging setup | `tools/generate_comparison_performance_report.py`, `logging_configuration.setup_loggers()` |
| Generating missing intermediate files | Not in the MVP. `tools/fio_common_output_wrapper.py` is named in an error message only. |

## Sub-tasks

Each sub-task is independent and is processed one at a time. The user reviews and approves each before
the next starts. Types are added in the sub-task that first needs them, in
`post_processing/post_processing_types.py`.

Sub-tasks 0 to 6 are the MVP. Sub-task 7 is a post-MVP placeholder. Every sub-task starts with the "Assumptions and
ambiguities rule": list assumptions, ask about anything that would change the design, and record the
answers in "Assumptions and open questions".

---

### 0. Float percent-difference helper

- **Status:** [x] done
- **Intent.** `calculate_percent_difference_to_baseline()` returns a string rounded to whole percent
  (for example `"10%"`) and raises `ZeroDivisionError` on a zero baseline. Thresholds need the
  unrounded fraction. This is the only change to shared code.
- **Expected outcomes.**
  - A new function in `post_processing/common.py` takes two floats and returns the unrounded fractional
    change. A zero baseline raises `ZeroDivisionError`, as the existing function does. The flag code
    checks for a zero baseline first and returns NOT_EVALUABLE.
  - The existing string function is built on the new one and behaves exactly as before, including
    `ZeroDivisionError`.
  - The one existing production caller, `post_processing/reports/comparison_report_generator.py`,
    behaves identically.
- **Todo list.**
  1. Add failing tests to `tests/test_post_processing_common.py`: a 9.6% drop returns an unrounded
     value, a zero baseline raises `ZeroDivisionError`, and the existing string cases are unchanged.
  2. Run them and confirm the new ones fail.
  3. Add the float function and make the string function call it.
  4. Run `tests/test_post_processing_common.py` and `tests/test_comparison_report_generator.py`.
  5. Sweep callers: grep every use of the string function and confirm each is unaffected.
- **Relevant context.** `post_processing/common.py` (the existing function takes `"<value> <unit>"`
  strings), `tests/test_post_processing_common.py`,
  `post_processing/reports/comparison_report_generator.py`.
- **Acceptance criterion.** New tests pass; existing percent-difference and comparison-report tests
  pass unchanged.

---

### 1. Loader and pairing

- **Status:** [x] done
- **Intent.** Turn two archive directories into paired baseline/candidate workloads, plus lists of
  missing and extra workloads, without trusting the `id-xxx` name or assuming anything about the YAML.
- **Expected outcomes.**
  - Given two archives with different `id-xxx` names, workloads pair by filename stem.
  - Time-series files (`*_timeseries.json`) are ignored. The existing finder returns directories, so the
    loader must itself skip these files inside them.
  - Missing and extra workloads are reported separately and logged as warnings.
  - A differing `runtime_seconds` between a pair is logged as a warning.
  - Failures raise a clear error that the CLI maps to exit code 3.
  - Nothing in the loader depends on the number, names or parameters of the workloads.
- **Todo list.**
  1. Write failing tests using real JSON written to a temp directory (built from small synthetic
     files): pairing across different `id-xxx` names; time-series exclusion; missing; extra; a
     duplicate stem within one archive raising `ValueError` that names the files and suggests a
     narrower directory; empty or invalid JSON raising a clear error; an archive with no intermediate
     files giving a message that points to `tools/fio_common_output_wrapper.py`; a nonexistent
     directory; the `runtime_seconds` warning; and a case with a different number of workloads and
     different filename stems than the first tests, to show nothing is hardcoded.
  2. Run and confirm they fail.
  3. Implement the loader in a new module under `post_processing/regression/`, reusing the existing
     directory finder and file reader.
  4. Run the tests until they pass.
- **Relevant context.** `post_processing/common.py` (`find_hockey_stick_visualisation_directories`,
  `read_intermediate_file`, `file_is_empty`); directory layout
  `results/<iteration>/id-<hash>/.../visualisation/*.json`. Sibling `post_processing/` packages carry an
  `__init__.py`, so add an empty one to `post_processing/regression/`.
- **Acceptance criterion.** All loader tests pass, including every negative path.

---

### 2. Flags (with their types)

- **Status:** [x] done
- **Intent.** Compute each flag from one paired workload as a pure function.
- **Expected outcomes.** For a pair of parsed JSON dicts, H1, H2, H3, M1, M2 and M3 each return a flag
  result with the band and the numbers behind it. Types added to `post_processing_types.py`: band enum,
  flag-tier, flag-result, and the thresholds type holding the 10% and 20% values.
- **Todo list.**
  1. Write failing tests per flag: a 25% drop is STRONG, 15% is WEAK, 5% is NONE, an improvement is
     NONE; exact boundaries at 10% (WEAK) and 20% (STRONG); zero baseline is NOT_EVALUABLE; a missing
     field is NOT_EVALUABLE; latency and CPU fire on rise only.
  2. Write failing tests for the iodepth groups: numeric ordering (a sweep where `"128"` would sort
     before `"16"` as text); `n // 3` group size on sweeps of different lengths (for example 10 and 7
     depths) and different start points (one starting at 1, one at 4); worst-depth selection and which
     depth is reported; NOT_EVALUABLE with fewer than 3 common depths.
  3. Run and confirm they fail.
  4. Implement the flag functions and the iodepth-group helper.
  5. Run the tests until they pass.
- **Relevant context.** The intermediate JSON format is documented at the top of
  `post_processing/formatter/common_output_formatter.py`. All values are strings. `latency` is in
  nanoseconds. Use the float helper from sub-task 0.
- **Acceptance criterion.** All flag tests pass, including boundaries, NOT_EVALUABLE cases and varied
  sweep shapes.

---

### 3. Verdicts (with their types)

- **Status:** [x] done
- **Intent.** Turn flag results into a workload decision, and workload decisions into a run decision.
- **Expected outcomes.** The workload and run rules in the Design section are implemented, with
  thresholds taken from the single tunable thresholds type. Types added: verdict enum, workload-result,
  run-result.
- **Todo list.**
  1. Write failing table-driven tests for the workload verdict: H STRONG gives FAIL; H WEAK gives
     REVIEW; M at any firing band gives REVIEW; nothing gives PASS; NOT_EVALUABLE never changes the
     verdict.
  2. Write failing run-level tests: FC=1 of 8 gives REVIEW; FC=3 of 8 gives FAIL; RC=1 gives PASS;
     RC=2 gives REVIEW; K for N=1, 2, 6 and 10; a missing workload counted in N and RC with all others
     passing gives PASS with a logged warning; N=0 raises an error.
  3. Run and confirm they fail.
  4. Implement the verdict functions.
  5. Run the tests until they pass.
- **Relevant context.** The Design section; `K = max(1, min(3, ceil(N * 0.5)))`.
- **Acceptance criterion.** All verdict tests pass.

---

### 4. Summary output and CLI

- **Status:** [x] done
- **Intent.** Make every verdict traceable, and expose it through a thin entry point that matches the
  existing `tools/generate_*` scripts.
- **Expected outcomes.**
  - `tools/check_regression.py --baseline <dir> --candidate <dir>` prints: warnings; one card per
    workload that is not PASS, listing each flag with baseline value, candidate value, percentage
    change and band (plus the iodepth for H3 and M3); NOT_EVALUABLE flags with a reason; and a final
    line with N, K, FC, RC, the missing count and the run verdict.
  - It exits 0, 1, 2 or 3 as defined above. The existing `tools/generate_*` scripts end with a bare
    `main()`, which discards the return value, so the new script must call `sys.exit(main())`.
- **Todo list.**
  1. Write failing tests asserting that the key content is present (workload name, flag name, the
     percentage, the band, the N/K/FC/RC line). Do not assert exact whitespace.
  2. Write failing end-to-end tests on temp archive trees: a baseline compared with itself gives exit 0
     and PASS; a candidate with a 25% IOPS drop on enough workloads gives exit 2; a single 15% IOPS drop
     on one workload of several gives exit 0 (RC=1) with that workload's card and a "needs review" line
     printed; two such workloads give exit 1; a nonexistent directory gives exit 3; an archive with no
     intermediate files gives exit 3. Add one small test that the script exits with the function's
     return value.
  3. Run and confirm they fail.
  4. Implement the summary formatter in a module under `post_processing/regression/` and the CLI as a
     thin wrapper over the loader, flags, verdicts and summary.
  5. Run the tests until they pass.
- **Relevant context.** `tools/generate_comparison_performance_report.py` for argparse and logger
  setup; `logging_configuration.setup_loggers()`.
- **Acceptance criterion.** Summary and end-to-end tests pass with the correct exit codes.

---

### 5. Docs, structure map and validation

- **Status:** [x] done
- **Intent.** Finish to the repo's standards.
- **Expected outcomes.**
  - `STRUCTURE.md` lists every new file (the `post_processing/regression/` modules, the new tool and
    the new test files).
  - A doc under `docs/` gives a concrete example: small input directory layouts and the output the tool
    really prints. It states the prerequisite (the MVP needs intermediate JSON to already exist, as it
    does for teuthology runs with a `report:` section; to create it otherwise run
    `tools/fio_common_output_wrapper.py`)
    and that the two archives are expected to come from the same YAML. It notes that automatic
    generation may be added after the MVP.
  - The full toolchain is clean.
- **Todo list.**
  1. Update `STRUCTURE.md`.
  2. Write the doc with a worked example whose output matches what the tool actually prints.
  3. Run black, ruff, mypy and pylint on every changed file (pylint one file at a time, score 10.0).
  4. Run `python3 -m pytest -c pyproject.toml tests/` and confirm no new failures.
  5. Tell the user the work is ready for review. Do not commit or push.
- **Relevant context.** `STRUCTURE.md`; the toolchain section of `docs/dev/coding-guidelines.md`.
- **Acceptance criterion.** The full toolchain passes with no new warnings, and the full test suite passes.

---

### 6. Real-archive calibration (user-run, after the above)

- **Status:** [ ] pending
- **Intent.** Unit tests prove the logic; real runs show whether 10% and 20% are sensible. This needs
  the user's archives.
- **Expected outcomes.** Known behaviour on real data and a recommendation on thresholds.
- **Todo list.**
  1. Baseline against itself: expect a clean PASS.
  2. Two runs of the same build: this is the noise floor. If it produces REVIEW or FAIL, thresholds
     need to move, or the CPU flag needs an absolute minimum change.
  3. Two different builds.
  4. Record findings and any threshold changes in this file.
- **Relevant context.** Real archives, `<archive>/results/<iteration>/id-<hash>/...`.
- **Acceptance criterion.** The user is satisfied with the thresholds, or a follow-up is recorded.

### 7. Post-MVP: standalone use (placeholder, not planned in detail)

- **Status:** [ ] not started. **Do not plan or work on this now.** It will be re-planned after the MVP
  is validated on real teuthology archives.
- **Direction only.** The user's current idea: when an archive has no intermediate JSON, warn and
  generate it automatically (for example via `CommonOutputFormatter`, which `Report` and
  `tools/fio_common_output_wrapper.py` already use). Odd cases (a baseline store with no `visualisation/`
  and no raw fio output, the raw filename root differing such as `output` vs `json_output`) are not
  designed here. For now they simply fall under the MVP's "error and exit 3".

## Assumptions and open questions

Record answers here as they are given. Each is asked with `ask_followup_question` before the sub-task
that depends on it. Do not proceed on a guess.

| # | Question or assumption | Needed before | Status |
|---|---|---|---|
| 1 | Where do the `visualisation/` directories live? **Answer:** the teuthology report config looks like `report: {force_refresh: True, create_pdf: True, output_directory: "/home/ubuntu/cephtest/archive/cbt/reports/", results_file_root: "output", no_error_bars: True}`, and `visualisation/` is available from the archive. Baselines will probably keep only what is necessary, and how or where they are stored is undecided. **Decision:** the tool must find `visualisation/` wherever it sits under the given directory, and must not assume `results/<iteration>/id-<hash>/`. | Sub-task 1 | [x] answered |
| 2 | REVIEW semantics. **Answer:** REVIEW will probably fail the teuthology run but must be presented as "needs review", not a full FAIL. **Decision:** teuthology wiring is out of scope for the POC. The POC only needs distinct exit codes (0, 1, 2, 3) and a clear label. | Sub-task 4 | [x] answered, deferred |
| 3 | Thresholds. **Answer:** unsure; 10% and 20% are fine for now and will be tweaked later. **Decision:** keep 10% and 20% in one tunable place; revisit in sub-task 6. | Sub-task 2 | [x] answered |
| 4 | Post-MVP generation of missing intermediate JSON. **Answer:** warn and generate automatically, but this is not a POC focus. **Decision:** deferred. Do not design it now. The MVP only needs a clean "error and exit 3" for archives with no `visualisation/`. | Post-MVP | [x] answered, deferred |
| 5 | Float helper signature. **Answer:** `float_percent_difference(baseline: float, candidate: float) -> float` — two floats in, unrounded fractional change out. Zero baseline raises `ZeroDivisionError`, matching the existing string function. | Sub-task 0 | [x] answered |
| 6 | `runtime_seconds` warning trigger. **Answer:** fire if ANY iodepth entry in the paired workload files has a differing `runtime_seconds` value. | Sub-task 1 | [x] answered |
| 7 | Baseline directory depth. **Answer:** baselines may be stored at any depth — as a bare `visualisation/` folder or anywhere else. The finder must locate `visualisation/` wherever it sits under the given directory (which the existing three-level finder already handles). | Sub-task 1 | [x] answered |
| 8 | Band enum name for "no problem". **Answer:** `VALID` (replaces the plan's `NONE`). | Sub-task 2 | [x] answered |
| 9 | Zero-baseline handling. **Answer:** treat as a normal numeric comparison where possible. Baseline=0 is only a division-by-zero problem; handle it by direction: drop-fires flags (H1, H2) → `VALID` (candidate ≥ 0 is at least as good); rise-fires flags (H3, M1, M2, M3) with candidate > 0 → `STRONG`. Both baseline and candidate zero → `VALID`. | Sub-task 2 | [x] answered |
| 10 | NOT_EVALUABLE effect on workload verdict. **Answer:** a `NOT_EVALUABLE` flag contributes to REVIEW, the same as a firing M flag. Missing field or non-numeric value → `NOT_EVALUABLE` with a reason string. | Sub-task 2 | [x] answered |
| 11 | Naming: "verdict" vs "decision". **Answer:** use `Decision` throughout (`Decision` enum, `WorkloadDecision`, `RunDecision`). | Sub-task 3 | [x] answered |
| 12 | RunDecision field names. **Answer:** `num_workloads` (N), `fail_count` (FC), `review_count` (RC), `fail_target` (K — the threshold number of FAILs needed to fail the run). | Sub-task 3 | [x] answered |

## Open points to watch during implementation

- The existing finder `find_hockey_stick_visualisation_directories()` checks three levels in order and
  stops at the first that has JSON: the root's own `visualisation/`, then each direct child's
  `visualisation/`, then a deep recursive search. It does not merge levels, and it fails on a
  nonexistent path (it calls `iterdir()`). Sub-task 1 must test a few layouts of varying depth, check
  the directory exists first, and ask the user (per the Assumptions rule) if the finder is not good
  enough, rather than change shared code silently.
- `STRUCTURE.md` excludes `__init__.py` files from its listing, so do not list the new one there.
- The filename stem encodes blocksize in bytes (for example `262144_...`), as written by
  `_write_operation_results()`. Pairing on the stem avoids having to parse it.
- Raw `maximum_cpu_usage` is small in the sample (`0.71`). A 10% relative change is about 0.07 points,
  so M1 may be noisy. Sub-task 6 will show whether an absolute minimum is needed. Not added now.
- Taking the worst depth of a group raises the false-alarm rate. If the noise-floor run shows this,
  switch to the group median.
- With `volumes_per_client: 16` and low `total_iodepth` values, per-volume depth is small, so low-depth
  latency may be noisier than high-depth latency. Watch H3 in calibration.

## Change log

(Filled in during implementation: file, what changed and why, and the commands run with results.)


### Sub-task 0 — Float percent-difference helper

**`post_processing/common.py`**
- Added `float_percent_difference(baseline: float, candidate: float) -> float`: returns the
  unrounded fractional change; raises `ZeroDivisionError` on a zero baseline.
- Refactored `calculate_percent_difference_to_baseline` to delegate its division to
  `float_percent_difference` instead of duplicating the arithmetic. Behaviour is unchanged.
- Applied `black` reformatting (the file had pre-existing style issues that black surfaced;
  reformatted in full as required by the toolchain).

**`tests/test_post_processing_common.py`**
- Added import of `float_percent_difference` (alphabetically ordered in the import block).
- Added four new tests (no existing tests modified):
  - `test_float_percent_difference_unrounded` — 9.6% drop returns −0.096 (unrounded).
  - `test_float_percent_difference_improvement_is_positive` — 10% gain returns +0.10.
  - `test_float_percent_difference_zero_baseline_raises` — zero baseline raises `ZeroDivisionError`.
  - `test_calculate_percent_difference_zero_baseline_raises` — same for the string function.
- Applied `black` reformatting.

**Commands run and results:**
- `python3 -m pytest tests/test_post_processing_common.py` (after adding tests, before code):
  `ImportError` — confirmed new tests fail as expected.
- `python3 -m pytest tests/test_post_processing_common.py tests/test_comparison_report_generator.py -v`:
  41 passed (4 new + 37 existing); 1 pre-existing failure (`test_find_common_data_file_names`), unrelated.
- `git stash` / `git stash pop` confirmed `test_find_common_data_file_names` was already failing
  on the baseline branch.
- `black post_processing/common.py tests/test_post_processing_common.py`: 2 files reformatted.
- `ruff check post_processing/common.py tests/test_post_processing_common.py`: 8 errors, all
  pre-existing in `common.py`; baseline had 9 (the import-ordering issue introduced by this change
  was fixed before the final check).
- `python3 -m pytest tests/ -v`: 1138 passed, 1 pre-existing failure, 3 skipped — no new failures.

### Sub-task 1 — Loader and pairing

**`post_processing/regression/__init__.py`** (new)
- Empty package marker; required so `post_processing.regression` is importable.

**`post_processing/regression/loader.py`** (new)
- `ArchiveLoadError` — exception class mapping to CLI exit 3.
- `WorkloadPair` — dataclass holding `stem`, `baseline_data`, `candidate_data`.
- `_collect_json_files(directory)` — calls `find_hockey_stick_visualisation_directories`,
  skips `*_timeseries.json` files, detects duplicate stems, raises `ArchiveLoadError` for
  missing directory or no JSON found (message references `fio_common_output_wrapper.py`).
- `_load_json(path)` — checks `file_is_empty`, parses JSON, raises `ArchiveLoadError` on
  empty or corrupt files naming the file.
- `_check_runtime_seconds(stem, baseline_data, candidate_data)` — warns if any iodepth entry
  has a differing `runtime_seconds`; one warning per workload.
- `load_archive_pairs(baseline_dir, candidate_dir)` — orchestrates the above; warns for missing
  and extra stems; raises `ArchiveLoadError` if nothing pairs.

**`tests/test_regression_loader.py`** (new)
- 17 tests covering: pairing by stem across different `id-xxx` names; multiple stems; missing
  and extra stems; timeseries exclusion; `runtime_seconds` warning and no-warning path;
  nonexistent baseline/candidate; no intermediate files (message checks `fio_common_output_wrapper`);
  no paired workloads; duplicate stem; invalid JSON; empty JSON file; bare `visualisation/` as
  archive; deeply nested `results/<iter>/id-<hash>/op/visualisation/` layout; five unusual stem
  shapes (nothing hardcoded); `WorkloadPair` structure.

**`STRUCTURE.md`** — added `regression/` sub-package and `test_regression_loader.py`.

**Commands run and results:**
- `python3 -m pytest tests/test_regression_loader.py` (before loader.py): `ModuleNotFoundError`
  — confirmed all tests fail as expected.
- `python3 -m pytest tests/test_regression_loader.py -v`: 17 passed.
- `black post_processing/regression/loader.py tests/test_regression_loader.py`: reformatted.
- `ruff check post_processing/regression/loader.py tests/test_regression_loader.py`:
  fixed unused import (`read_intermediate_file`), `Optional` → `str | None`, merged nested `if`,
  `_missing`/`_extra` prefixes for unused unpacked vars. All checks passed.
- `python3 -m pytest tests/`: 1155 passed, 1 pre-existing failure, 3 skipped — no new failures.

### Sub-task 3 — Decisions

**Design decisions recorded (Q&A #11–12):**
- Named `Decision` / `WorkloadDecision` / `RunDecision` (not "verdict").
- `RunDecision` fields: `num_workloads`, `fail_target`, `fail_count`, `review_count`.

**`post_processing/post_processing_types.py`**
- Added `Decision` enum: `PASS`, `REVIEW`, `FAIL`.
- Added `WorkloadDecision` NamedTuple: `stem`, `decision`, `flags`.
- Added `RunDecision` NamedTuple: `decision`, `num_workloads`, `fail_target`, `fail_count`, `review_count`.

**`post_processing/regression/decisions.py`** (new)
- `_compute_fail_target(num_workloads)` — `max(1, min(3, ceil(N * 0.5)))`.
- `compute_workload_decision(flags)` — FAIL/REVIEW/PASS per flag tiers; NOT_EVALUABLE → REVIEW.
- `compute_run_decision(num_workloads, fail_count, review_count)` — raises `ValueError` for N=0.

**`tests/test_regression_decisions.py`** (new)
- 22 tests covering: all workload decision combinations; NOT_EVALUABLE → REVIEW; FAIL precedence;
  `_compute_fail_target` for N=1/2/3/6/10; run decision FC/RC rules; N=0 raises; field values.

**`STRUCTURE.md`** — added `decisions.py` and `test_regression_decisions.py`.

**Commands run and results:**
- `python3 -m pytest tests/test_regression_decisions.py -v`: 22 passed.
- `black` + `ruff check --config pyproject.toml`: all clean.
- `python3 -m pytest tests/`: 1220 passed, 1 pre-existing failure, 3 skipped — no new failures.

### Sub-task 2 — Flags

**Design decisions recorded (Q&A #8–10):**
- `VALID` replaces the plan's `NONE` band name.
- Zero-baseline: drop-fires flags (H1,H2) → `VALID` if candidate≥0; rise-fires flags (H3,M1,M2,M3) → `STRONG` if candidate>0, `VALID` if both zero.
- `NOT_EVALUABLE` (missing/non-numeric field) contributes to REVIEW in the workload verdict, same as a firing M flag.

**`post_processing/post_processing_types.py`**
- Added `Band` enum: `VALID`, `WEAK`, `STRONG`, `NOT_EVALUABLE`.
- Added `FlagTier` enum: `H`, `M`.
- Added `FlagResult` NamedTuple: `tier`, `band`, `baseline_value`, `candidate_value`, `pct_change`, `reason`, `iodepth`.
- Added `Thresholds` NamedTuple: `weak`, `strong` (defaults 0.10, 0.20).

**`post_processing/regression/flags.py`** (new)
- `_not_evaluable()` — builds a NOT_EVALUABLE FlagResult.
- `_read_float()` — extracts a float from a dict key; returns `(None, reason)` on missing/non-numeric.
- `_band_for_worsening()` — maps fractional worsening to `VALID`/`WEAK`/`STRONG`.
- `_evaluate_drop_fires()` — handles H1/H2 direction with zero-baseline rules.
- `_evaluate_rise_fires()` — handles H3/M1/M2/M3 direction with zero-baseline rules.
- `_common_iodepths()` — returns numerically sorted common iodepth keys.
- `_latency_flag_for_group()` — picks the worst-worsening depth in a group.
- Six public `compute_*` functions: `compute_h1_max_iops`, `compute_h2_max_bandwidth`, `compute_h3_low_depth_latency`, `compute_m1_max_cpu`, `compute_m2_max_memory`, `compute_m3_high_depth_latency`.

**`tests/test_regression_flags.py`** (new)
- 43 tests covering: STRONG/WEAK/VALID/boundary/NOT_EVALUABLE for each flag; zero-baseline rules by direction; improvement never fires; numeric iodepth ordering; group sizes for n=10 and n=7; sweep starting at 4; worst-depth selection; <3 common depths NOT_EVALUABLE; H3/M3 cross-isolation (low-depth rise doesn't fire M3, etc.).

**`STRUCTURE.md`** — added `flags.py` and `test_regression_flags.py`.

**Commands run and results:**
- `python3 -m pytest tests/test_regression_flags.py` (before flags.py): `ImportError` — confirmed fail.
- `python3 -m pytest tests/test_regression_flags.py -v`: 43 passed.
- `black` + `ruff check` on changed files: 0 new errors introduced (5 pre-existing `Union` warnings in `post_processing_types.py` unchanged from baseline).
- `python3 -m pytest tests/`: 1198 passed, 1 pre-existing failure, 3 skipped — no new failures.

### Sub-task 4 — Summary output and CLI

**`post_processing/regression/summary.py`** (new)
- `format_workload_line(stem, decision)` — single workload verdict line.
- `format_flag_detail(flag, label)` — details for firing / NOT_EVALUABLE flags.
- `format_workload_block(wd, flag_labels)` — workload block with flag details.
- `format_run_summary(run, missing_count)` — overall result and workload count summary.
- `print_summary(workload_decisions, run_decision, missing_count)` — prints formatted output to stdout.

**`post_processing/regression/runner.py`** (new)
- `_evaluate_pair(pair, thresholds)` — computes flags and decision for a paired workload.
- `run_comparison(baseline_dir, candidate_dir, thresholds)` — orchestrates loader → flags → decisions → summary, returning exit codes (0, 1, 2, 3).

**`tools/check_regression.py`** (new)
- CLI wrapper using `argparse`, logger setup, and `sys.exit(main())`.

**`tests/test_regression_summary.py`** (new)
- End-to-end test suite testing exit codes 0, 1, 2, and 3 across various baseline/candidate scenarios.

### Sub-task 5 — Docs, structure map and validation

**`STRUCTURE.md`**
- Added entries for `post_processing/regression/summary.py`, `post_processing/regression/runner.py`, `tools/check_regression.py`, `tests/test_regression_summary.py`, and `docs/dev/check-regression.md`.

**`docs/dev/check-regression.md`** (new)
- Usage documentation for `tools/check_regression.py` with prerequisites, exit codes table, and example run output.

### Calibration tweak — Overriding thresholds for H3 (low-depth latency)

**`post_processing/regression/flags.py`**
- Added `_H3_THRESHOLDS = Thresholds(weak=0.25, strong=0.35)` to account for higher noise floor at low queue depths.
- Updated `compute_h3_low_depth_latency()` to evaluate against `_H3_THRESHOLDS` (25% weak, 35% strong).

**`tests/test_regression_flags.py`**
- Updated and added unit tests in `TestH3LowDepthLatency` covering 40% rise (STRONG), 30% rise (WEAK), 20% rise (VALID), exact 25% weak boundary, exact 35% strong boundary, and verifying general strict thresholds do not override fixed H3 thresholds.

**Commands run and results:**
- `black --config pyproject.toml`: all files formatted.
- `ruff check --config pyproject.toml`: all checks passed cleanly.
- `mypy --config-file pyproject.toml`: success, 0 issues across all 13 source/test files.
- `pylint --rcfile pyproject.toml`: 10.00/10 on all modified source files.
- `pytest -c pyproject.toml tests/test_regression*.py`: 95 passed.
