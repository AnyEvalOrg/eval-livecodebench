# LiveCodeBench for AnyEval

This packages the **original LiveCodeBench code-generation benchmark** (Jain et al.,
2024) as the Inspect task `livecodebench/livecodebench`. Models solve competitive
programming problems in Python. A problem earns one point only when every public
and private test passes; accuracy over one generation and one epoch is **pass@1**.
This is **not LiveCodeBench-Pro**, the different benchmark shipped by `inspect_evals`.

## Frozen newest window

The source is [livecodebench/code_generation_lite](https://huggingface.co/datasets/livecodebench/code_generation_lite),
revision `0fe84c3912ea0c4d4a78037083943e8f0c4dd505`. Inspection of its
[builder configs](https://huggingface.co/datasets/livecodebench/code_generation_lite/blob/0fe84c3912ea0c4d4a78037083943e8f0c4dd505/code_generation_lite.py)
on September 9, 2026 found **`release_v6`** to be newest (`release_latest` selects the
same files). It is cumulative; `test6.jsonl` is the newest added shard, with 175 problems.
The full release's six source files total approximately 4.49 GB before our compression.

The wheel ships **144 problems dated January 25–April 6, 2025**, inclusive: 92 AtCoder
stdin problems and 52 LeetCode functional problems. There are no Codeforces problems
in this selected window. All **382 public and 5,324 private tests** are retained.

The deterministic gzip JSONL artifact is **62,487,410 bytes** (about 59.6 MiB).
Private tests remain in their upstream compressed string representation. The complete
latest shard compressed to 92,815,112 bytes; to leave ample room below 100 MB, selection
uses a 64,000,000-byte budget and takes the newest contiguous suffix of **whole contest
dates**. Adding the next older date, January 18, would add 6,798,922 bytes and exceed
that budget. No problems are selected by difficulty, platform, or test size within a date.

`livecodebench/data/manifest.json` records the exact original `question_id` list,
window, platform counts, selection rule and SHA-256 checksums. IDs are never renumbered.
Data loads through `importlib.resources` from the installed wheel: no checkout,
Hugging Face dependency, dataset cache, or runtime download is needed.

A frozen recent window reduces exposure to older benchmark material; it does not
prove freedom from contamination. April 2025 is the upstream endpoint available at
packaging time, not a claim that these are September 2026 problems.

## Prompt and score

The system message and user template are the official **generic/OpenAIChat** variant,
copied verbatim from `PromptConstants.SYSTEM_MESSAGE_GENERIC` and
`get_generic_question_template_answer` in
[`lcb_runner/prompts/code_generation.py`](https://github.com/LiveCodeBench/LiveCodeBench/blob/28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24/lcb_runner/prompts/code_generation.py),
commit `28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24`. Both the starter-code and stdin/stdout
instructions, Python fences, headings, spaces and newlines are preserved. The model
receives only the original statement, optional starter code, and those instructions.
Original examples embedded in the statement remain intact to preserve the official
prompt; structured public/private tests and outputs stay only in the scorer closure.
Sample metadata is an allowlist of problem identity, platform, date and difficulty.
No reference answer is placed in the sample target. The package does not choose
upstream model-specific prompt variants or override the model's generation settings.

The scorer extracts the **last closed `python` fenced block**. Each test uses three
bounded sandbox execs: setup atomically creates an unpredictable `/tmp/lcb-*`
directory and a request file, then a supervisor reads and unlinks that request,
writes the candidate once, and executes it once in that fresh directory. A separate cleanup exec then kills
every process with the reserved candidate UID before another test starts. There are
no unbounded `write_file` operations or reused candidate-writable launch paths.
The candidate has a **six-second timeout** by default, including process startup;
`per_test_timeout` overrides it. Setup has a five-second container deadline. The run
has an explicit container `timeout -s KILL`, an Inspect exec timeout, and an outer
async deadline, with five seconds of supervisor overhead beyond the candidate limit.

The trusted Linux supervisor runs as root and captures stdout/stderr in anonymous
files, obtaining the exit status from `wait()`. Before exec it clears supplementary
groups and irreversibly sets all real/effective/saved GIDs and UIDs to **65532**,
with `no_new_privs` and no retained capabilities. Only the per-test `candidate/`
subdirectory is candidate-owned; its unpredictable parent stays supervisor-owned.
A hard zero `RLIMIT_NPROC` prevents candidate forks and threads. The different UID
prevents the candidate from signaling the supervisor or the outer timeout process.

The candidate starts in its own session. The supervisor sends **SIGTERM then SIGKILL
to the whole process group**, and reaps the child on completion or timeout. Cleanup
also has an independent enforcement path: the scorer always issues a separate
sandbox exec running `/usr/bin/pkill -KILL -u 65532`, including on setup failure,
execution failure, missing/invalid receipts and cancellation. If no authenticated
receipt arrives, the scorer waits through the run's host deadline (candidate limit
plus ten seconds) before sweeping, even if the provider returned early. An
authenticated completion permits an immediate sweep. The cleanup exec has its own
five-second container/provider deadline and ten-second host deadline; cleanup failure
aborts scoring rather than starting another test. UID 65532 must be reserved solely
for candidates in a per-sample sandbox; tests within that sandbox run sequentially.
No parent-death signal or other candidate-controlled setting is used for cleanup.
A test without an authenticated supervisor report is **INCORRECT**, with reason
`supervisor did not complete`.

A per-test HMAC-SHA256 receipt authenticates the actual child exit code, timeout,
overflow, output bytes and directory. The setup process generates the key locally;
no ancestor shell receives it as input or an argument. The request file is deleted
before candidate execution, and `PR_SET_DUMPABLE=0` protects the supervisor's key
and file descriptors in addition to the UID separation. Candidate stdout goes to its own file,
not the provider's control stream. Printing a provider completion marker cannot
forge a passing receipt: provider `success` and `returncode` are never verdicts.
Expected outputs stay on the host and are never sent to the sandbox. Model code
never executes in the harness process.

The comparison and functional adapter are ported from
[`lcb_runner/evaluation/testing_util.py`](https://github.com/LiveCodeBench/LiveCodeBench/blob/28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24/lcb_runner/evaluation/testing_util.py)
at the same commit:

- **stdin:** `get_stripped_lines` trims the whole output and each line, preserving
  internal blank lines and line count. Each corresponding line must match exactly,
  or all its whitespace-separated tokens must be equal as `Decimal` values.
  There is no approximate float tolerance, line reordering, or general text token matching.
- **functional:** the upstream standard-library import prelude and recursion limit
  are provided. One JSON value per input line becomes one positional argument.
  The named `func_name` is invoked on `Solution()` when the upstream class-detection
  rule applies, otherwise on the module. Debug stdout is suppressed. A literal-only
  return-value transport preserves nested tuple/list distinctions; the host uses
  `ast.literal_eval`, never `eval`. As upstream does, only a top-level tuple becomes
  a list, followed by Python equality with the JSON-decoded expected output.
- Any wrong answer, timeout, output overflow or nonzero exit is incorrect. Explanations
  identify the failing test and reason without including tests, stdout or stderr.
  Missing code or an absent supervisor report is incorrect. Corrupt packaged data,
  setup infrastructure errors and failed independent cleanup raise sanitized errors.

Private test decoding follows `CodeGenerationProblem.__post_init__` in
[`lcb_runner/benchmarks/code_generation.py`](https://github.com/LiveCodeBench/LiveCodeBench/blob/28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24/lcb_runner/benchmarks/code_generation.py):
JSON, or base64 → zlib → pickled JSON → JSON. Our unpickler rejects executable globals.

**Comparison limits:** this is a date subset, not the full cumulative leaderboard set.
The requested real-stdin subprocess design differs from upstream's AST wrapper and
mock stdin; each test also gets a fresh module/`Solution` rather than upstream's
reused object. Candidates cannot create subprocesses or threads. Output is limited
to 1 MiB per stream. Startup counts toward the timeout, and sandboxes have 1 CPU/1 GiB RAM.
These execution differences, model sampling settings and hardware should accompany
reported scores. Matching the generic prompt and comparison rules does not guarantee
numerical equivalence with every leaderboard run.

## Install and run with Docker

```bash
python -m pip install '.[inspect]'
inspect eval livecodebench/livecodebench \
  --model <provider/model> -T sandbox_type=docker
```

Docker must be available. Compose builds the packaged `livecodebench/Dockerfile`
as `eval-livecodebench-sandbox:local` from `python:3.12-slim`, installing `procps`
for `/usr/bin/pkill`, `util-linux` for `dmesg`, and `hostname` for provenance,
and reserving UID/GID 65532 as `lcb-candidate`. Image building
requires network access; the running container uses `network_mode: none`.
The image must provide `/usr/local/bin/python3`, GNU `timeout`, procps `pkill`, and
a root-owned interpreter/system tree that UID 65532 cannot modify. Numeric IDs are
fixed in the wrapper and cleanup command, so a custom image must reserve the same IDs.
Evaluation data is in the wheel, not the image.

Trusted setup, supervision and independent cleanup execs run as UID/GID 0 with
all capabilities dropped except `SETUID`, `SETGID`, `KILL`, `CHOWN` and `DAC_OVERRIDE`
(to drop candidate credentials, signal across UIDs, and manage candidate-owned
scratch files). Privilege escalation remains disabled. The candidate receives none
of these capabilities. Container memory and process limits remain in force.

The distribution is `eval-livecodebench`, the module is `livecodebench`, and the
`inspect_ai` entry point imports that module for cold task discovery. Python >=3.11
is supported. The `[inspect]` extra pins `inspect_ai==0.3.260`; `[anyeval]` also pins
`inspect-evals==0.19.0` and `inspect-k8s-sandbox==0.13.0`. These are AnyEval's resolved
installed versions; its requirements specify ranges for the first two. This task
does not depend on, import, or wrap `inspect_evals`' LiveCodeBench-Pro implementation.

## Kubernetes and AnyEval

The default Kubernetes configuration targets the GKE Autopilot cluster
`anyeval-sandbox` and namespace `anyeval-sandbox`, using the packaged Pod-based
AnyEval chart and **0.13.0** provider values schema. It creates one Python Pod and
one native, namespace-wide deny-all NetworkPolicy with empty ingress and egress
(including DNS). The cluster has no `CiliumNetworkPolicy` kind.

The Pod uses `runtimeClassName: gvisor` (handler `gvisor`) and the sole node selector
`cloud.google.com/gke-spot: "true"`. Autopilot Spot nodes scale from zero, so initial
scheduling can wait for provisioning. Custom node labels are not accepted. The Pod
template deliberately omits tolerations: Autopilot injects the Spot toleration.
It disables service-account token mounting and uses `restartPolicy: Never`.

Requests equal limits: **1 CPU, 1 GiB memory, and 1 GiB ephemeral storage**. This
meets the 250m CPU minimum and the permitted 1:1–1:6.5 CPU-to-memory ratio; Autopilot
would overwrite unequal limits. The disk allocation gives headroom for sequential
`/tmp/lcb-*` request/input files, candidate scratch files and the two captured
streams (each capped at 1 MiB). Normal completion removes each test directory;
ephemeral storage also accounts for the writable container layer and logs. This
is a scheduling/eviction budget, not a per-file quota. There are no hostPath mounts,
privileged containers or host networking.

Trusted execs run as UID/GID 0, permitted for these gVisor Pods, retaining only
`SETUID`, `SETGID`, `KILL`, `CHOWN` and `DAC_OVERRIDE` from Autopilot's default allowed
set. Privilege escalation is disabled and seccomp uses `RuntimeDefault`. Candidates
irreversibly drop to UID/GID 65532 with no capabilities.

Cloud Build builds `livecodebench/Dockerfile` and publishes the worker's sandbox
image as
`us-central1-docker.pkg.dev/openevalz-sbx-84737/openevalz/eval-livecodebench-sandbox:1.0.0`.
The tag in `values.yaml` matches the package version in `pyproject.toml`; update both
when releasing. **AnyEval pins the image by digest in its catalog**, using the
resolved Artifact Registry digest. Publish the image before evaluating; the worker
must be able to pull it from that registry.

```bash
python -m pip install '.[anyeval]'
inspect eval livecodebench/livecodebench --model <provider/model>
```

The task defaults `INSPECT_K8S_DEFAULT_NAMESPACE` to `anyeval-sandbox` when unset;
the provider exposes namespace selection only through that setting. An explicit
namespace setting is retained. The verified cluster provides the namespace and
gVisor RuntimeClass; Autopilot
provisions Spot nodes and enforces the native network policy. AnyEval's
existing namespace-wide `deny-all-egress` policy remains in force as well.

`-T anyeval_chart=false` explicitly selects the provider's built-in Cilium chart
for other clusters. It requires Cilium CRDs and includes a CoreDNS sidecar; it is
not the AnyEval deployment path. Docker selection needs only `-T sandbox_type=docker`.

The provenance hook runs `uname -r`, `dmesg` and `hostname` **inside the sandbox
container** as its configured user (root). It requires the boot line
`[    0.000000] Starting gVisor...` from `dmesg`. The Dockerfile explicitly installs
`util-linux` and `hostname` and checks their executables during the build. With no
local Docker available, executable locations were verified against Debian's
[util-linux file list](https://packages.debian.org/trixie/amd64/util-linux/filelist)
and [hostname file list](https://packages.debian.org/trixie/amd64/hostname/filelist);
reading the live gVisor boot log still requires a cluster run.

The hook must also verify the **live** kernel, resolved image digests, node and
selecting policies; values alone do not prove containment. Record the resolved
custom sandbox image digest per run.

## Build, test and publication

```bash
python scripts/build_dataset.py           # pinned fetch, only during maintenance
python scripts/build_dataset.py --offline # reuse ignored .build/latest.jsonl.gz
python -m pip install '.[test]'
python -m pytest -q
python -m pip wheel --no-deps . -w dist
```

The build script writes only inside the repository, streams the upstream download
into an ignored gzip cache, and never executes the upstream dataset builder. No raw
download is committed. A normal wheel build uses the already committed artifact and
performs no dataset fetch. Tests need neither network nor Docker; scorer tests use a
fake sandbox, and adapter tests execute only authored synthetic fixtures in child Python
processes. Pytest temporary files stay under `.build/`; authored supervisor fixtures
also create and remove isolated `/tmp/lcb-*` directories. On macOS, those fixtures
stub Linux `prctl`, credential changes and group signaling; these are mechanics tests, not evidence
of UID isolation. Linux containment regressions are skipped off Linux with explicit
reasons. Run them as root **inside a disposable Linux sandbox** with the image
requirements above, an otherwise-unused candidate UID 65532, and
`LCB_LINUX_CONTAINMENT=1`. They verify blocked forks and supervisor-memory access,
denied candidate signals to the supervisor after clearing `PDEATHSIG`, zero
candidate capabilities, and termination by a separate UID-wide exec after the trusted
test driver forcibly kills the supervisor. The scorer must return `supervisor did
not complete`, no candidate may remain runnable, and a fresh subsequent test must pass.
Linux/Docker containment has not been exercised on the macOS development host.

`run.py` mirrors the reference package's one-problem contract. Pass one literal
`--sample-id`, `--model`, `--scaffold baseline` and optional `--token-limit`;
`--sandbox-type docker` selects Docker. Its Kubernetes path uses the AnyEval chart.
It rejects globs and unknown ids before model spend, then writes `bundle.json`
containing only identity, status and score, without test material. The standalone
bundle leaves serving receipts and live sandbox provenance absent; the AnyEval
application must supply and verify those before publication.

`anyeval.json` supplies discovery metadata. `redaction.yaml` records the enforced
publication contract using schema `version: 1`, exact source test keys under
`redact`, general answer keys under `global`, and
`sandbox_exec: [input, stdout, stderr]`. Tests derive sensitive key coverage from
loaded records and samples; current sample metadata has no answer-bearing keys.
AnyEval's current exporter does **not** load that YAML; it
recursively scrubs recognized answer-key names, including targets, score answers
and explanations, but cannot strip generic sandbox input/output fields or the old
`public_test_cases` / `private_test_cases` keys. The package therefore prevents those
fields from entering logs: tests live in the scorer closure; grading uses a dedicated
Inspect proxy with events disabled; provider diagnostics are filtered within the
grading context; infrastructure exceptions are replaced outside the private frame,
without their original chain or private traceback locals. These private Inspect
interfaces are version-pinned and covered by tests using the real event proxy and,
when available, AnyEval's real `redact_export`.

Published transcripts retain the original statement and its embedded examples,
starter code, model messages and candidate attempt, allowlisted problem metadata,
scalar verdict, model usage/cost/timing, and non-grading live sandbox provenance.
They carry no added structured test inputs, expected outputs, candidate execution
output, receipt key, or grading sandbox events. Embedded examples are already in
the official prompt; the candidate attempt is model-generated, not a dataset
reference solution or a structured test answer key.

Package code is Apache-2.0; adapted upstream portions retain their MIT notice.
**Dataset licensing is ambiguous upstream**: the card says `cc` without a variant,
while the builder says MIT. See `NOTICE.md`; no Apache or particular CC license is
asserted for the original platform problems and tests.
