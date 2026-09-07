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
import type { TranscriptV1 } from "@temnia/contracts";
import { selectTime, usePlayer } from "@videojs/react";
import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  useTransition,
} from "react";
import {
  correctTranscript,
  type TranscriptActionResult,
  updateSpeakerLabels,
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
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { Toggle } from "@/components/ui/toggle";
import { TooltipProvider } from "@/components/ui/tooltip";
import { fetchRevision } from "@/lib/transcript/content";
import { STALE_REVISION_MESSAGE } from "@/lib/transcript/edits";
import {
  buildParagraphs,
  findMatches,
  paragraphAt,
  wordAt,
} from "@/lib/transcript/paragraphs";

interface TranscriptReaderProps {
  /** The revision every edit on this screen is made against. */
  baseRevision: number;
  labels: Readonly<Record<string, string>>;
  revisionUrl: string;
  sourceId: string;
  /** Names the two caption downloads, so a folder of exports is readable. */
  title: string;
}

/** A row is about four lines; the virtualiser measures the real one on mount. */
const ESTIMATED_ROW_PX = 92;

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

/**
 * The transcript, once there is one.
 *
 * Reads the revision from the org-scoped media proxy once, builds the display
 * paragraphs, and virtualises them: a 2.5-hour episode is about 28 thousand
 * words, and only the rows on screen may exist in the DOM. The playhead comes
 * from the player store the source page provides, so following and seeking
 * share the one player with the pane beside it rather than a lifted ref.
 */
export function TranscriptReader({
  baseRevision,
  labels,
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

  const [content, setContent] = useState<TranscriptV1 | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Counted up by Try again. The fetch effect depends on it, so a retry is a
  // new request; a router refresh alone re-rendered the same client component
  // with the same URL and fetched nothing (S2 review, I20).
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [assignError, setAssignError] = useState<string | null>(null);
  const [viewport, setViewport] = useState<HTMLElement | null>(null);
  const [follow, setFollow] = useState(true);
  const [editMode, setEditMode] = useState(false);
  const [edit, setEdit] = useState<WordEdit | null>(null);
  const [query, setQuery] = useState("");
  const [matchIndex, setMatchIndex] = useState(0);
  const [stale, setStale] = useState(false);
  const [speakersOpen, setSpeakersOpen] = useState(false);
  const [speakersError, setSpeakersError] = useState<string | null>(null);

  // biome-ignore lint/correctness/useExhaustiveDependencies: `loadAttempt` is the retry; a new value is a new request
  useEffect(() => {
    const controller = new AbortController();
    setLoadError(null);
    fetchRevision(revisionUrl, controller.signal)
      .then((loaded) => setContent(loaded))
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
  }, [revisionUrl, loadAttempt]);

  const words = content?.words;
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
  const focusedWord = matches[matchIndex] ?? -1;
  const focusedParagraph = paragraphAt(paragraphs, focusedWord);
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

  // Every save catches: an action that throws (the network, the store, the
  // database) used to leave the editor pending for ever with nothing said,
  // and a speaker reassignment that was refused for a reason other than
  // staleness said nothing at all (S2 review, I22). The draft is kept.
  const saveWord = useCallback(
    (index: number, text: string) => {
      setEdit({ draft: text, error: null, index, pending: true });
      startTransition(async () => {
        let result: TranscriptActionResult;
        try {
          result = await correctTranscript(sourceId, baseRevision, [
            { index, text },
          ]);
        } catch {
          setEdit({
            draft: text,
            error: SAVE_FAILED_MESSAGE,
            index,
            pending: false,
          });
          return;
        }
        if (result.ok) {
          setEdit(null);
          router.refresh();
          return;
        }
        if (refused(result)) {
          setEdit(null);
          return;
        }
        setEdit({ draft: text, error: result.message, index, pending: false });
      });
    },
    [baseRevision, refused, router, sourceId]
  );

  const assignSpeaker = useCallback(
    (utteranceIndex: number, speaker: string) => {
      setAssignError(null);
      startTransition(async () => {
        let result: TranscriptActionResult;
        try {
          result = await correctTranscript(sourceId, baseRevision, {
            speaker,
            utteranceIndex,
          });
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
    [baseRevision, refused, router, sourceId]
  );

  const saveSpeakers = useCallback(
    (next: Record<string, string>) => {
      setSpeakersError(null);
      startTransition(async () => {
        let result: TranscriptActionResult;
        try {
          result = await updateSpeakerLabels(sourceId, next);
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
    [router, sourceId]
  );

  const openRename = useCallback(() => setSpeakersOpen(true), []);
  const cancelEdit = useCallback(() => setEdit(null), []);
  const draftWord = useCallback(
    (text: string) =>
      setEdit((current) => (current ? { ...current, draft: text } : current)),
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
      if (editMode) {
        setEdit({ draft: null, error: null, index, pending: false });
        return;
      }
      const word = content?.words[index];
      if (word && seek) {
        // The promise resolves at the seeked position and nothing here waits
        // for it; a seek that cannot happen is the player's to report.
        seek(word.startMs / 1000).catch(() => undefined);
      }
    };
    viewport.addEventListener("click", onWordClick);
    return () => viewport.removeEventListener("click", onWordClick);
  }, [content, editMode, seek, viewport]);

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

  if (loadError) {
    return (
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
    );
  }

  const items = virtualizer.getVirtualItems();
  return (
    <TooltipProvider>
      <div className="flex flex-col gap-3">
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
                    : `${matchIndex + 1}/${matches.length}`}
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
            onPressedChange={(next) => {
              setEditMode(next);
              setEdit(null);
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
                  href={`/api/sources/${sourceId}/transcript.${format}`}
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
                          focusedWord={
                            item.index === focusedParagraph ? focusedWord : -1
                          }
                          labels={labels}
                          onAssign={assignSpeaker}
                          onCancelEdit={cancelEdit}
                          onDraft={draftWord}
                          onRenameSpeakers={openRename}
                          onSaveEdit={saveWord}
                          paragraph={paragraph}
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
        labels={labels}
        onOpenChange={setSpeakersOpen}
        onSave={saveSpeakers}
        open={speakersOpen}
        pending={pending}
        speakers={content?.speakers ?? []}
      />
    </TooltipProvider>
  );
}
