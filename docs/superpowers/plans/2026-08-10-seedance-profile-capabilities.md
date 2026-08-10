# Seedance Profile Capabilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Seedance compatibility suite select legal, capability-specific default tests through a required `--profile` flag while reporting unsupported cases as skipped.

**Architecture:** Store authoritative model limits in `profiles.yaml` and parse them into an immutable `SeedanceProfile` object in a focused `profiles.py` module. The existing runner applies profile-specific overrides, filters cases through structured requirements, builds new official-URL scenarios, and performs request/response and MOV-container assertions. The shared report gains a pass-neutral `skipped` state.

**Tech Stack:** Python 3 standard library, PyYAML, jsonschema draft 2020-12, unittest, YAML, Markdown.

## Global Constraints

- `--profile` is required and accepts exactly `seedance-2.5`, `seedance-2.0`, `seedance-2.0-fast`, or `seedance-2.0-mini`.
- `--model` remains independent and may contain an official Model ID, Endpoint ID, or custom alias.
- All media inputs are public URLs from Volcengine documentation; no Base64 or local media fixtures.
- Capability-specific cases run by default; there is no `--extended` mode.
- Do not claim coverage for 10 video, 10 audio, or 50 total reference assets because current official links cannot satisfy the duration constraints.
- Live API generation is not required without `API_BASE_URL` and `API_KEY`; dry-run and unit tests must be fully local.

---

## File Structure

- Create `test-cases/seedance/profiles.yaml`: declarative profile capability values and 2.5 task overrides.
- Create `test-cases/seedance/profiles.py`: typed profile loading, validation, requirement matching, and case override merging.
- Create `test-cases/seedance/test_profiles.py`: profile module unit tests.
- Create `test-cases/seedance/test_run_tests.py`: request construction, checks, skip flow, and MOV probe tests.
- Create `test-cases/_shared/test_report.py`: skipped report behavior tests.
- Modify `test-cases/_shared/report.py`: skipped summary/rendering semantics.
- Modify `test-cases/seedance/run_tests.py`: required profile CLI, scenario builders, case selection, checks, and reporting.
- Modify `test-cases/seedance/cases.yaml`: capability-aware cases and official URLs.
- Modify `test-cases/seedance/README.md`: profile matrix, invocation, cases, skip semantics, and source limitations.
- Modify `test-cases/README.md`: document `skipped` as a report status.

---

### Task 1: Add pass-neutral skipped reporting

**Files:**
- Modify: `test-cases/_shared/report.py:23-345`
- Create: `test-cases/_shared/test_report.py`
- Modify: `test-cases/README.md:96-125`

**Interfaces:**
- Produces: `Report.summary()["skipped"] -> int`.
- Produces: `CaseResult(status="skipped", details={"skip_reason": ...})` rendered as `○` without failing `Report.passed`.

- [ ] **Step 1: Write failing skipped-report tests**

```python
class ReportSkippedTests(unittest.TestCase):
    def test_skipped_is_counted_but_does_not_fail_report(self):
        report = Report(model="ep", cases=[
            CaseResult(id="ok", name="ok", status="pass"),
            CaseResult(id="skip", name="skip", status="skipped"),
        ])
        self.assertEqual(report.summary()["skipped"], 1)
        self.assertTrue(report.passed)

    def test_skipped_is_visible_in_markdown_and_html(self):
        report = Report(model="ep", cases=[CaseResult(
            id="skip", name="skip", status="skipped",
            details={"skip_reason": "profile 不支持 4k"},
        )])
        self.assertIn("跳过 1", report.to_markdown())
        self.assertIn("class='skipped'", report.to_html())
```

- [ ] **Step 2: Run the tests and verify the missing summary key failure**

Run: `python -m unittest discover -s test-cases/_shared -p 'test_*.py' -v`

Expected: FAIL because `skipped` is not counted or rendered.

- [ ] **Step 3: Implement skipped summary and rendering**

```python
_STATUS_ICON = {"pass": "✓", "fail": "✗", "error": "!", "skipped": "○"}

def summary(self) -> dict[str, int]:
    skipped = sum(1 for c in self.cases if c.status == "skipped")
    return {"total": len(self.cases), "passed": passed, "failed": failed,
            "errored": errored, "skipped": skipped, "warned": warned,
            "duration_ms": sum(c.duration_ms for c in self.cases)}
```

Add `跳过 {s['skipped']}` to Markdown/HTML summaries and a neutral `.skipped` row style. Keep `Report.passed` defined only by `failed == 0 and errored == 0`.

- [ ] **Step 4: Run shared report tests**

Run: `python -m unittest discover -s test-cases/_shared -p 'test_*.py' -v`

Expected: PASS.

