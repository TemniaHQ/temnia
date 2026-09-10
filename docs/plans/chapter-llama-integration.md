# Chapter-Llama candidate integration — 2026-09-10

This implements an explicit Chapter-Llama topic proposer beside Temnia's existing
chapter planner. The gateway still composes the brief-aware global proposal and
Temnia's compiler still owns physical cut points. A Chapter-Llama timestamp is
mapped to an existing source-evidence sentence ID; it never becomes a direct cut
command. In particular, topic prediction cannot prove the end of an uploaded
excerpt contains complete speech.

## Choice and exact model identity

Fresh comparison: the existing live harness starts from SaT sentences; KernelCPD
is a useful unsupervised embedding-change candidate in the segmentation evaluator.
Chapter-Llama is a fine-tuned semantic chapter-and-title proposer, so it enters as
an optional candidate with its own recorded output and evaluation row. No model
is promoted by installation or by successful loading.

The supported first variant is the upstream ASR-only 10k-step adapter. It consumes
existing timestamped transcript sentences and performs no media download,
transcription or frame captioning. The paper's combined frame-caption/ASR result
is not a measured result for this text-only integration.

| Input | Immutable identity |
| --- | --- |
| Base and tokenizer | `meta-llama/Llama-3.1-8B-Instruct@0e9e39f249a16976918f6564b8830bc894c89659` |
| Adapter | `lucas-ventura/chapter-llama@a3837e908f05eb3697873acba2870b2370b1e92d` |
| Adapter directory | `outputs/chapterize/Meta-Llama-3.1-8B-Instruct/asr/default/s10k-2_train/default/model_checkpoints` |
| Upstream source reviewed | `lucas-ventura/chapter-llama@d19a77efcf583f63771de052267fdeea016510e9` |
| Inference | Torch `2.14.0`, Transformers `5.16.1`, PEFT `0.20.0` |
| Wire protocol / prompt | `temnia-chapter-llama/1` / `chapter-llama-asr/1` |

Direct Transformers and PEFT avoid pulling upstream training orchestration,
Lightning, Hydra and experiment tracking into the pipeline. The existing
Transformers pin is retained: 5.17.0 had been released less than 24 hours before
research, while PEFT 0.20.0 was released July 28. Setup downloads allowlisted files
at exact commits; runtime checks their local paths and passes `local_files_only`.
The CPU worker does not install a CUDA runtime or load this 8B model.

