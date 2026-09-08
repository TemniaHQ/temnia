"use client";

import {
  ArrowDown01Icon,
  ArrowDownDoubleIcon,
  ArrowUp01Icon,
  Cancel01Icon,
  Edit02Icon,
  FileDownloadIcon,
  Search01Icon,
  UserMultipleIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  type TranscriptRevisionAnnotations,
  TranscriptRevisionAnnotationsSchema,
  type TranscriptV1,
} from "@temnia/contracts";
import { selectTime, usePlayer } from "@videojs/react";
import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  useTransition,
} from "react";
import {
  correctTranscriptStructure,
  type TranscriptActionResult,
} from "@/app/actions/transcript";
import { TranscriptParagraph } from "@/components/sources/transcript-paragraph";
import { TranscriptSpeakersDialog } from "@/components/sources/transcript-speakers-dialog";
import type { WordEdit } from "@/components/sources/transcript-word-editor";
import { Alert, AlertAction, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
  InputGroupText,
} from "@/components/ui/input-group";
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { Toggle } from "@/components/ui/toggle";
import { TooltipProvider } from "@/components/ui/tooltip";
import { clearSessionUuid, stableSessionUuid } from "@/lib/harness/client";
import { fetchRevision } from "@/lib/transcript/content";
import {
  EMPTY_DRAFTS,
  type WordDrafts,
  wordDrafts,
} from "@/lib/transcript/drafts";
import { STALE_REVISION_MESSAGE } from "@/lib/transcript/edits";
import {
  buildParagraphs,
  findMatches,
  type Paragraph,
  paragraphAt,
  type TranscriptMatch,
  wordAt,
} from "@/lib/transcript/paragraphs";
import { legacyAnnotations } from "@/lib/transcript/structure";
import { wordsByUtterance } from "@/lib/transcript/utterances";

interface LoadedRevision {
  annotations: TranscriptRevisionAnnotations;
  content: TranscriptV1;
  revision: number;
}

interface TranscriptReaderProps {
  annotationsUrl: string;
  /** The revision at revisionUrl; edits use it only after those bytes load. */
  baseRevision: number;
  labels: Readonly<Record<string, string>>;
  readOnly: boolean;
  revisionUrl: string;
  sourceId: string;
  /** Names the two caption downloads, so a folder of exports is readable. */
  title: string;
}

/** A row is about four lines; the virtualiser measures the real one on mount. */
const ESTIMATED_ROW_PX = 92;
const WHITESPACE = /\s+/;
const NONNEGATIVE_INTEGER = /^\d+$/;

/** A save that threw rather than answered: the network, the store, the database. */
const SAVE_FAILED_MESSAGE =
  "Could not save. Check your connection and try again.";

function exportName(title: string, format: string): string {
  const cleaned = title
    .replace(/[^\w \-.]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80);
  return `${cleaned || "transcript"}.${format}`;
}

function focusedWords(
  paragraph: Paragraph,
  match: TranscriptMatch | null
): readonly [first: number, last: number] {
  if (
    !match ||
    paragraph.lastWord < match.firstWord ||
    paragraph.firstWord > match.lastWord
  ) {
    return [-1, -1];
  }
  return [
    Math.max(paragraph.firstWord, match.firstWord),
    Math.min(paragraph.lastWord, match.lastWord),
  ];
}

/**
 * The transcript, once there is one.
 *
 * Reads the revision from the org-scoped media proxy once, builds the display
 * paragraphs, and virtualises them: a 2.5-hour episode is about 28 thousand
 * words, and only the rows on screen may exist in the DOM. The playhead comes
 * from the player store the source page provides, so following and seeking
 * share the one player with the pane beside it rather than a lifted ref.
 */
