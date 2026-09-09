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

The scorer extracts the **last closed `python` fenced block**. Each test uses two
bounded sandbox execs: setup atomically creates an unpredictable `/tmp/lcb-*`
directory and a request file, then a supervisor reads and unlinks that request,
writes the candidate once, and executes it once in that fresh directory. There are
no unbounded `write_file` operations or reused candidate-writable launch paths.
The candidate has a **six-second timeout** by default, including process startup;
`per_test_timeout` overrides it. Setup has a five-second container deadline. The run
has an explicit container `timeout -s KILL`, an Inspect exec timeout, and an outer
async deadline, with five seconds of supervisor overhead beyond the candidate limit.

The nonroot Linux supervisor captures stdout/stderr in anonymous files and obtains
the exit status from `wait()`. A hard zero `RLIMIT_NPROC` prevents candidate forks
and threads; `no_new_privs` prevents privilege gains. The candidate starts in its own
session, is killed and reaped on timeout, and receives `SIGKILL` if its supervisor
dies. The supervisor also kills the original process group before returning a receipt.
Thus a previous candidate cannot leave a process to poison the next test's setup.

A per-test HMAC-SHA256 receipt authenticates the actual child exit code, timeout,
overflow, output bytes and directory. The setup process generates the key locally;
no ancestor shell receives it as input or an argument. The request file is deleted
before candidate execution, and `PR_SET_DUMPABLE=0` protects the supervisor's key
and file descriptors from the same-UID child. Candidate stdout goes to its own file,
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
  Missing code is incorrect. Corrupt packaged data and infrastructure failures raise
  errors instead of silently counting as model failures.

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

Docker and the `python:3.12-slim` image must be available; the initial image pull
requires network access, but the running container uses `network_mode: none`.
It runs as UID/GID 1000, drops capabilities, disallows privilege escalation and limits
memory/process count. Evaluation data is in the wheel, not the image.

The distribution is `eval-livecodebench`, the module is `livecodebench`, and the
`inspect_ai` entry point imports that module for cold task discovery. Python >=3.11
is supported. The `[inspect]` extra pins `inspect_ai==0.3.260`; `[anyeval]` also pins
`inspect-evals==0.18.0` and `inspect-k8s-sandbox==0.13.0`. These are AnyEval's resolved
installed versions; its requirements specify ranges for the first two. This task
does not depend on, import, or wrap `inspect_evals`' LiveCodeBench-Pro implementation.

## Kubernetes and AnyEval

The default Kubernetes configuration uses the packaged AnyEval chart and values.
It creates one Python Pod and a standard namespace-wide deny-all NetworkPolicy,
with no Cilium resources or DNS sidecar. It requests `python:3.12-slim`,
`runtimeClassName: gvisor`, node selector `anyeval.io/tier: sandbox`, no service-account
token, nonroot execution and `restartPolicy: Never`, matching AnyEval's
`sandbox-pod-template.yaml`. The values also follow the **0.13.0** provider schema.

```bash
python -m pip install '.[anyeval]'
inspect eval livecodebench/livecodebench --model <provider/model>
```

The task defaults `INSPECT_K8S_DEFAULT_NAMESPACE` to `anyeval-sandbox` when unset;
the provider exposes namespace selection only through that setting. An explicit
namespace setting is retained. The cluster must provide the namespace, gVisor
runtime, sandbox node pool and enforcing network-policy implementation. AnyEval's
existing namespace-wide `deny-all-egress` policy remains in force as well.

`-T anyeval_chart=false` explicitly selects the provider's built-in Cilium chart
for other clusters. It requires Cilium CRDs and includes a CoreDNS sidecar; it is
not the AnyEval deployment path. Docker selection needs only `-T sandbox_type=docker`.

The provenance hook must still verify the **live** kernel, resolved image digests,
node and selecting policies; values alone do not prove containment. The Python image
tag is deliberately the requested plain image, so record its resolved digest per run.

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
stub Linux `prctl` calls, and the real Linux containment test is skipped.

`run.py` mirrors the reference package's one-problem contract. Pass one literal
`--sample-id`, `--model`, `--scaffold baseline` and optional `--token-limit`;
`--sandbox-type docker` selects Docker. Its Kubernetes path uses the AnyEval chart.
It rejects globs and unknown ids before model spend, then writes `bundle.json`
containing only identity, status and score, without test material. The standalone
bundle leaves serving receipts and live sandbox provenance absent; the AnyEval
application must supply and verify those before publication.

`anyeval.json` supplies discovery metadata. `redaction.yaml` records the enforced
publication contract. AnyEval's current exporter does **not** load that YAML; it
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