Sources: [official repository](https://github.com/lucas-ventura/chapter-llama/tree/d19a77efcf583f63771de052267fdeea016510e9),
[adapter model card](https://huggingface.co/lucas-ventura/chapter-llama/tree/a3837e908f05eb3697873acba2870b2370b1e92d),
[base model card and license](https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct/tree/0e9e39f249a16976918f6564b8830bc894c89659),
[CVPR paper](https://openaccess.thecvf.com/content/CVPR2025/papers/Ventura_Chapter-Llama_Efficient_Chaptering_in_Hour-Long_Videos_with_LLMs_CVPR_2025_paper.pdf),
[PEFT loading documentation](https://huggingface.co/docs/peft/main/en/package_reference/peft_model),
[Transformers loading documentation](https://huggingface.co/docs/transformers/main_classes/model).

The adapter/code is MIT; the base is under the Llama 3.1 Community License and
acceptable-use policy. The adapter MIT notice is vendored and the base license and
policy are required downloaded files; the third-party notice includes attribution
and “Built with Llama.” The owner of Modal secret `temnia-hf` must hold gated base
access. The September 10 metadata probe initially returned 403 for account
`rkpattanaik`; after Rajesh confirmed access, the same secret returned 200 for the
pinned config and first weight shard. No token was printed or copied locally.

## Durable execution and accounting

`HARNESS_CHAPTER_LLAMA_CONFIG_JSON` is an explicit deployment configuration; absence
disables the candidate. The configuration is frozen into the new run's existing
route-snapshot wrapper as `chapterLlama`, preserving old public run-config hashes
and old continuations. Recorded backend configuration refuses enabling live GPU
inference. The workflow calls `generate_chapter_llama_candidate` after evidence
and supplies the verified candidate hints only to the final global proposal,
including its artifact dependency before context-size/cost estimation.

The isolated Modal app is `temnia-chapter-llama`, explicitly resolved in the
`staging` environment. Its deployment identity returns protocol, source/build
fingerprint, complete model configuration and resource profile. The worker checks
that identity before a new physical attempt. Already dispatched attempts recover
from their immutable checkpoint or retained handle without requiring a still-live
old deployment identity endpoint.

Each operation reserves the configured allocation window in the existing scoped
ledger and wins the dispatch compare-and-swap before spawning. The GPU function
then takes an R2 create-only admission before loading model bytes. A restarted
container sees the admission and cannot execute the model again. Both successful
and known failed computations create immutable terminal checkpoints; malformed
or truncated generations retain the raw paid output and token counts. A lost
spawn response, missing handle, cancelled observation or uncertain transport
retains the physical attempt and its exposure. It never authorizes redispatch.

The initial unquantized allocation is one L40S, four physical CPU cores, 32 GiB
RAM, one container, one-hour timeout, no application retries, single-use
containers. September 10 list prices are $0.000542/GPU-second,
$0.0000131/core-second and $0.00000222/GiB-second: a combined 665.44 microUSD/second,
or 2,395,584 microUSD reserved for the full configured execution window. Measured
execution time gets a clearly labelled estimate. Modal has not returned an invoice
through this protocol, so actual cost stays unknown and the reservation remains
active; cold-start/restart overhead is not falsely represented as zero. This is
an attributable allocation estimate, not a guaranteed invoice ceiling.
[Modal pricing](https://modal.com/pricing), [GPU guide](https://modal.com/docs/guide/gpu).

## Setup and explicit audition

Run from `apps/pipeline`. The optional local extra adds PEFT without changing the
worker's normal dependency set:

```sh
uv sync --extra chapter-llama --group dev
# Optional local inference setup, using an authorized HF_TOKEN:
uv run python scripts/fetch_chapter_llama.py

MODAL_ENVIRONMENT=staging uv run modal deploy -m temnia_pipeline.modal_chapter_llama_app
```

Call the deployed CPU `prepare_models` function once to prefetch and commit the
separate `temnia-chapter-llama-models` volume. Inference has the existing `temnia-r2`
secret but no Hugging Face token. Capture the configuration from the deployed
identity rather than typing a guessed build hash:

```python
import json
import modal

app = "temnia-chapter-llama"
environment = "staging"
prepare = modal.Function.from_name(app, "prepare_models", environment_name=environment)
print(prepare.remote())
identity = modal.Function.from_name(app, "deployment_identity", environment_name=environment)
configuration = {
    "app_name": app,
    "environment": environment,
    "deployment": identity.remote(),
}
print(json.dumps(configuration, separators=(",", ":")))
```

The second JSON is the **complete value** of
`HARNESS_CHAPTER_LLAMA_CONFIG_JSON`. It contains `deployment.protocol`, `build`,
`config` (the exact model pins above, adapter path, prompt version,
`max_input_tokens:35000`, `max_new_tokens:2048`) and `resources` (the exact dated
profile above). Set it only for an explicitly selected candidate run after the
live audition; capture a new identity after a deployment change.

For a standalone audition, preserve the original source/organization IDs:

```sh
uv run temnia-chapter-llama input transcript.json --output input.json
uv run temnia-chapter-llama spawn input.json --environment staging \
  --organization ORGANIZATION_UUID --source SOURCE_UUID --state attempt.json
uv run temnia-chapter-llama status attempt.json --output outcome.json
```

`attempt.json` is create-only dispatch intent; `attempt.json.handle` is the retained
remote handle. An intent without a handle means inspect that attempt's admission
and checkpoint, not repeat `spawn`. `status` observes one known handle; repeat
observation while running without another spawn. All output paths refuse
replacement. `outcome.json` contains the full success/failure envelope; save its
`result` object separately for the recorded segmentation evaluator:

```sh
uv run temnia-eval segment transcript.json \
  --segmenter legacy chapter-llama:result=result.json
```

Recorded evaluation requires the exact same source text, sentence IDs/times,
duration and model configuration. Production candidates instead use the run's
already immutable evidence sentences, avoiding a second segmentation step.

## Verification and promotion gate

Contract tests cover strict timestamp parsing, source/model/mapping identity,
missing offline weights, no model load for empty input, immutable admission,
known failed output retention, ambiguous CLI dispatch and the existing synthetic
segmentation comparison. Real Postgres lifecycle tests inject a lost spawn
response after checkpoint completion and prove one dispatch for both success and
known failure, including reuse after deployment disappearance and unknown invoice
reservation retention. These are engineering checks; they do not establish
editorial quality.

Promotion requires a real pinned-weight result on the four-minute source, recorded
raw predictions and token/time provenance, followed by comparison with the current
proposal on topic coherence and independently reviewed boundary windows. Candidate
quality does not override the intelligent harness's source-edge check, semantic
review or artifact verification. A successful load alone does not enable default
routing.

### First real audition

After HF access was granted, the first isolated L40S attempt succeeded using the pinned base and
adapter snapshots. The 240.040-second source supplied 51 original sentence IDs: 1,064 input tokens,
32 output tokens, 41.147 seconds model load and 4.236 seconds generation, 45.434 seconds measured
compute in total. The listed allocation estimate is $0.030234; the invoice remains unknown.
Create-only admission and the terminal checkpoint retain the physical attempt.

The proposed starts were 0:00, 1:57 and 3:23, mapped to `s000000`, `s000021` and `s000047`.
Compared with the existing 58.960/160.560-second cuts, the model combines the opening teaser and
host introduction and then separates the interviewer's karma question from the guest's answer.
One proposed name also differs from the supplied ASR spelling. The existing edit is a comparator,
not annotated gold, but this first result does not justify changing the default route. Topic
prediction also leaves the incomplete final source sentence for the separate editorial program
to handle. The integration is runnable and remains an explicit evaluated candidate.