// biome-ignore lint/complexity/noExcessiveCognitiveComplexity: the transcript surface coordinates playback, virtualized search, retained drafts, and explicit revision commands
export function TranscriptReader({
  annotationsUrl,
  baseRevision,
  labels,
  readOnly,
  revisionUrl,
  sourceId,
  title,
}: TranscriptReaderProps) {
  const router = useRouter();
  // The standalone hook with the library's own premade selector: an inline
  // selector on this store is typed `unknown`, and `selectTime` is what makes
  // the playhead and the seek typed without reaching into the player's
  // internals. The value is compared field by field, so the reader re-renders
  // on a time change and on nothing else.
  const time = usePlayer(selectTime);
  const currentTime = time?.currentTime ?? 0;
  const seek = time?.seek;
  const [pending, startTransition] = useTransition();

  const [loaded, setLoaded] = useState<LoadedRevision | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Counted up by Try again. The fetch effect depends on it, so a retry is a
  // new request; a router refresh alone re-rendered the same client component
  // with the same URL and fetched nothing (S2 review, I20).
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [assignError, setAssignError] = useState<string | null>(null);
  const [viewport, setViewport] = useState<HTMLElement | null>(null);
  const [follow, setFollow] = useState(true);
  const [editMode, setEditMode] = useState(false);
  const [draftState, dispatchDraft] = useReducer(wordDrafts, EMPTY_DRAFTS);
  const nextDraftId = useRef(0);
  const { content, currentRevision, currentWord, edit } = readerContext(
    loaded,
    draftState
  );
  const [query, setQuery] = useState("");
  const [matchIndex, setMatchIndex] = useState(0);
  const [stale, setStale] = useState(false);
  const [speakersOpen, setSpeakersOpen] = useState(false);
  const [speakersError, setSpeakersError] = useState<string | null>(null);
  const [structureText, setStructureText] = useState("");
  const [structureStart, setStructureStart] = useState("");
  const [structureEnd, setStructureEnd] = useState("");
  const [undoRevision, setUndoRevision] = useState("");
  const [mergeSpeaker, setMergeSpeaker] = useState("");

  // biome-ignore lint/correctness/useExhaustiveDependencies: `loadAttempt` is the retry; a new value is a new request
  useEffect(() => {
    const controller = new AbortController();
    setLoadError(null);
    Promise.all([
      fetchRevision(revisionUrl, controller.signal),
      fetch(annotationsUrl, {
        cache: "no-store",
        signal: controller.signal,
      }).then(async (response) => {
        if (!response.ok) {
          throw new Error("annotation metadata unavailable");
        }
        return (await response.json()) as {
          annotations: unknown;
          legacySpeakerLabels: Record<string, string>;
          machineRevision: number;
          transcriptId: string;
        };
      }),
    ])
      .then(([next, metadata]) => {
        if (!controller.signal.aborted) {
          // A router refresh changes the URL/number before the JSON arrives.
          // Keep the previous pair intact until both can move together.
          const parsed = TranscriptRevisionAnnotationsSchema.safeParse(
            metadata.annotations
          );
          setLoaded({
            annotations: parsed.success
              ? parsed.data
              : legacyAnnotations(
                  metadata.transcriptId,
                  metadata.machineRevision,
                  next,
                  metadata.legacySpeakerLabels
                ),
            content: next,
            revision: baseRevision,
          });
          const resolved = parsed.success
            ? parsed.data
            : legacyAnnotations(
                metadata.transcriptId,
                metadata.machineRevision,
                next,
                metadata.legacySpeakerLabels
              );
          dispatchDraft({
            identityIds: resolved.wordIdentities.map((identity) => identity.id),
            type: "rebase",
          });
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setLoadError(
            error instanceof Error && error.name === "TypeError"
              ? "The transcript could not be reached. Check your connection."
              : "The transcript could not be read."
          );
        }
      });
    return () => controller.abort();
  }, [annotationsUrl, baseRevision, revisionUrl, loadAttempt]);

  const words = content?.words;
  const displayLabels = useMemo(
    () =>
      loaded
        ? Object.fromEntries(
            Object.entries(loaded.annotations.speakerIdentities).map(
              ([raw, identity]) => [raw, identity.label]
            )
          )
        : labels,
    [labels, loaded]
  );
  const paragraphs = useMemo(
    () => (content ? buildParagraphs(content.words, content.utterances) : []),
    [content]
  );
  const matches = useMemo(
    () => (words ? findMatches(words, query) : []),
    [query, words]
  );
  const activeWord = words ? wordAt(words, currentTime * 1000) : -1;
  const activeParagraph = paragraphAt(paragraphs, activeWord);
  const safeMatchIndex = Math.min(matchIndex, Math.max(0, matches.length - 1));
  const focusedMatch = matches[safeMatchIndex] ?? null;
  const focusedParagraph = paragraphAt(
    paragraphs,
    focusedMatch?.firstWord ?? -1
  );
  const editParagraph = paragraphAt(paragraphs, edit?.index ?? -1);

  const virtualizer = useVirtualizer({
    count: paragraphs.length,
    estimateSize: () => ESTIMATED_ROW_PX,
    getScrollElement: () => viewport,
    overscan: 8,
  });

  const attach = useCallback((node: HTMLDivElement | null) => {
    setViewport(
      node?.querySelector<HTMLElement>('[data-slot="scroll-area-viewport"]') ??
        null
    );
  }, []);

  // Follow-scroll stops the moment a person scrolls for themselves; the Jump
  // control is how it starts again, so the pane never fights the reader.
  useEffect(() => {
    if (!viewport) {
      return;
    }
    const pause = () => setFollow(false);
    viewport.addEventListener("wheel", pause, { passive: true });
    viewport.addEventListener("touchmove", pause, { passive: true });
    return () => {
      viewport.removeEventListener("wheel", pause);
      viewport.removeEventListener("touchmove", pause);
    };
  }, [viewport]);

  // Paused while a word is open for correction: following playback would
  // scroll the editor out of the retained rows mid-word.
  useEffect(() => {
    if (follow && activeParagraph >= 0 && edit === null) {
      virtualizer.scrollToIndex(activeParagraph, { align: "center" });
    }
  }, [activeParagraph, edit, follow, virtualizer]);

  useEffect(() => {
    if (focusedParagraph >= 0) {
      virtualizer.scrollToIndex(focusedParagraph, { align: "center" });
    }
  }, [focusedParagraph, virtualizer]);

  const refused = useCallback(
    (result: { invalid?: true; message: string; stale?: true }) => {
      if (result.stale) {
        setStale(true);
        return true;
      }
      return false;
    },
    []
  );

  const saveWord = useCallback(
    (index: number, text: string) => {
      if (!edit || edit.index !== index || edit.pending) {
        return;
      }
      const owner = edit;
      dispatchDraft({ id: owner.id, text, type: "save" });
      startTransition(async () => {
        let result: TranscriptActionResult;
        try {
          const targetId = loaded?.annotations.wordIdentities[index]?.id;
          if (!targetId) {
            throw new Error("word identity missing");
          }
          const intent = JSON.stringify([
            "replace",
            owner.baseRevision,
            targetId,
            text,
          ]);
          const key = `transcript-command:${sourceId}:${intent}`;
          result = await correctTranscriptStructure(sourceId, {
            action: "replace",
            baseRevision: owner.baseRevision,
            mutationKey: stableSessionUuid(key),
            targetId,
            text,
          });
          if (result.ok) {
            clearSessionUuid(key);
          }
        } catch {
          dispatchDraft({
            error: SAVE_FAILED_MESSAGE,
            id: owner.id,
            type: "settle",
          });
          return;
        }
        dispatchDraft({
          error: result.ok ? null : result.message,
          id: owner.id,
          type: "settle",
        });
        if (result.ok) {
          router.refresh();
        } else {
          refused(result);
        }
      });
    },
    [edit, loaded, refused, router, sourceId]
  );

  const assignSpeaker = useCallback(
    (utteranceIndex: number, speaker: string) => {
      if (!loaded) {
        return;
      }
      const { revision } = loaded;
      setAssignError(null);
      startTransition(async () => {
        let result: TranscriptActionResult;
        try {
          const targetSpeakerIdentityId =
            loaded.annotations.speakerIdentities[speaker]?.identityId;
          const targetIds = (
            wordsByUtterance(loaded.content.words, loaded.content.utterances)[
              utteranceIndex
            ] ?? []
          )
            .map(
              (index) => loaded.annotations.wordIdentities[index]?.id ?? null
            )
            .filter((identity): identity is string => identity !== null);
          if (!targetSpeakerIdentityId || targetIds.length === 0) {
            throw new Error("speaker identity missing");
          }
          const intent = JSON.stringify([
            "reassign_speaker",
            revision,
            targetIds,
            targetSpeakerIdentityId,
          ]);
          const key = `transcript-command:${sourceId}:${intent}`;
          result = await correctTranscriptStructure(sourceId, {
            action: "reassign_speaker",
            baseRevision: revision,
            mutationKey: stableSessionUuid(key),
            targetIds,
            targetSpeakerIdentityId,
          });
          if (result.ok) {
            clearSessionUuid(key);
          }
        } catch {
          setAssignError(SAVE_FAILED_MESSAGE);
          return;
        }
        if (result.ok) {
          router.refresh();
          return;
        }
        if (!refused(result)) {
          setAssignError(result.message);
        }
      });
    },
    [loaded, refused, router, sourceId]
  );

  const saveSpeakers = useCallback(
    (next: Record<string, string>) => {
      if (!loaded) {
        return;
      }
      setSpeakersError(null);
      startTransition(async () => {
        let result: TranscriptActionResult;
        try {
          const renamed = Object.fromEntries(
            Object.entries(next).map(([raw, label]) => {
              const identity =
                loaded.annotations.speakerIdentities[raw]?.identityId;
              if (!identity) {
                throw new Error("speaker identity missing");
              }
              return [identity, label];
            })
          );
          const intent = JSON.stringify([
            "rename_speakers",
            loaded.revision,
            renamed,
          ]);
          const key = `transcript-command:${sourceId}:${intent}`;
          result = await correctTranscriptStructure(sourceId, {
            action: "rename_speakers",
            baseRevision: loaded.revision,
            labels: renamed,
            mutationKey: stableSessionUuid(key),
          });
          if (result.ok) {
            clearSessionUuid(key);
          }
        } catch {
          setSpeakersError(SAVE_FAILED_MESSAGE);
          return;
        }
        if (result.ok) {
          setSpeakersOpen(false);
          router.refresh();
          return;
        }
        setSpeakersError(result.message);
      });
    },
    [loaded, router, sourceId]
  );

  const openRename = useCallback(() => setSpeakersOpen(true), []);
  const cancelEdit = useCallback(() => dispatchDraft({ type: "cancel" }), []);
  const draftWord = useCallback(
    (text: string) => dispatchDraft({ text, type: "change" }),
    []
  );

  // One listener for every word on screen, on the scrolling element itself.
  // Each word is a real button and carries its index in a data attribute; a
  // handler per word would give every row a new prop on every render and undo
  // the point of memoising them (S2 plan §3).
  useEffect(() => {
    if (!viewport) {
      return;
    }
    const onWordClick = (event: MouseEvent) => {
      const target = (event.target as HTMLElement | null)?.closest<HTMLElement>(
        "[data-word]"
      );
      const index = Number(target?.dataset.word);
      if (!Number.isInteger(index)) {
        return;
      }
      const word = content?.words[index];
      if (editMode && loaded && word) {
        const orphaned = draftState.drafts.find(
          (draft) => draft.id === draftState.activeId && draft.index < 0
        );
        const identityId =
          loaded.annotations.wordIdentities[index]?.id ??
          `missing:${loaded.revision}:${index}`;
        if (orphaned) {
          dispatchDraft({
            id: orphaned.id,
            identityId,
            index,
            original: word.text,
            revision: loaded.revision,
            type: "retarget",
          });
          return;
        }
        const existing = draftState.drafts.find(
          (draft) => draft.index === index
        );
        if (existing) {
          dispatchDraft({ id: existing.id, type: "activate" });
        } else {
          nextDraftId.current += 1;
          dispatchDraft({
            edit: {
              baseRevision: loaded.revision,
              draft: word.text,
              error: null,
              id: nextDraftId.current,
              identityId,
              index,
              original: word.text,
              pending: false,
            },
            type: "open",
          });
        }
        return;
      }
      if (word && seek) {
        // The promise resolves at the seeked position and nothing here waits
        // for it; a seek that cannot happen is the player's to report.
        seek(word.startMs / 1000).catch(() => undefined);
      }
    };
    viewport.addEventListener("click", onWordClick);
    return () => viewport.removeEventListener("click", onWordClick);
  }, [
    content,
    draftState.activeId,
    draftState.drafts,
    editMode,
    loaded,
    seek,
    viewport,
  ]);

  const step = (delta: number) => {
    if (matches.length > 0) {
      setMatchIndex((current) => {
        const next = (current + delta) % matches.length;
        return next < 0 ? next + matches.length : next;
      });
    }
  };

  const onQuery = (value: string) => {
    setQuery(value);
    setMatchIndex(0);
    if (value.trim()) {
      // A search and follow-scroll both want the pane, and the reader asked
      // for the search.
      setFollow(false);
    }
  };

  const loadNotice = loadError ? (
    <Alert data-testid="transcript-load-error" variant="destructive">
      <AlertDescription>{loadError}</AlertDescription>
      <AlertAction>
        <Button
          data-testid="transcript-try-again"
          onClick={() => setLoadAttempt((attempt) => attempt + 1)}
          size="sm"
          variant="outline"
        >
          Try again
        </Button>
      </AlertAction>
    </Alert>
  ) : null;
  if (loadError && !content) {
    return loadNotice;
  }

  const reviewDraft = (draft: WordEdit) => {
    dispatchDraft({ id: draft.id, type: "activate" });
    setEditMode(true);
    const paragraph = paragraphAt(paragraphs, draft.index);
    if (paragraph >= 0) {
      virtualizer.scrollToIndex(paragraph, { align: "center" });
    }
  };

  const runStructural = (payload: Record<string, unknown>) => {
    if (!loaded) {
      return;
    }
    startTransition(async () => {
      const frozen = {
        ...payload,
        baseRevision: loaded.revision,
      };
      const intent = JSON.stringify(frozen);
      const key = `transcript-command:${sourceId}:${intent}`;
      try {
        const result = await correctTranscriptStructure(sourceId, {
          ...frozen,
          mutationKey: stableSessionUuid(key),
        });
        if (result.ok) {
          clearSessionUuid(key);
          dispatchDraft({ type: "closeAfterStructure" });
          router.refresh();
        } else if (!refused(result)) {
          setAssignError(result.message);
        }
      } catch {
        setAssignError(SAVE_FAILED_MESSAGE);
      }
    });
  };

  const selectedIdentity = edit?.identityId;
  const selectedWord = content?.words[edit?.index ?? -1];
  const tokens = structureText.trim().split(WHITESPACE).filter(Boolean);
  const canInsert = Boolean(selectedIdentity) || content?.words.length === 0;
  const [firstSpeaker] = content?.speakers ?? [];
  const currentSpeakerIdentity = selectedWord?.speaker
    ? loaded?.annotations.speakerIdentities[selectedWord.speaker]?.identityId
    : null;

  const items = virtualizer.getVirtualItems();
  return (
    <TooltipProvider>
      <div className="flex flex-col gap-3" data-revision={currentRevision}>
        <div className="flex flex-wrap items-center gap-2">
          <InputGroup className="w-56">
            <InputGroupAddon>
              <HugeiconsIcon icon={Search01Icon} />
            </InputGroupAddon>
            <InputGroupInput
              aria-label="Search the transcript"
              data-testid="transcript-search"
              onChange={(event) => onQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  onQuery("");
                }
                if (event.key === "Enter") {
                  event.preventDefault();
                  step(event.shiftKey ? -1 : 1);
                }
              }}
              placeholder="Search"
              value={query}
            />
            <InputGroupAddon align="inline-end">
              {query.trim() ? (
                <InputGroupText data-testid="transcript-match-count">
                  {matches.length === 0
                    ? "No matches"
                    : `${safeMatchIndex + 1}/${matches.length}`}
                </InputGroupText>
              ) : null}
              <InputGroupButton
                aria-label="Previous match"
                data-testid="transcript-previous"
                disabled={matches.length === 0}
                onClick={() => step(-1)}
                size="icon-xs"
              >
                <HugeiconsIcon icon={ArrowUp01Icon} />
              </InputGroupButton>
              <InputGroupButton
                aria-label="Next match"
                data-testid="transcript-next"
                disabled={matches.length === 0}
                onClick={() => step(1)}
                size="icon-xs"
              >
                <HugeiconsIcon icon={ArrowDown01Icon} />
              </InputGroupButton>
              {query ? (
                <InputGroupButton
                  aria-label="Clear search"
                  onClick={() => onQuery("")}
                  size="icon-xs"
                >
                  <HugeiconsIcon icon={Cancel01Icon} />
                </InputGroupButton>
              ) : null}
            </InputGroupAddon>
          </InputGroup>
          <Toggle
            data-testid="transcript-follow"
            onPressedChange={setFollow}
            pressed={follow}
            size="sm"
            variant="outline"
          >
            <HugeiconsIcon icon={ArrowDownDoubleIcon} />
            Follow
          </Toggle>
          <Toggle
            data-testid="transcript-edit-mode"
            disabled={readOnly}
            onPressedChange={(next) => {
              setEditMode(next);
              dispatchDraft({ type: "close" });
            }}
            pressed={editMode}
            size="sm"
            variant="outline"
          >
            <HugeiconsIcon icon={Edit02Icon} />
            Edit
          </Toggle>
          <Button
            data-testid="transcript-speakers"
            disabled={readOnly}
            onClick={openRename}
            size="sm"
            variant="outline"
          >
            <HugeiconsIcon icon={UserMultipleIcon} />
            Speakers
          </Button>
          {(["srt", "vtt"] as const).map((format) => (
            <Button
              key={format}
              // An anchor, so the browser downloads rather than posts, and
              // `nativeButton` off because Base UI otherwise expects a real
              // <button> here and says so.
              nativeButton={false}
              render={
                <a
                  data-testid={`transcript-${format}`}
                  download={exportName(title, format)}
                  href={`/api/sources/${sourceId}/transcript.${format}?revision=${baseRevision}`}
                />
              }
              size="sm"
              variant="outline"
            >
              <HugeiconsIcon icon={FileDownloadIcon} />
              {format.toUpperCase()}
            </Button>
          ))}
        </div>

        <div
          className="flex flex-wrap items-center gap-2"
          data-testid="transcript-structure-controls"
          hidden={readOnly || !editMode}
        >
          <InputGroup className="w-44">
            <InputGroupInput
              aria-label="Structural edit words"
              onChange={(event) => setStructureText(event.target.value)}
              placeholder="Words for split or insert"
              value={structureText}
            />
          </InputGroup>
          <InputGroup className="w-28">
            <InputGroupInput
              aria-label="Inserted word start milliseconds"
              inputMode="numeric"
              onChange={(event) => setStructureStart(event.target.value)}
              placeholder="start ms"
              value={structureStart}
            />
          </InputGroup>
          <InputGroup className="w-28">
            <InputGroupInput
              aria-label="Inserted word end milliseconds"
              inputMode="numeric"
              onChange={(event) => setStructureEnd(event.target.value)}
              placeholder="end ms"
              value={structureEnd}
            />
          </InputGroup>
          <span className="text-muted-foreground text-xs">
            Manual millisecond timings are approximate; confirm them against
            playback.
          </span>
          <Button
            disabled={pending || !selectedIdentity}
            onClick={() =>
              runStructural({ action: "delete", targetIds: [selectedIdentity] })
            }
            size="sm"
            variant="outline"
          >
            Delete word
          </Button>
          <Button
            disabled={pending || !selectedIdentity || tokens.length === 0}
            onClick={() =>
              runStructural({
                action: "split",
                targetId: selectedIdentity,
                tokens,
              })
            }
            size="sm"
            variant="outline"
          >
            Split word
          </Button>
          <Button
            disabled={
              pending ||
              !canInsert ||
              tokens.length === 0 ||
              !NONNEGATIVE_INTEGER.test(structureStart) ||
              !NONNEGATIVE_INTEGER.test(structureEnd)
            }
            onClick={() =>
              runStructural({
                action: "insert",
                anchorId: selectedIdentity ?? null,
                endMs: Number(structureEnd),
                side: "before",
                speakerIdentityId: currentSpeakerIdentity,
                startMs: Number(structureStart),
                tokens,
              })
            }
            size="sm"
            variant="outline"
          >
            Insert before
          </Button>
          <Button
            disabled={
              pending ||
              !canInsert ||
              tokens.length === 0 ||
              !NONNEGATIVE_INTEGER.test(structureStart) ||
              !NONNEGATIVE_INTEGER.test(structureEnd)
            }
            onClick={() =>
              runStructural({
                action: "insert",
                anchorId: selectedIdentity ?? null,
                endMs: Number(structureEnd),
                side: "after",
                speakerIdentityId: currentSpeakerIdentity,
                startMs: Number(structureStart),
                tokens,
              })
            }
            size="sm"
            variant="outline"
          >
            Insert after
          </Button>
          {content?.words.length === 0 ? (
            <span className="text-muted-foreground text-xs">
              The first inserted words will use Unknown speaker.
            </span>
          ) : null}
          <Button
            disabled={
              pending ||
              !selectedIdentity ||
              !loaded?.annotations.wordIdentities[(edit?.index ?? -1) + 1]
            }
            onClick={() =>
              runStructural({
                action: "merge",
                targetIds: [
                  selectedIdentity,
                  loaded?.annotations.wordIdentities[(edit?.index ?? -1) + 1]
                    ?.id,
                ],
              })
            }
            size="sm"
            variant="outline"
          >
            Merge next
          </Button>
          <InputGroup className="w-28">
            <InputGroupInput
              aria-label="Undo to revision"
              inputMode="numeric"
              onChange={(event) => setUndoRevision(event.target.value)}
              placeholder="revision"
              value={undoRevision}
            />
          </InputGroup>
          <Button
            disabled={
              pending ||
              !NONNEGATIVE_INTEGER.test(undoRevision) ||
              Number(undoRevision) >= (loaded?.revision ?? 0)
            }
            onClick={() =>
              runStructural({
                action: "undo",
                targetRevision: Number(undoRevision),
              })
            }
            size="sm"
            variant="outline"
          >
            Undo
          </Button>
          {content && content.speakers.length > 1 ? (
            <>
              <NativeSelect
                aria-label="Speaker identity to merge"
                onChange={(event) => setMergeSpeaker(event.target.value)}
                size="sm"
                value={mergeSpeaker}
              >
                <NativeSelectOption value="">Merge speaker…</NativeSelectOption>
                {content.speakers.slice(1).map((speaker) => (
                  <NativeSelectOption key={speaker} value={speaker}>
                    {displayLabels[speaker] ?? speaker}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
              <Button
                disabled={pending || !mergeSpeaker}
                onClick={() => {
                  const sourceIdentity =
                    loaded?.annotations.speakerIdentities[mergeSpeaker]
                      ?.identityId;
                  const targetIdentity = firstSpeaker
                    ? loaded?.annotations.speakerIdentities[firstSpeaker]
                        ?.identityId
                    : null;
                  if (sourceIdentity && targetIdentity) {
                    runStructural({
                      action: "merge_speakers",
                      sourceIdentityIds: [sourceIdentity],
                      targetIdentityId: targetIdentity,
                    });
                  }
                }}
                size="sm"
                variant="outline"
              >
                Merge into{" "}
                {displayLabels[firstSpeaker ?? ""] ?? "first speaker"}
              </Button>
            </>
          ) : null}
        </div>

        {stale ? (
          <Alert data-testid="transcript-stale">
            <AlertDescription>{STALE_REVISION_MESSAGE}</AlertDescription>
            <AlertAction>
              <Button
                data-testid="transcript-reload"
                onClick={() => {
                  setStale(false);
                  router.refresh();
                }}
                size="sm"
              >
                Reload
              </Button>
            </AlertAction>
          </Alert>
        ) : null}

        {loadNotice}

        <DraftNotices
          currentRevision={currentRevision}
          currentWord={currentWord}
          edit={edit}
          onKeep={() => {
            if (edit && loaded) {
              dispatchDraft({
                id: edit.id,
                original: content?.words[edit.index]?.text ?? edit.original,
                revision: loaded.revision,
                type: "review",
              });
            }
          }}
          onReview={reviewDraft}
          state={draftState}
        />

        {assignError ? (
          <Alert data-testid="transcript-assign-error" variant="destructive">
            <AlertDescription>{assignError}</AlertDescription>
            <AlertAction>
              <Button
                onClick={() => setAssignError(null)}
                size="sm"
                variant="outline"
              >
                Dismiss
              </Button>
            </AlertAction>
          </Alert>
        ) : null}

        {content?.words.length === 0 ? (
          <p className="text-muted-foreground text-sm" role="status">
            This revision contains no lexical text. You can insert words or undo
            to an earlier revision.
          </p>
        ) : null}

        {content ? null : (
          <div className="flex flex-col gap-3" data-testid="transcript-loading">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        )}

        {content ? (
          <div className="relative">
            <ScrollArea
              className="h-[28rem] rounded-lg border"
              data-testid="transcript-scroll"
              ref={attach}
            >
              <div className="px-3 py-2 text-sm" data-testid="transcript-words">
                <div
                  className="relative w-full"
                  style={{ height: `${virtualizer.getTotalSize()}px` }}
                >
                  {items.map((item) => {
                    const paragraph = paragraphs[item.index];
                    const [focusedFirstWord, focusedLastWord] = paragraph
                      ? focusedWords(paragraph, focusedMatch)
                      : [-1, -1];
                    return paragraph ? (
                      <div
                        className="absolute top-0 left-0 w-full"
                        data-index={item.index}
                        key={item.key}
                        ref={virtualizer.measureElement}
                        style={{ transform: `translateY(${item.start}px)` }}
                      >
                        <TranscriptParagraph
                          activeWord={
                            item.index === activeParagraph ? activeWord : -1
                          }
                          edit={item.index === editParagraph ? edit : null}
                          focusedFirstWord={focusedFirstWord}
                          focusedLastWord={focusedLastWord}
                          labels={displayLabels}
                          onAssign={assignSpeaker}
                          onCancelEdit={cancelEdit}
                          onDraft={draftWord}
                          onRenameSpeakers={openRename}
                          onSaveEdit={saveWord}
                          paragraph={paragraph}
                          readOnly={readOnly}
                          speakers={content.speakers}
                          words={content.words}
                        />
                      </div>
                    ) : null;
                  })}
                </div>
              </div>
            </ScrollArea>
            {follow || activeParagraph < 0 ? null : (
              <Button
                className="absolute right-4 bottom-4 shadow"
                data-testid="transcript-jump"
                onClick={() => setFollow(true)}
                size="sm"
              >
                <HugeiconsIcon icon={ArrowDownDoubleIcon} />
                Jump to current
              </Button>
            )}
          </div>
        ) : null}
      </div>
      <TranscriptSpeakersDialog
        error={speakersError}
        labels={displayLabels}
        onOpenChange={setSpeakersOpen}
        onSave={saveSpeakers}
        open={speakersOpen}
        pending={pending}
        speakers={content?.speakers ?? []}
      />
    </TooltipProvider>
  );
}

function DraftNotices({
  currentRevision,
  currentWord,
  edit,
  onKeep,
  onReview,
  state,
}: {
  currentRevision: number | null;
  currentWord: string | undefined;
  edit: WordEdit | null;
  onKeep: () => void;
  onReview: (draft: WordEdit) => void;
  state: WordDrafts;
}) {
  return (
    <>
      {state.drafts
        .filter((draft) => draft.id !== state.activeId)
        .map((draft) => (
          <Alert data-testid="transcript-saved-draft" key={draft.id}>
            <AlertDescription>
              {draft.pending ? "Saving" : "Unsaved correction"}: “
              {draft.draft ?? draft.original}”
              {draft.error ? ` · ${draft.error}` : null}
            </AlertDescription>
            <AlertAction>
              <Button
                onClick={() => onReview(draft)}
                size="sm"
                variant="outline"
              >
                Review draft
              </Button>
            </AlertAction>
          </Alert>
        ))}

      {edit &&
      currentRevision !== null &&
      edit.baseRevision !== currentRevision ? (
        <Alert data-testid="transcript-draft-review">
          <AlertDescription>
            Your draft for “{edit.original}” is still here. The current word is
            “{currentWord ?? "no longer available"}”. Review it before saving.
          </AlertDescription>
          <AlertAction>
            <Button
              data-testid="transcript-review-current-word"
              disabled={edit.pending || !currentWord}
              onClick={onKeep}
              size="sm"
              variant="outline"
            >
              Keep draft on this word
            </Button>
          </AlertAction>
        </Alert>
      ) : null}
    </>
  );
}

function readerContext(loaded: LoadedRevision | null, state: WordDrafts) {
  const content = loaded?.content ?? null;
  const edit =
    state.drafts.find((draft) => draft.id === state.activeId) ?? null;
  return {
    content,
    currentRevision: loaded?.revision ?? null,
    currentWord: content?.words[edit?.index ?? -1]?.text,
    edit,
  };
}
