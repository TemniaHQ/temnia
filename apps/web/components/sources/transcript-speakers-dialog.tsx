"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { speakerName } from "@/lib/transcript/captions";

interface TranscriptSpeakersDialogProps {
  error: string | null;
  labels: Readonly<Record<string, string>>;
  onOpenChange: (open: boolean) => void;
  onSave: (labels: Record<string, string>) => void;
  open: boolean;
  pending: boolean;
  speakers: readonly string[];
}

/** Two ids with one name are one speaker, on screen and in every export. */
export const MERGE_NOTICE = "This will merge the two speakers.";

function trimmed(draft: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [id, name] of Object.entries(draft)) {
    const value = name.trim();
    if (value) {
      out[id] = value;
    }
  }
  return out;
}

/** True when two different ids have been given the same name. */
export function willMerge(draft: Record<string, string>): boolean {
  const names = Object.values(trimmed(draft)).map((name) =>
    name.toLocaleLowerCase()
  );
  return new Set(names).size !== names.length;
}

/**
 * Rename the speakers a diarizer only knows as "0" and "1".
 *
 * Names live on the transcript row, so a rename costs no revision and no
 * storage. Giving two ids the same name is the manual merge over-segmented
 * diarization needs, and it is announced before Save rather than discovered
 * afterwards; nothing merges on its own.
 */
export function TranscriptSpeakersDialog({
  error,
  labels,
  onOpenChange,
  onSave,
  open,
  pending,
  speakers,
}: TranscriptSpeakersDialogProps) {
  const [draft, setDraft] = useState<Record<string, string>>({});

  useEffect(() => {
    if (open) {
      setDraft(
        Object.fromEntries(speakers.map((id) => [id, labels[id] ?? ""]))
      );
    }
  }, [labels, open, speakers]);

  const merging = willMerge(draft);
  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent data-testid="speakers-dialog">
        <DialogHeader>
          <DialogTitle>Speakers</DialogTitle>
          <DialogDescription>
            The names here are used in the transcript and in every caption
            export. They are not part of a revision, so renaming one costs
            nothing.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          {speakers.map((id) => (
            <Field key={id}>
              <FieldLabel htmlFor={`speaker-${id}`}>
                {speakerName(id, {})}
              </FieldLabel>
              <Input
                autoComplete="off"
                data-testid={`speaker-name-${id}`}
                id={`speaker-${id}`}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    [id]: event.target.value,
                  }))
                }
                placeholder={speakerName(id, {}) ?? id}
                value={draft[id] ?? ""}
              />
            </Field>
          ))}
          {speakers.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No speakers were identified in this recording.
            </p>
          ) : null}
          {merging ? (
            <p
              className="text-muted-foreground text-sm"
              data-testid="speakers-merge-notice"
            >
              {MERGE_NOTICE}
            </p>
          ) : null}
          {error ? <FieldError>{error}</FieldError> : null}
        </div>
        <DialogFooter>
          <Button onClick={() => onOpenChange(false)} variant="outline">
            Cancel
          </Button>
          <Button
            data-testid="speakers-save"
            disabled={pending || speakers.length === 0}
            onClick={() => onSave(trimmed(draft))}
          >
            {pending ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
