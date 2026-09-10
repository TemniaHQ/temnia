# Deferred work from the editorial intelligence audit

September 10, 2026. Rajesh asked to stop cycling through minor fixes and focus on independently
publishable topic videos. This is a holding list, not an implementation queue for the current work.
The [research](../design/standalone-topic-intelligence-2026-09-10.md) and
[focused plan](standalone-topic-intelligence.md) own the core changes.

| Item | Evidence/status | Why deferred | Revisit trigger |
| --- | --- | --- | --- |
| More precise proposal-anchor diagnostics | Current replacement receives a generic grounding failure, rather than a precise field-level repair. Two full Karma proposals supplied foreign anchors. | The new semantic contract should first eliminate redundant copied fields. Do not polish the old wire before deciding which fields survive. | A retained field causes an otherwise comparable experiment to fail. |
| Global versus local uncertainty wording | Full Karma carries a global speech-coverage flag across boundaries; only three cuts intersect detector intervals and none crosses an aligned word. | Warning presentation is separate from the missing semantic judgment. | After the topic contract stabilizes; earlier only if it corrupts evaluator input. |
| Acoustic calibration at 34:50.200, 38:27.680 and 42:07.640 | Saved VAD intersections are listening risks, not proven clipping. | Do not tune thresholds from detector disagreement alone or treat milliseconds as a cure for a missing answer. | Listen to actual rendered edges during acceptance; address any demonstrated failure. |
| Mixed instruction-required and repairable findings | Current workflow can stop a repair round when any finding needs instruction. Code-observed possibility; not exercised in the full Karma run because no critic ran. | A narrower repair policy needs actual mixed findings and compatibility work. | A valid topic comparison is blocked by this policy. |
| Transcript names and title spelling | Source/render titles contain inconsistent transliterations. No full ASR accuracy review was performed here. | Cosmetic corrections do not establish standalone quality. | Batch after editorial comparison, unless a meaning-changing transcription error prevents judgment. |
| Export, player and general UI polish | No new UI defect is established by this read-only audit. Earlier user-facing reports remain historical items. | Do not manufacture a browser QA sweep while the editorial task is unresolved. | After topic output and review states are stable, or if media cannot be judged. |
| Reuse technical checks after human topic decisions | The new topic renderer reuses media and captions by content identity, but a changed child edit ID/SHA causes technical checks to run again after an acceptance or rejection. No additional model call is made. | Correct review/export behavior is implemented first; this is a throughput optimization, not an editorial-quality fix. | After the standalone-topic audition, rebind proven unchanged executions through the existing review reuse checks and measure media reads/check time. |
| Navigation-segmentation tuning | KCPD granularity and boundary metrics are separate from independent-topic completion. KCPD did not set full Karma's chapter count. | Tuning candidate density does not test the current failure. | A controlled proposer ablation shows missed-topic recall is the limiting factor. |

Reviewer provisioning, independent span representation, discourse completion, cold comprehension,
source faithfulness and actual editorial evaluation are **core work**, not items to defer as minor
bugs. Conversely, the previous renderer heartbeat/ownership correction is already merged and was
not the cause of the latest full Karma result. Do not reopen it without new evidence.

No item in this list was fixed or deployed by the research pass. A new blocker may be handled only
to restore a valid editorial experiment; record its evidence and return to that experiment.
