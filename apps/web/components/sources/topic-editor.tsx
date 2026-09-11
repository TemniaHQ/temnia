"use client";

import {
  HarnessEvidenceSchema,
  type TopicCandidate,
  TopicEditSpecSchema,
  type TopicSentenceSpan,
} from "@temnia/contracts";
import { selectTime, usePlayer } from "@videojs/react";
import { useEffect, useRef, useState } from "react";
import {
  editTopicPortfolio,
  getPendingTopicWorkflowStatus,
} from "@/app/actions/topics";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { verifiedArtifactJson } from "@/lib/harness/artifact";
import type { ChapterArtifactRef, ChapterView } from "@/lib/harness/queries";
import {
  restoreTopicIntent,
  TopicEditorialDraftSchema,
  type TopicEditorialPatchIntent,
  TopicEditorialPatchIntentSchema,
} from "@/lib/harness/topic-pending";
import { formatDuration } from "@/lib/sources/labels";

type Operation = TopicEditorialPatchIntent["operations"][number]["kind"];
interface Sentence {
  endMs: number;
  id: string;
  startMs: number;
  text: string;
}
interface EditorContext {
  candidates: TopicCandidate[];
  editSha256: string;
  evidenceSha256: string;
  sentences: Sentence[];
}
const OPERATIONS: Record<Operation, string> = {
  add: "Add missed discussion",
  adjust_extent: "Change opening or ending",
  drop: "Remove video",
  merge: "Merge discussions",
  retitle: "Correct title",
  split: "Split discussion",
};

function blankCandidate(): TopicCandidate {
  return {
    completionSpans: [],
    coreSpans: [],
    firstSentenceId: "",
    id: crypto.randomUUID(),
    lastSentenceId: "",
    meaningChangingFollowups: [],
    purpose: "",
    reason: "",
    requiredContextSpans: [],
    title: "",
  };
}
function store(key: string, value: unknown) {
  try {
    if (value === null) {
      sessionStorage.removeItem(key);
    } else {
      sessionStorage.setItem(key, JSON.stringify(value));
    }
  } catch {
    /* The mounted editor retains its exact draft. */
  }
}