- [ ] **Step 5: Commit the reporting unit**

```bash
git add test-cases/_shared/report.py test-cases/_shared/test_report.py test-cases/README.md
git commit -m "feat(test-report): support skipped cases"
```

### Task 2: Add validated Seedance profiles and requirement matching

**Files:**
- Create: `test-cases/seedance/profiles.yaml`
- Create: `test-cases/seedance/profiles.py`
- Create: `test-cases/seedance/test_profiles.py`

**Interfaces:**
- Produces: `SeedanceProfile` dataclass fields `name`, `resolutions`, `output_formats`, `max_duration`, `audio_only_reference`, `max_reference_images`, `max_reference_videos`, `max_reference_audios`, `max_total_reference_assets`, `scenario_overrides`.
- Produces: `load_profiles(path: Path | None = None) -> dict[str, SeedanceProfile]`.
- Produces: `unmet_requirement(requirements: dict, profile: SeedanceProfile) -> str | None`.
- Produces: `apply_profile_overrides(case: dict, profile: SeedanceProfile) -> dict`.

- [ ] **Step 1: Write failing profile tests**

```python
class ProfileTests(unittest.TestCase):
    def test_official_capability_matrix(self):
        profiles = load_profiles()
        self.assertIn("4k", profiles["seedance-2.0"].resolutions)
        self.assertNotIn("4k", profiles["seedance-2.5"].resolutions)
        self.assertIn("mov", profiles["seedance-2.5"].output_formats)
        self.assertEqual(profiles["seedance-2.5"].max_duration, 30)
        self.assertTrue(profiles["seedance-2.5"].audio_only_reference)

    def test_requirement_explains_unsupported_resolution(self):
        profile = load_profiles()["seedance-2.0-mini"]
        reason = unmet_requirement({"resolution": "4k"}, profile)
        self.assertIn("4k", reason)

    def test_profile_override_wins_over_case_value(self):
        profile = load_profiles()["seedance-2.5"]
        case = {"scenario": "image_to_video", "duration": 5, "ratio": "16:9"}
        merged = apply_profile_overrides(case, profile)
        self.assertEqual(merged["duration"], -1)
        self.assertEqual(merged["ratio"], "adaptive")
```

- [ ] **Step 2: Run profile tests and verify import failure**

Run: `python -m unittest discover -s test-cases/seedance -p 'test_profiles.py' -v`

Expected: FAIL because `profiles.py` does not exist.

- [ ] **Step 3: Add the exact capability data**

```yaml
profiles:
  seedance-2.5:
    resolutions: [480p, 720p]
    output_formats: [mp4, mov]
    max_duration: 30
    audio_only_reference: true
    max_reference_images: 30
    max_reference_videos: 10
    max_reference_audios: 10
    max_total_reference_assets: 50
    scenario_overrides:
      image_to_video: {ratio: adaptive, duration: -1}
      start_end_to_video: {ratio: adaptive}
```

Add 2.0 standard, Fast, and Mini with the exact values from the approved design.

- [ ] **Step 4: Implement strict loading and supported requirement keys**

`unmet_requirement` must support `resolution`, `output_format`, `min_max_duration`, `audio_only_reference`, and `min_max_reference_videos`. Unknown keys raise `ValueError` so misspelled capability rules cannot silently run.

```python
@dataclass(frozen=True)
class SeedanceProfile:
    name: str
    resolutions: frozenset[str]
    output_formats: frozenset[str]
    max_duration: int
    audio_only_reference: bool
    max_reference_images: int
    max_reference_videos: int
    max_reference_audios: int
    max_total_reference_assets: int
    scenario_overrides: dict[str, dict[str, object]]
```

- [ ] **Step 5: Run profile tests**

Run: `python -m unittest discover -s test-cases/seedance -p 'test_profiles.py' -v`

Expected: PASS.

- [ ] **Step 6: Commit the profile unit**

```bash
git add test-cases/seedance/profiles.yaml test-cases/seedance/profiles.py test-cases/seedance/test_profiles.py
git commit -m "feat(seedance): add model capability profiles"
```

### Task 3: Make the runner profile-aware and build capability scenarios

**Files:**
- Modify: `test-cases/seedance/run_tests.py:15-255,493-668`
- Modify: `test-cases/seedance/cases.yaml`
- Create: `test-cases/seedance/test_run_tests.py`

**Interfaces:**
- Consumes: `SeedanceProfile`, `load_profiles`, `unmet_requirement`, `apply_profile_overrides` from Task 2.
- Produces: `build_content(scenario: str, cfg: dict, case: dict, profile: SeedanceProfile) -> list[dict]`.
- Produces: `run_case(..., profile: SeedanceProfile, ...) -> CaseResult`, including skipped results before request construction.

