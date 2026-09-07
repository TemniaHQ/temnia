"use client";

import type { TranscriptWord } from "@temnia/contracts";
import { memo } from "react";
import {
  TranscriptWordEditor,
  type WordEdit,
} from "@/components/sources/transcript-word-editor";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { formatDuration } from "@/lib/sources/labels";
import { speakerName } from "@/lib/transcript/captions";
import { LOW_CONFIDENCE, type Paragraph } from "@/lib/transcript/paragraphs";

interface TranscriptParagraphProps {
  /** The word the playhead is in, or -1 when it is not in this paragraph. */
  activeWord: number;
  /** The open correction, or null when it is not in this paragraph. */
  edit: WordEdit | null;
  /** The search hit that has the focus, or -1. */
  focusedWord: number;
  labels: Readonly<Record<string, string>>;
  onAssign: (utteranceIndex: number, speaker: string) => void;
  onCancelEdit: () => void;
  onRenameSpeakers: () => void;
  onSaveEdit: (index: number, text: string) => void;
  paragraph: Paragraph;
  speakers: readonly string[];
  words: readonly TranscriptWord[];
}

const WORD_CLASS =
  "cursor-pointer rounded-sm px-0.5 text-left transition-colors hover:bg-muted " +
  "data-[active=true]:bg-primary/15 data-[active=true]:text-foreground " +
  "data-[focused=true]:bg-amber-300/50 data-[focused=true]:ring-1 data-[focused=true]:ring-amber-500/60 " +
  "data-[timing=interpolated]:text-muted-foreground " +
  "data-[uncertain=true]:underline data-[uncertain=true]:decoration-dotted data-[uncertain=true]:underline-offset-4";

function confidencePercent(confidence: number): string {
  return `${Math.round(confidence * 100)}%`;
}

/**
 * One display paragraph: a speaker chip, a timestamp, and its words.
 *
 * Memoised on purpose. The playhead moves several times a second and the only
 * rows that may re-render are the one the active word is now in and the one it
 * has just left, so every prop here is either a stable value or -1. The words
 * carry no click handler of their own for the same reason: the index is in a
 * data attribute and one handler on the list reads it (S2 plan §3).
 */
function ParagraphRow({
  activeWord,
  edit,
  focusedWord,
  labels,
  onAssign,
  onCancelEdit,
  onRenameSpeakers,
  onSaveEdit,
  paragraph,
  speakers,
  words,
}: TranscriptParagraphProps) {
  const name = speakerName(paragraph.speaker, labels) ?? "Unknown speaker";
  const spoken: React.ReactNode[] = [];
  for (
    let index = paragraph.firstWord;
    index <= paragraph.lastWord;
    index += 1
  ) {
    const word = words[index];
    if (!word) {
      continue;
    }
    if (edit?.index === index) {
      spoken.push(
        <TranscriptWordEditor
          edit={edit}
          initial={word.text}
          key={index}
          onCancel={onCancelEdit}
          onSave={onSaveEdit}
        />
      );
    } else {
      spoken.push(
        <Word
          active={index === activeWord}
          focused={index === focusedWord}
          index={index}
          key={index}
          word={word}
        />
      );
    }
    // The separator sits outside the button so a double-click on a word never
    // selects the space beside it, and a copy of the paragraph still reads.
    spoken.push(<span key={`gap-${index}`}> </span>);
  }

  return (
    <div className="flex gap-3 py-2" data-paragraph={paragraph.firstWord}>
      <div className="flex w-28 shrink-0 flex-col items-start gap-0.5">
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                className="h-6 max-w-full justify-start truncate px-1.5 font-medium text-xs"
                data-testid="speaker-chip"
                size="sm"
                variant="ghost"
              />
            }
          >
            {name}
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            {speakers.map((speaker) => (
              <DropdownMenuItem
                key={speaker}
                onClick={() => onAssign(paragraph.utteranceIndex, speaker)}
              >
                {speakerName(speaker, labels)}
              </DropdownMenuItem>
            ))}
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={onRenameSpeakers}>
              Rename…
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <span className="px-1.5 text-muted-foreground text-xs tabular-nums">
          {formatDuration(paragraph.startMs)}
        </span>
      </div>
      <p className="flex-1 text-pretty leading-7">{spoken}</p>
    </div>
  );
}

function Word({
  active,
  focused,
  index,
  word,
}: {
  active: boolean;
  focused: boolean;
  index: number;
  word: TranscriptWord;
}) {
  const uncertain =
    word.confidence !== null && word.confidence < LOW_CONFIDENCE;
  const button = (
    <button
      className={WORD_CLASS}
      data-active={active}
      data-focused={focused}
      data-timing={word.timing}
      data-uncertain={uncertain}
      data-word={index}
      type="button"
    >
      {word.text}
    </button>
  );
  if (!uncertain) {
    return button;
  }
  return (
    <Tooltip>
      <TooltipTrigger render={button} />
      <TooltipContent>
        {`The engine was ${confidencePercent(word.confidence ?? 0)} sure of this word.`}
      </TooltipContent>
    </Tooltip>
  );
}

export const TranscriptParagraph = memo(ParagraphRow);