function stored(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

/** Source-bound editorial feedback; acceptance remains a separate action after playback. */
export function TopicEditor({
  sourceId,
  runId,
  revision,
  blocked,
  onChanged,
}: {
  sourceId: string;
  runId: string;
  revision: number;
  blocked: boolean;
  onChanged: () => Promise<void>;
}) {
  const key = `temnia:topic-patch:${sourceId}:${runId}`;
  const draftKey = `${key}:draft`;
  const [open, setOpen] = useState(false);
  const [context, setContext] = useState<EditorContext | null>(null);
  const [baseRevision, setBaseRevision] = useState(revision);
  const [kind, setKind] = useState<Operation>("adjust_extent");
  const [parents, setParents] = useState<string[]>([]);
  const [drafts, setDrafts] = useState<TopicCandidate[]>([]);
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState<TopicEditorialPatchIntent | null>(
    null
  );
  const [busy, setBusy] = useState(false);
  const [restoredDraft, setRestoredDraft] = useState(false);
  const [terminal, setTerminal] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedSentence, setSelectedSentence] = useState<string | null>(null);
  const started = useRef<number | null>(null);
  const activeSeconds = useRef(0);
  const time = usePlayer(selectTime);

  useEffect(() => {
    const restored = restoreTopicIntent(
      stored(key),
      TopicEditorialPatchIntentSchema
    );
    if (restored) {
      setPending(restored);
      setBaseRevision(restored.baseRevision);
      setOpen(true);
      setKind(restored.operations[0]?.kind ?? "adjust_extent");
      setParents(restored.operations[0]?.affectedCandidateIds ?? []);
      setDrafts(restored.operations[0]?.replacementCandidates ?? []);
      setReason(restored.reason);
    } else {
      const draft = restoreTopicIntent(
        stored(draftKey),
        TopicEditorialDraftSchema
      );
      if (draft) {
        setBaseRevision(draft.baseRevision);
        setKind(draft.kind);
        setParents(draft.parents);
        setDrafts(draft.drafts);
        setReason(draft.reason);
        setOpen(true);
      }
    }
    setRestoredDraft(true);
  }, [key, draftKey]);
  useEffect(() => {
    if (restoredDraft) {
      store(
        draftKey,
        parents.length || drafts.length || reason
          ? {
              baseRevision,
              drafts,
              kind,
              parents,
              reason,
            }
          : null
      );
    }
  }, [restoredDraft, draftKey, baseRevision, drafts, kind, parents, reason]);
  useEffect(() => {
    if (!open) {
      return;
    }
    const tick = () => {
      const now = performance.now();
      if (
        started.current !== null &&
        document.visibilityState === "visible" &&
        document.hasFocus()
      ) {
        activeSeconds.current += (now - started.current) / 1000;
      }
      started.current = now;
    };
    started.current = performance.now();
    const timer = setInterval(tick, 1000);
    return () => {
      tick();
      clearInterval(timer);
      started.current = null;
    };
  }, [open]);
  useEffect(() => {
    if (!open) {
      return;
    }
    let active = true;
    setContext(null);
    fetch(
      `/api/sources/${sourceId}/topics/context?runId=${runId}&revision=${baseRevision}`,
      { cache: "no-store" }
    )
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(
            "The source context for this revision is unavailable."
          );
        }
        const refs = (await response.json()) as {
          edit: ChapterArtifactRef;
          evidence: ChapterArtifactRef;
        };
        const [edit, evidence] = await Promise.all([
          verifiedArtifactJson(refs.edit, TopicEditSpecSchema),
          verifiedArtifactJson(refs.evidence, HarnessEvidenceSchema),
        ]);
        if (
          edit.sourceId !== sourceId ||
          evidence.sourceId !== sourceId ||
          edit.evidenceArtifactId !== refs.evidence.id ||
          edit.evidenceSha256 !== refs.evidence.sha256
        ) {
          throw new Error("The editor source does not match this revision.");
        }
        if (active) {
          setContext({
            candidates: edit.videos.map((video) => video.candidate),
            editSha256: refs.edit.sha256,
            evidenceSha256: refs.evidence.sha256,
            sentences: evidence.sentences,
          });
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setMessage(
            error instanceof Error
              ? error.message
              : "Source context could not be loaded."
          );
        }
      });
    return () => {
      active = false;
    };
  }, [open, baseRevision, sourceId, runId]);
  useEffect(() => {
    if (!pending) {
      return;
    }
    let active = true;
    const inspect = async () => {
      try {
        const response = await fetch(
          `/api/sources/${sourceId}/topics?runId=${runId}&mutationKey=${pending.mutationKey}`,
          { cache: "no-store" }
        );
        if (!response.ok) {
          return;
        }
        const view = (await response.json()) as ChapterView;
        const event = view.events.find(
          (item) => item.mutationKey === pending.mutationKey
        );
        if (!(active && event)) {
          return;
        }
        setPending(null);
        store(key, null);
        const outcomes: Record<string, string> = {
          applied:
            "Correction recorded. Review the revised videos before accepting them.",
          conflict:
            "The portfolio changed. Your draft is retained against its original revision; review the latest source before creating a new correction.",
          refused:
            "The correction was refused. Your draft is retained; check its source spans and operation.",
        };
        setMessage(
          event.state === "refused" && event.message
            ? `${event.message} Your draft is retained.`
            : (outcomes[event.state] ?? "The correction needs review.")
        );
        if (event.state === "applied") {
          setOpen(false);
          setContext(null);
          setParents([]);
          setDrafts([]);
          setReason("");
          store(draftKey, null);
        }
        await onChanged();
      } catch {
        /* Keep the exact pending identity until a durable outcome is readable. */
      }
    };
    const timer = setInterval(inspect, 2500);
    inspect().catch(() => undefined);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [pending, key, draftKey, sourceId, runId, onChanged]);

  function selectOperation(operation: Operation, selected = parents) {
    setKind(operation);
    setParents(operation === "add" ? [] : selected);
    const first = context?.candidates.find(
      (candidate) => candidate.id === selected[0]
    );
    if (operation === "drop") {
      setDrafts([]);
    } else if (operation === "split") {
      setDrafts([blankCandidate(), blankCandidate()]);
    } else if (operation === "add" || operation === "merge") {
      setDrafts([blankCandidate()]);
    } else {
      setDrafts(first ? [structuredClone(first)] : []);
    }
  }
  function selectParent(id: string) {
    let selected = [id];
    if (kind === "merge") {
      selected = parents.includes(id)
        ? parents.filter((item) => item !== id)
        : [...parents, id];
    }
    selectOperation(kind, selected);
  }

  function updateDraft(index: number, candidate: TopicCandidate) {
    setDrafts((current) =>
      current.map((item, at) => (at === index ? candidate : item))
    );
  }
  async function submit(existing?: TopicEditorialPatchIntent) {
    if (
      !context ||
      busy ||
      (!existing && (blocked || baseRevision !== revision))
    ) {
      return;
    }
    const parsed = TopicEditorialPatchIntentSchema.safeParse(
      existing ?? {
        action: "topic_edit",
        baseEditSha256: context.editSha256,
        baseRevision,
        correctionActiveSeconds: activeSeconds.current,
        correctionMeasurementMethod: "visible_focused_editor_elapsed_seconds",
        evidenceSha256: context.evidenceSha256,
        mutationKey: crypto.randomUUID(),
        operations: [
          {
            affectedCandidateIds: parents,
            kind,
            operationId: crypto.randomUUID(),
            replacementCandidates: drafts,
          },
        ],
        reason,
        runId,
        sourceId,
        version: 1,
      }
    );
    if (!parsed.success) {
      setMessage(
        "Complete the title, viewer purpose, explanation and source spans for every replacement, and give a correction reason."
      );
      return;
    }
    setPending(parsed.data);
    store(key, parsed.data);
    setBusy(true);
    setTerminal(false);
    setMessage(null);
    try {
      const result = await editTopicPortfolio(parsed.data);
      if (!result.ok) {
        setMessage(result.message);
      }
      if (!result.pending) {
        setPending(null);
        store(key, null);
        if (result.ok) {
          setOpen(false);
          setParents([]);
          setDrafts([]);
          setReason("");
          store(draftKey, null);
          await onChanged();
        }
      }
    } catch {
      setMessage(
        "The correction result is unknown. Keep this draft and retry the same command."
      );
    } finally {
      setBusy(false);
    }
  }
  const shown =
    context?.sentences
      .filter(
        (sentence) =>
          !query ||
          sentence.text
            .toLocaleLowerCase()
            .includes(query.toLocaleLowerCase()) ||
          sentence.id === selectedSentence
      )
      .slice(0, 80) ?? [];
  return (
    <section className="space-y-3 rounded-lg border p-4">
      <Button
        disabled={blocked && !open}
        onClick={() => {
          setOpen(!open);
          if (!(open || pending || parents.length || drafts.length || reason)) {
            setBaseRevision(revision);
            setContext(null);
          }
        }}
        variant="outline"
      >
        Edit topic selections
      </Button>
      {!!message && (
        <p className="text-sm" role="status">
          {message}
        </p>
      )}
      {!!open && (
        <div className="space-y-4">
          <p className="text-sm">
            Choose the source speech you want in each video. Core and completion
            spans are your editorial annotations; changed videos need fresh
            review.
          </p>
          {baseRevision !== revision && (
            <p role="alert">
              This draft uses revision {baseRevision}; the current revision is{" "}
              {revision}. It will not be silently rebased.{" "}
              <Button
                disabled={!!pending}
                onClick={() => {
                  setBaseRevision(revision);
                  setParents([]);
                  setDrafts([]);
                  setReason("");
                  setContext(null);
                }}
                size="sm"
                variant="outline"
              >
                Start a new correction on the current revision
              </Button>
            </p>
          )}
          {pending ? (
            <div className="flex gap-2">
              <Button disabled={busy} onClick={() => submit(pending)}>
                Retry same correction
              </Button>
              <Button
                onClick={async () => {
                  const status = await getPendingTopicWorkflowStatus({
                    intent: pending,
                    kind: "patch",
                  });
                  setMessage(status.message);
                  setTerminal(status.state === "terminal");
                }}
                variant="outline"
              >
                Check correction status
              </Button>
              {!!terminal && (
                <Button
                  onClick={() => {
                    setPending(null);
                    store(key, null);
                    setTerminal(false);
                  }}
                  variant="outline"
                >
                  Keep draft and clear stopped request
                </Button>
              )}
            </div>
          ) : null}
          {context ? (
            <fieldset
              className="space-y-4"
              disabled={busy || !!pending || blocked}
            >
              <NativeSelect
                aria-label="Editorial correction"
                onChange={(event) =>
                  selectOperation(event.target.value as Operation)
                }
                value={kind}
              >
                {Object.entries(OPERATIONS).map(([value, label]) => (
                  <NativeSelectOption key={value} value={value}>
                    {label}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
              {kind !== "add" && (
                <div className="space-y-2">
                  <p className="text-sm">
                    {kind === "merge"
                      ? "Select the videos to merge:"
                      : "Select the video to change:"}
                  </p>
                  {context.candidates.map((candidate) => (
                    <label className="flex gap-2 text-sm" key={candidate.id}>
                      <input
                        checked={parents.includes(candidate.id)}
                        onChange={() => selectParent(candidate.id)}
                        type="checkbox"
                      />
                      {candidate.title}
                    </label>
                  ))}
                </div>
              )}
              {drafts.map((draft, index) => (
                <CandidateFields
                  draft={draft}
                  index={index}
                  key={draft.id}
                  onChange={(candidate) => updateDraft(index, candidate)}
                  onLocate={setSelectedSentence}
                  sentences={context.sentences}
                  titleOnly={kind === "retitle"}
                />
              ))}
              {kind === "split" && (
                <Button
                  onClick={() => setDrafts([...drafts, blankCandidate()])}
                  variant="outline"
                >
                  Add another resulting video
                </Button>
              )}
              <Textarea
                aria-label="Editorial correction reason"
                onChange={(event) => setReason(event.target.value)}
                placeholder="Why does this improve the selection?"
                value={reason}
              />
              <Button
                disabled={baseRevision !== revision}
                onClick={() => submit()}
              >
                Save editorial correction
              </Button>
            </fieldset>
          ) : (
            <p>Loading verified source sentences…</p>
          )}
          {!!context && (
            <details className="space-y-2" open={!!selectedSentence}>
              <summary className="cursor-pointer">
                Read and listen to source context
              </summary>
              <Input
                aria-label="Search source sentences"
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search the source discussion"
                value={query}
              />
              <div className="max-h-80 space-y-2 overflow-auto">
                {(selectedSentence
                  ? context.sentences.slice(
                      Math.max(
                        0,
                        context.sentences.findIndex(
                          (row) => row.id === selectedSentence
                        ) - 3
                      ),
                      context.sentences.findIndex(
                        (row) => row.id === selectedSentence
                      ) + 4
                    )
                  : shown
                ).map((sentence) => (
                  <div className="text-sm" key={sentence.id}>
                    <Button
                      onClick={() => {
                        time
                          ?.seek?.(sentence.startMs / 1000)
                          .catch(() => undefined);
                      }}
                      size="sm"
                      variant="ghost"
                    >
                      {formatDuration(sentence.startMs)} · Listen
                    </Button>
                    <span
                      className={
                        sentence.id === selectedSentence ? "font-medium" : ""
                      }
                    >
                      {sentence.text}
                    </span>
                  </div>
                ))}
              </div>
              {!!selectedSentence && (
                <Button
                  onClick={() => setSelectedSentence(null)}
                  size="sm"
                  variant="outline"
                >
                  Return to source search
                </Button>
              )}
              {!selectedSentence && (
                <p className="text-muted-foreground text-xs">
                  Showing up to 80 matching sentences. Search to inspect any
                  discussion in the full source.
                </p>
              )}
            </details>
          )}
        </div>
      )}
    </section>
  );
}

function SentenceSelect({
  label,
  value,
  sentences,
  onChange,
  onLocate,
}: {
  label: string;
  value: string;
  sentences: Sentence[];
  onChange: (value: string) => void;
  onLocate: (value: string) => void;
}) {
  const [search, setSearch] = useState("");
  const matches = sentences.filter(
    (row) =>
      !search ||
      row.text.toLocaleLowerCase().includes(search.toLocaleLowerCase())
  );
  const selected = sentences.find((row) => row.id === value);
  const options = [
    ...new Map(
      [...(selected ? [selected] : []), ...matches.slice(0, 60)].map((row) => [
        row.id,
        row,
      ])
    ).values(),
  ];
  return (
    <div className="space-y-1">
      <label className="text-sm" htmlFor={`${label}-search`}>
        {label}
      </label>
      <Input
        aria-label={`${label} search`}
        id={`${label}-search`}
        onChange={(event) => setSearch(event.target.value)}
        placeholder="Find a source sentence"
        value={search}
      />
      <div className="flex gap-2">
        <NativeSelect
          aria-label={label}
          onChange={(event) => onChange(event.target.value)}
          value={value}
        >
          <NativeSelectOption value="">Choose a sentence</NativeSelectOption>
          {options.map((row) => (
            <NativeSelectOption key={row.id} value={row.id}>
              {formatDuration(row.startMs)} · {row.text.slice(0, 120)}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        <Button
          disabled={!value}
          onClick={() => onLocate(value)}
          size="sm"
          variant="outline"
        >
          Context
        </Button>
      </div>
    </div>
  );
}

function CandidateFields({
  draft,
  index,
  sentences,
  titleOnly,
  onChange,
  onLocate,
}: {
  draft: TopicCandidate;
  index: number;
  sentences: Sentence[];
  titleOnly: boolean;
  onChange: (candidate: TopicCandidate) => void;
  onLocate: (value: string) => void;
}) {
  const prefix = `Video ${index + 1}`;
  return (
    <div className="space-y-3 rounded border p-3">
      <h4 className="font-medium">{prefix}</h4>
      <Input
        aria-label={`${prefix} title`}
        onChange={(event) => onChange({ ...draft, title: event.target.value })}
        placeholder="Faithful title"
        value={draft.title}
      />
      {!titleOnly && (
        <>
          <Textarea
            aria-label={`${prefix} purpose`}
            onChange={(event) =>
              onChange({ ...draft, purpose: event.target.value })
            }
            placeholder="What does the viewer get from this discussion?"
            value={draft.purpose}
          />
          <Textarea
            aria-label={`${prefix} explanation`}
            onChange={(event) =>
              onChange({ ...draft, reason: event.target.value })
            }
            placeholder="Why is this a complete, useful selection?"
            value={draft.reason}
          />
          <SentenceSelect
            label={`${prefix} opening`}
            onChange={(value) => onChange({ ...draft, firstSentenceId: value })}
            onLocate={onLocate}
            sentences={sentences}
            value={draft.firstSentenceId}
          />
          <SentenceSelect
            label={`${prefix} ending`}
            onChange={(value) => onChange({ ...draft, lastSentenceId: value })}
            onLocate={onLocate}
            sentences={sentences}
            value={draft.lastSentenceId}
          />
          {(
            [
              "coreSpans",
              "completionSpans",
              "requiredContextSpans",
              "meaningChangingFollowups",
            ] as const
          ).map((field) => (
            <SpanFields
              key={field}
              label={`${prefix} ${{ completionSpans: "completion", coreSpans: "core discussion", meaningChangingFollowups: "consequential follow-up", requiredContextSpans: "required context" }[field]}`}
              onChange={(values) => onChange({ ...draft, [field]: values })}
              onLocate={onLocate}
              sentences={sentences}
              values={draft[field]}
            />
          ))}
        </>
      )}
    </div>
  );
}

function SpanFields({
  label,
  values,
  sentences,
  onChange,
  onLocate,
}: {
  label: string;
  values: TopicSentenceSpan[];
  sentences: Sentence[];
  onChange: (spans: TopicSentenceSpan[]) => void;
  onLocate: (value: string) => void;
}) {
  return (
    <details
      open={
        values.length === 0 &&
        (label.includes("core discussion") || label.endsWith("completion"))
      }
    >
      <summary className="cursor-pointer text-sm">
        {label} · {values.length} spans
      </summary>
      <div className="space-y-3 pt-2">
        {values.map((span, index) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: source span entries are position-based editable slots; their values deliberately change in place
          <div className="space-y-2" key={`${label}-${index}`}>
            <SentenceSelect
              label={`${label} ${index + 1} first`}
              onChange={(value) =>
                onChange(
                  values.map((item, at) =>
                    at === index ? { ...item, firstSentenceId: value } : item
                  )
                )
              }
              onLocate={onLocate}
              sentences={sentences}
              value={span.firstSentenceId}
            />
            <SentenceSelect
              label={`${label} ${index + 1} last`}
              onChange={(value) =>
                onChange(
                  values.map((item, at) =>
                    at === index ? { ...item, lastSentenceId: value } : item
                  )
                )
              }
              onLocate={onLocate}
              sentences={sentences}
              value={span.lastSentenceId}
            />
            <Button
              onClick={() => onChange(values.filter((_, at) => at !== index))}
              size="sm"
              variant="outline"
            >
              Remove this annotation
            </Button>
          </div>
        ))}
        <Button
          onClick={() =>
            onChange([...values, { firstSentenceId: "", lastSentenceId: "" }])
          }
          size="sm"
          variant="outline"
        >
          Add {label.split(" ").slice(2).join(" ")} span
        </Button>
      </div>
    </details>
  );
}