- [ ] **Step 1: Write failing construction and skip tests**

Load `run_tests.py` with `importlib.util.spec_from_file_location` and assert:

```python
def test_profile_max_image_content_count(self):
    content = runner.build_content(
        "reference_images_profile_max", self.config, self.case,
        self.profiles["seedance-2.5"],
    )
    refs = [item for item in content if item.get("role") == "reference_image"]
    self.assertEqual(len(refs), 30)

def test_unsupported_case_is_skipped_without_sending_request(self):
    case = {"id": "4k", "name": "4k", "scenario": "text_to_video",
            "requires": {"resolution": "4k"}}
    result = runner.run_case(case, profile=self.profiles["seedance-2.5"],
                             schemas={}, config=self.config, model="ep",
                             base_url="", api_key="", dry_run=True, no_poll=False)
    self.assertEqual(result.status, "skipped")
    self.assertIn("4k", result.details["skip_reason"])
```

Also assert audio-only content is exactly text + reference audio, the 2.5 profile-max request contains six `reference_video` items, `output_format` is copied into the request body, and the 2.5 first-frame override produces `ratio=adaptive` and `duration=-1`.

- [ ] **Step 2: Run runner tests and verify signature/scenario failures**

Run: `python -m unittest discover -s test-cases/seedance -p 'test_run_tests.py' -v`

Expected: FAIL because profile-aware signatures and scenarios are absent.

- [ ] **Step 3: Implement profile-aware case flow**

At the beginning of `run_case`:

```python
reason = unmet_requirement(case.get("requires", {}), profile)
if reason:
    return CaseResult(id=cid, name=name, status="skipped", duration_ms=0,
                      details={"scenario": scenario, "model": case_model,
                               "profile": profile.name, "skip_reason": reason})
effective_case = apply_profile_overrides(case, profile)
```

Use `effective_case` for content, body, checks, expected error code, and polling behavior. Pass `profile` into content construction and validate generated image/video/audio reference counts against its limits before sending.

- [ ] **Step 4: Add scenario builders and output_format**

Add builders for `audio_only_reference` and `reference_images_profile_max`. `reference_images_profile_max` combines the profile image maximum with six videos for 2.5 or one video for 2.0, plus one audio reference. Extend the optional body-key loop with `output_format`.

- [ ] **Step 5: Replace and add YAML cases with official URLs**

Merge 2.5 30-second/MOV checks and 2.0-standard 4K checks into profile-specific overrides of the single `t2v_full` request. Add `audio_only_reference` and the combined `reference_images_profile_max` using the exact URLs in the design spec. Put 2.5 `duration=-1` on the existing first-frame request and change the global ratio to `adaptive`.

- [ ] **Step 6: Require the CLI profile and record it**

```python
profiles = load_profiles()
parser.add_argument("--profile", required=True, choices=sorted(profiles))
profile = profiles[args.profile]
```

Pass `profile` to all cases, set report env `SEEDANCE_PROFILE`, and print `skip={s['skipped']}` in the Seedance console summary.

- [ ] **Step 7: Run construction tests and four dry runs**

Run:

```bash
python -m unittest discover -s test-cases/seedance -p 'test_*.py' -v
python test-cases/seedance/run_tests.py --dry-run --profile seedance-2.5 --out /tmp/seedance-25-report
python test-cases/seedance/run_tests.py --dry-run --profile seedance-2.0 --out /tmp/seedance-20-report
python test-cases/seedance/run_tests.py --dry-run --profile seedance-2.0-fast --out /tmp/seedance-fast-report
python test-cases/seedance/run_tests.py --dry-run --profile seedance-2.0-mini --out /tmp/seedance-mini-report
```

Expected: all commands exit 0; 2.5 全部 9 个 case 通过，2.0 variants 各有 8 个 case 通过、纯音频 case 跳过。预计真正出片数分别为 6 / 5 / 5 / 5。

- [ ] **Step 8: Commit profile-aware execution**

```bash
git add test-cases/seedance/run_tests.py test-cases/seedance/cases.yaml test-cases/seedance/test_run_tests.py
git commit -m "feat(seedance): select cases by model profile"
```

### Task 4: Verify requested resolution, duration, and MOV container

**Files:**
- Modify: `test-cases/seedance/run_tests.py:346-487`
- Modify: `test-cases/seedance/test_run_tests.py`

**Interfaces:**
- Produces: `is_quicktime_mov_header(data: bytes) -> bool`.
- Produces: `probe_mov_url(url: str, timeout: int = 60) -> tuple[bool, dict[str, object]]`.
- Extends: `run_checks(..., create_body: dict, ...)` with `query_resolution_matches_request`, `query_duration_matches_request`, and `succeeded_video_format_matches_request`.

