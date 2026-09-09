# Staging gateway qualification — 2026-09-09

**Result:** paid Pro is working. DeepSeek/DeepInfra, Qwen/Alibaba and Kimi/Alibaba each passed
all three live schemas. The sixteen HTTP dispatches cost **$0.03845786** in total; conservative
retained exposure was **$0.851151**, below the $1 limit. The shared worker remains disabled until
the demonstrated accounting and prompt fixes deploy through the follow-up PR.

## Deployment and account

PR #24 merged as `0c73420b88bf5056cd1fd19cc0d4ca9164a8373b` at 09:49:59 UTC.
The web container started at 09:51:45 UTC and applied migrations and seed rows. The pipeline
started at 09:56:25 UTC with PydanticAI 2.40.0, the gateway key present, and `HARNESS_ENABLED=0`.
The key is absent from the web application and its container. The original deployed gateway
module hashes to `673343ff04fd8f4e86383becea63e208cd8373ab5c03e10998b9139dc8a09f20`.

The initial authenticated credits read succeeded, but the first bounded synthetic inference
request received HTTP 403: Pro Trial does not permit per-request zero data retention. This
confirmed the [documented plan requirement](https://vercel.com/docs/ai-gateway/security-and-compliance/zdr).
The setup guidance had omitted it. Gateway credits and a valid API key do not activate that plan.

Rajesh activated paid Pro. At 10:30:23 UTC a new DeepSeek/DeepInfra request succeeded with
`store=false`, native strict JSON, `zeroDataRetention=true`, and an exact provider allowlist.
Its identity-matched generation receipt reports **$0.00000338**. The account restriction is
resolved; no privacy control was relaxed. Credits rose from $5 to $15 after Rajesh's account change.

## Live findings and corrections

The follow-up uses isolated containers on the staging VPS, the deployed dependency image, and
hash-identified, read-only mounts of the reviewed fixes. The shared worker has not been hot-patched.
Its key stays on the server and is absent from code, command arguments, reports and model inputs.

1. **Integer monetary fields stopped settlement.** The real gateway returned
   `upstream_inference_cost: 0`. Strict Pydantic validation expected Decimal, while the JSON loader
   converted only fractional numbers. The fix converts integer monetary values to Decimal without
   a float round trip and continues to reject booleans. The saved summary generation reconciled
   read-only at **$0.0000505**, rounded upward to 51 micro-dollars; no repeat inference was needed
   to recover that charge.
2. **Token details used obsolete field names.** The adapter now reads the documented
   `tokens_prompt`, `tokens_completion`, and `native_tokens_*` fields, retaining normalized,
   native, reasoning and cache counts. The [generation API](https://vercel.com/docs/ai-gateway/sdks-and-apis/rest-api#look-up-a-generation)
   and actual receipts confirm the shape. Generation/model/provider identity checks remain strict.
3. **Visible word anchors looked like expandable ranges.** DeepSeek returned valid summary JSON
   but inferred intermediate word IDs. Summary/proposal prompt v3 explicitly requires copying only
   visible anchors verbatim, including during hierarchy reduction. The deterministic grounding
   check stays strict. DeepSeek then passed all three schemas.
4. **Provider metadata did not establish compatibility.** Kimi/Wafer returned the wrong schema.
   Gemini/Vertex rejected the integer enum used by the version field with HTTP 400. Both pairings
   remain excluded; neither failed artifact is relabeled as a pass.
5. **Failed HTTP requests lacked useful evidence.** The qualifier now retains bounded, allowlisted
   type/code/message receipts with credential redaction, hashes and sizes. Arbitrary response fields,
   headers and raw request bodies are excluded from those error receipts.

## Route observations

These are small authored-input transport checks, not editorial model auditions. Every passed
stage has a saved SDK response, strict output/grounding validation, exact routed identity, and a
reported non-BYOK charge. PydanticAI's response provider label `openai` identifies the compatible
adapter; the actual upstream identity comes from the matching gateway generation receipt.

| Model/provider | Summary / proposal / verifier | Exact cost of the successful three-stage set | Status |
| --- | --- | ---: | --- |
| Qwen3.8-27B / Alibaba | 3 / 3 passed | $0.0084240 | Transport passed on prompt v2 |
| DeepSeek-V4-Flash / DeepInfra | 3 / 3 passed | $0.0001462 | Transport passed on prompt v3 |
| Gemini3.8-Flash / Vertex | Summary rejected | — | Integer enum unsupported by this path |
| Kimi K3 / Alibaba | 3 / 3 passed | $0.0261960 | Transport passed on prompt v3 |
| Kimi K3 / Wafer | Summary failed validation | — | Native response did not conform to the schema |

Qwen and DeepSeek are owner-verified open-weight models; their earlier metadata flags were
conservatively unverified, not evidence of closed weights. Primary records:
[Qwen weights and Apache-2.0 license](https://huggingface.co/Qwen/Qwen3.8-27B),
[DeepSeek weights and MIT license](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash).
Kimi's [owner repository](https://github.com/MoonshotAI/Kimi-K3) publishes its weights and license.

## Budget and evidence discipline

Every finite batch freezes its metadata and caps before dispatch. Admission is persisted before
network I/O; SDK/model retries are disabled. Saved responses precede schema validation. Pending
cost ingestion receives a bounded read-only lookup window. Uncertain outcomes, identity mismatches
and unresolved charges halt the batch. Reconciliation reads a retained generation only and never
resumes paid work or changes incomplete validation into a pass.

The cumulative conservative exposure cap is **$1**, including rejected and failed attempts.
A new journal does not reset that cap: each subsequent operator plan explicitly subtracts earlier
admissions. Deliberate requalification followed the paid-plan activation, demonstrated parser and
prompt fixes, or a separately reviewed provider change. No automatic inference retry was enabled.
Every failed response and reservation remains retained. The final plan allowed at most sixteen
HTTP dispatches across the explicitly reviewed batches; sixteen occurred, including three rejected
requests. No inference retries happened inside any batch. At 10:52:42 UTC, the authenticated credits
endpoint reported total usage **$0.03845786** and balance **$14.96154214**, matching the retained
exact generation charges. Summing per-call upward-rounded costs gives 38,461 micro-dollars.

| Retained artifact | SHA-256 |
| --- | --- |
| Initial entitlement admission | `8030549a76afafed06c143dc11467c29f096ce95c8ee0123ba9a651955fad636` |
| Initial Pro Trial rejection | `e397b7e556a0911df279eb30b8ff9cea98e126884a9fab823d8666719c6462b3` |
| Successful paid-Pro entitlement receipt | `ceee3f89d2a9ff12611933a6b5ba01d1cc3d1057887291533a9a83ce0888d3c2` |
| Entitlement cost and credits | `d2f2a75d665ea1dd2faae418c3c0eb13f7b7627120131b06f8be04264b9615a7` |
| Read-only integer-money reconciliation | `ed713d3d6152feb9afcde5a7ac87e1c6c6c33f0c3a661056b7c728a562a42f68` |
| Corrected-money batch journal/report | `b630d108fbe0a57d27775372570556f24607a4fac4471de50ea62ecb40197fff` |
| Prompt-v3 batch journal/report | `aacfa22a06947d0991d159b917303c960eb52badac087f33e608ad8f14221978` |
| Kimi/Alibaba journal/report | `3caa0ab0abea5e7ff6067757a8958483108580cf727c71d7552e6f13c48a4cc1` |
| Final credits and disabled-worker check | `81458752e228ca91e13b13484e95e52340e416bd92958d7c157ff66c97268f2f` |
| Extracted DeepSeek route proof | `877d56a2045d1bda104f3f5ac186299a7bc8c8adaa06ce953dfff10c25283aea` |
| Extracted Kimi/Alibaba route proof | `ad1727c90c1b32e96c374d021cd638a44ab2bd44c5c47a439a1ef2beb615029c` |
| Extracted Qwen route proof | `81b436b1f62dca6d1feb9aa9122d45d8e2af7d68955b1377e81469cc08ebceee` |

Private manifests bind candidate metadata, code hashes, request hashes, response hashes/sizes,
generation identities and cost observations. Qualification-only route IDs use a reserved prefix
that cannot enter a production route snapshot. No production routing order or model winner has
been selected from these transport checks.

## Verification and remaining work

The focused gateway, qualifier, CLI and workflow suite passes **48 tests**. Ruff passes on the
changed adapter, runner and prompts; the runner's Pyright check passes. The required complete
release gate and exact-SHA GitHub check establish delivery for the final follow-up commit.

Two retained 151-minute staging recordings have ready transcripts. A later chapter test can reuse
one without another ingest or GPU transcription. No real-source chapter workflow, human boundary
labels, correction-time measurement or matched-budget seat audition has run in this qualification.
Chapter-Llama remains an optional later challenger. The corrected adapter must deploy through the
follow-up PR before enabling a shared worker; route qualification and a reviewed staging test
configuration are separate prerequisites. See the [runbook](../runbooks/chapter-harness.md).
