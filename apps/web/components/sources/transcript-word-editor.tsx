"use client";

import { Cancel01Icon, Tick02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { FieldError } from "@/components/ui/field";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group";

export interface WordEdit {
  /**
   * What the person has typed, or null until they touch the input. It lives in
   * the reader's state, not in this component's: the row this editor sits in
   * is virtualised and unmounts when it scrolls out of the retained range,
   * which took the draft with it (S2 review, I21).
   */
  draft: string | null;
  /** Why the last save was refused, when the edit itself was the problem. */
  error: string | null;
  index: number;
  pending: boolean;
}

interface TranscriptWordEditorProps {
  edit: WordEdit;
  initial: string;
  onCancel: () => void;
  onDraft: (text: string) => void;
  onSave: (index: number, text: string) => void;
}

/**
 * One word, open for correction.
 *
 * Inline and in place, so the word keeps its position in the sentence while it
 * is being changed and the reader can still see what is on either side of it.
 * A refusal from the server is shown here rather than at the top of the tab:
 * an edit that does not apply is about this word, and the text stays in the
 * input so nothing a person typed is thrown away.
 */
export function TranscriptWordEditor({
  edit,
  initial,
  onCancel,
  onDraft,
  onSave,
}: TranscriptWordEditorProps) {
  const text = edit.draft ?? initial;
  const save = () => {
    if (text.trim()) {
      onSave(edit.index, text.trim());
    }
  };
  return (
    <span className="inline-flex flex-col gap-1 align-middle">
      <InputGroup className="w-56">
        <InputGroupInput
          aria-label="Word"
          autoFocus
          data-testid="transcript-word-input"
          disabled={edit.pending}
          onChange={(event) => onDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              save();
            }
            if (event.key === "Escape") {
              event.preventDefault();
              onCancel();
            }
          }}
          value={text}
        />
        <InputGroupAddon align="inline-end">
          <InputGroupButton
            aria-label="Save word"
            data-testid="transcript-word-save"
            disabled={edit.pending || !text.trim()}
            onClick={save}
            size="icon-xs"
          >
            <HugeiconsIcon icon={Tick02Icon} />
          </InputGroupButton>
          <InputGroupButton
            aria-label="Cancel"
            data-testid="transcript-word-cancel"
            disabled={edit.pending}
            onClick={onCancel}
            size="icon-xs"
          >
            <HugeiconsIcon icon={Cancel01Icon} />
          </InputGroupButton>
        </InputGroupAddon>
      </InputGroup>
      {edit.error ? (
        <FieldError data-testid="transcript-word-error">
          {edit.error}
        </FieldError>
      ) : null}
    </span>
  );
}