- [ ] **Step 1: Write failing assertion and header tests**

```python
def test_quicktime_major_brand(self):
    self.assertTrue(runner.is_quicktime_mov_header(b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00"))
    self.assertFalse(runner.is_quicktime_mov_header(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00"))

def test_resolution_must_match_request(self):
    verdict = runner.run_checks(
        ["query_resolution_matches_request"], self.schemas,
        create_status=200, create_resp={}, query_status=200,
        query_resp={"resolution": "720p"}, polled=True,
        create_body={"resolution": "4k"},
    )
    self.assertEqual(verdict[0], "fail")
```

Add duration pass/fail cases and mock `urllib.request.urlopen` to assert a Range header is sent and only a bounded prefix is read.

- [ ] **Step 2: Run tests and verify missing helper/check failures**

Run: `python -m unittest discover -s test-cases/seedance -p 'test_run_tests.py' -v`

Expected: FAIL because the new checks and MOV probe do not exist.

- [ ] **Step 3: Implement request/response checks**

For resolution and duration, compare the final query response value directly with `create_body` and return an actionable expected/actual error. Only cases declaring the check invoke it.

- [ ] **Step 4: Implement bounded MOV probing**

```python
def is_quicktime_mov_header(data: bytes) -> bool:
    offset = data.find(b"ftyp")
    return offset >= 0 and data[offset + 4:offset + 8] == b"qt  "

def probe_mov_url(url: str, timeout: int = 60) -> tuple[bool, dict[str, object]]:
    req = urllib.request.Request(url, headers={"Range": "bytes=0-255"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        prefix = resp.read(256)
        meta = {"content_type": resp.headers.get("Content-Type", ""),
                "prefix_hex": prefix[:32].hex()}
    return is_quicktime_mov_header(prefix), meta
```

Catch network and HTTP exceptions and return probe metadata in the check error instead of crashing the whole runner.

- [ ] **Step 5: Run all Seedance unit tests**

Run: `python -m unittest discover -s test-cases/seedance -p 'test_*.py' -v`

Expected: PASS.

- [ ] **Step 6: Commit capability assertions**

```bash
git add test-cases/seedance/run_tests.py test-cases/seedance/test_run_tests.py
git commit -m "test(seedance): verify profile-specific outputs"
```

### Task 5: Document, integrate, and run the complete local gate

**Files:**
- Modify: `test-cases/seedance/README.md`
- Modify: `test-cases/README.md`
- Verify: all files changed by Tasks 1-4

**Interfaces:**
- Consumes: final CLI and report schema from Tasks 1-4.
- Produces: user-facing invocation and capability coverage documentation.

- [ ] **Step 1: Update Seedance documentation**

Document the required `--profile`, four-profile matrix, capability-selected cases, skipped semantics, official media URLs, the 6-video/28.14-second coverage, and the explicit non-coverage of 10-video/10-audio/50-total limits.

- [ ] **Step 2: Update the shared report contract**

Change the top-level status table from `pass / fail / error` to `pass / fail / error / skipped`, explaining that skipped is neutral and records `details.skip_reason`.

- [ ] **Step 3: Run the full local gate**

```bash
python -m unittest discover -s test-cases/_shared -p 'test_*.py' -v
python -m unittest discover -s test-cases/seedance -p 'test_*.py' -v
python test-cases/seedance/run_tests.py --dry-run --profile seedance-2.5 --out /tmp/seedance-25-report
python test-cases/seedance/run_tests.py --dry-run --profile seedance-2.0 --out /tmp/seedance-20-report
python test-cases/seedance/run_tests.py --dry-run --profile seedance-2.0-fast --out /tmp/seedance-fast-report
python test-cases/seedance/run_tests.py --dry-run --profile seedance-2.0-mini --out /tmp/seedance-mini-report
python -m py_compile test-cases/_shared/report.py test-cases/seedance/profiles.py test-cases/seedance/run_tests.py
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 4: Inspect generated reports**

Confirm each JSON report includes `SEEDANCE_PROFILE`, `summary.skipped`, profile-correct create bodies, and skip reasons. Confirm Markdown and HTML summaries display skipped counts and remain PASS when all executed cases pass.

- [ ] **Step 5: Commit documentation and final integration fixes**

```bash
git add test-cases/seedance/README.md test-cases/README.md
git commit -m "docs(seedance): explain profile-aware coverage"
```

- [ ] **Step 6: Review final branch scope**

Run:

```bash
git status --short
git diff origin/main...HEAD --stat
git diff origin/main...HEAD --check
```

Expected: only the approved design/plan, Seedance suite, shared report, and their tests/docs are changed; the worktree is clean after commits.
