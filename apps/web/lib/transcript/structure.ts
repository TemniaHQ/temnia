import {
  type TranscriptCorrectionCommand,
  type TranscriptRevisionAnnotations,
  TranscriptRevisionAnnotationsSchema,
  type TranscriptV1,
  TranscriptV1Schema,
} from "@temnia/contracts";
import { speakerName } from "@/lib/transcript/captions";
import { deriveUtterances, speakersOf } from "@/lib/transcript/utterances";

export interface StructuralResult {
  affectedIdentityIds: string[];
  annotations: TranscriptRevisionAnnotations;
  content: TranscriptV1;
  deletedIdentityIds: string[];
}

export function legacyAnnotations(
  transcriptId: string,
  machineRevision: number,
  content: TranscriptV1,
  labels: Readonly<Record<string, string>> = {}
): TranscriptRevisionAnnotations {
  const prefix = `${transcriptId}:${machineRevision}`;
  return TranscriptRevisionAnnotationsSchema.parse({
    speakerIdentities: Object.fromEntries(
      content.speakers.map((speaker, index) => [
        speaker,
        {
          identityId: `${prefix}:speaker:${index}`,
          label: labels[speaker] ?? speakerName(speaker, {}) ?? speaker,
          parentIdentityIds: [],
        },
      ])
    ),
    version: 1,
    wordIdentities: content.words.map((_, index) => ({
      id: `${prefix}:word:${index}`,
      parentIds: [],
      timingOrigin: "provider",
    })),
  });
}

function identityIndex(
  annotations: TranscriptRevisionAnnotations,
  identity: string
): number {
  const index = annotations.wordIdentities.findIndex(
    (item) => item.id === identity
  );
  if (index < 0) {
    throw new Error("That word identity is absent from this revision.");
  }
  return index;
}

function contiguous(indices: number[]): void {
  const sorted = [...indices].sort((left, right) => left - right);
  if (
    new Set(indices).size !== indices.length ||
    sorted.some(
      (value, index) => index > 0 && value !== (sorted[index - 1] ?? 0) + 1
    )
  ) {
    throw new Error("The selected words must be unique and contiguous.");
  }
}

function generatedId(
  command: TranscriptCorrectionCommand,
  ordinal: number
): string {
  return `${command.mutationKey}:word:${ordinal}`;
}

function interpolateMs(
  startMs: number,
  endMs: number,
  ordinal: number,
  count: number
): number {
  const start = BigInt(startMs);
  const duration = BigInt(endMs) - start;
  return Number(start + (duration * BigInt(ordinal)) / BigInt(count));
}

// biome-ignore lint/complexity/noExcessiveCognitiveComplexity: the exhaustive command interpreter keeps each structural rule in one visible transaction-free switch
export function applyStructuralCorrection(
  original: TranscriptV1,
  originalAnnotations: TranscriptRevisionAnnotations,
  command: TranscriptCorrectionCommand
): StructuralResult {
  const content = TranscriptV1Schema.parse(structuredClone(original));
  const annotations = TranscriptRevisionAnnotationsSchema.parse(
    structuredClone(originalAnnotations)
  );
  if (annotations.wordIdentities.length !== content.words.length) {
    throw new Error("Word annotations do not match transcript word order.");
  }
  for (const speaker of content.speakers) {
    if (!annotations.speakerIdentities[speaker]) {
      throw new Error("Speaker annotations do not match the transcript.");
    }
  }
  const affected: string[] = [];
  const deleted: string[] = [];

  if (command.action === "replace") {
    const index = identityIndex(annotations, command.targetId);
    const word = content.words[index];
    if (!word) {
      throw new Error("That word is absent from this revision.");
    }
    word.text = command.text;
    affected.push(command.targetId);
  } else if (command.action === "delete") {
    const indices = command.targetIds.map((id) =>
      identityIndex(annotations, id)
    );
    contiguous(indices);
    const start = Math.min(...indices);
    content.words.splice(start, indices.length);
    annotations.wordIdentities.splice(start, indices.length);
    affected.push(...command.targetIds);
    deleted.push(...command.targetIds);
  } else if (command.action === "insert") {
    if (command.startMs > command.endMs || command.endMs > content.durationMs) {
      throw new Error("Inserted word timing lies outside the source.");
    }
    const anchor =
      command.anchorId === null
        ? null
        : identityIndex(annotations, command.anchorId);
    const index =
      anchor === null
        ? content.words.length
        : anchor + (command.side === "after" ? 1 : 0);
    const before = content.words[index - 1];
    const after = content.words[index];
    if (
      (before && command.startMs < before.endMs) ||
      (after && command.endMs > after.startMs)
    ) {
      throw new Error(
        "Inserted timing is out of order with neighboring words."
      );
    }
    const speakerRaw =
      Object.entries(annotations.speakerIdentities).find(
        ([, value]) => value.identityId === command.speakerIdentityId
      )?.[0] ?? null;
    if (command.speakerIdentityId !== null && speakerRaw === null) {
      throw new Error("That speaker identity is absent from this revision.");
    }
    const words = command.tokens.map((text, ordinal) => ({
      confidence: null,
      endMs: interpolateMs(
        command.startMs,
        command.endMs,
        ordinal + 1,
        command.tokens.length
      ),
      speaker: speakerRaw,
      startMs: interpolateMs(
        command.startMs,
        command.endMs,
        ordinal,
        command.tokens.length
      ),
      text,
      timing: "interpolated" as const,
    }));
    const identities = command.tokens.map((_, ordinal) => ({
      id: generatedId(command, ordinal),
      parentIds: [],
      timingOrigin: "manual" as const,
    }));
    content.words.splice(index, 0, ...words);
    annotations.wordIdentities.splice(index, 0, ...identities);
    affected.push(...identities.map((identity) => identity.id));
  } else if (command.action === "split") {
    const index = identityIndex(annotations, command.targetId);
    const word = content.words[index];
    if (!word) {
      throw new Error("That word is absent from this revision.");
    }
    const words = command.tokens.map((text, ordinal) => ({
      ...word,
      confidence: null,
      endMs: interpolateMs(
        word.startMs,
        word.endMs,
        ordinal + 1,
        command.tokens.length
      ),
      startMs: interpolateMs(
        word.startMs,
        word.endMs,
        ordinal,
        command.tokens.length
      ),
      text,
      timing: "interpolated" as const,
    }));
    const identities = command.tokens.map((_, ordinal) => ({
      id: generatedId(command, ordinal),
      parentIds: [command.targetId],
      timingOrigin: "manual" as const,
    }));
    content.words.splice(index, 1, ...words);
    annotations.wordIdentities.splice(index, 1, ...identities);
    affected.push(
      command.targetId,
      ...identities.map((identity) => identity.id)
    );
    deleted.push(command.targetId);
  } else if (command.action === "merge") {
    const indices = command.targetIds.map((id) =>
      identityIndex(annotations, id)
    );
    contiguous(indices);
    const start = Math.min(...indices);
    const words = content.words.slice(start, start + indices.length);
    const speakers = new Set(words.map((word) => word.speaker));
    if (speakers.size !== 1) {
      throw new Error("Reassign speakers before merging these words.");
    }
    const [first] = words;
    const last = words.at(-1);
    if (!(first && last)) {
      throw new Error("No words were selected.");
    }
    const identity = {
      id: generatedId(command, 0),
      parentIds: [
        ...new Set(
          indices.flatMap((index) => {
            const prior = annotations.wordIdentities[index];
            return prior ? [prior.id, ...prior.parentIds] : [];
          })
        ),
      ],
      timingOrigin: "manual" as const,
    };
    content.words.splice(start, words.length, {
      ...first,
      confidence: null,
      endMs: last.endMs,
      text: words.map((word) => word.text).join(" "),
      timing: "interpolated",
    });
    annotations.wordIdentities.splice(start, words.length, identity);
    affected.push(...command.targetIds, identity.id);
    deleted.push(...command.targetIds);
  } else if (command.action === "rename_speakers") {
    for (const [identityId, label] of Object.entries(command.labels)) {
      const speaker = Object.values(annotations.speakerIdentities).find(
        (item) => item.identityId === identityId
      );
      if (!speaker) {
        throw new Error("That speaker identity is absent from this revision.");
      }
      speaker.label = label;
      affected.push(identityId);
    }
  } else if (command.action === "reassign_speaker") {
    const raw = Object.entries(annotations.speakerIdentities).find(
      ([, item]) => item.identityId === command.targetSpeakerIdentityId
    )?.[0];
    if (!raw) {
      throw new Error("That speaker identity is absent from this revision.");
    }
    for (const id of command.targetIds) {
      const word = content.words[identityIndex(annotations, id)];
      if (!word) {
        throw new Error("That word is absent from this revision.");
      }
      word.speaker = raw;
    }
    affected.push(...command.targetIds, command.targetSpeakerIdentityId);
  } else if (command.action === "merge_speakers") {
    const target = Object.entries(annotations.speakerIdentities).find(
      ([, item]) => item.identityId === command.targetIdentityId
    );
    if (!target) {
      throw new Error("The target speaker identity is absent.");
    }
    const sources = new Set(command.sourceIdentityIds);
    if (sources.has(command.targetIdentityId)) {
      throw new Error("A speaker cannot be merged into itself.");
    }
    for (const sourceId of sources) {
      if (
        !Object.values(annotations.speakerIdentities).some(
          (item) => item.identityId === sourceId
        )
      ) {
        throw new Error("A source speaker identity is absent.");
      }
    }
    for (const word of content.words) {
      const identity = word.speaker
        ? annotations.speakerIdentities[word.speaker]?.identityId
        : null;
      if (identity && sources.has(identity)) {
        word.speaker = target[0];
      }
    }
    target[1].parentIdentityIds = [
      ...new Set([
        ...target[1].parentIdentityIds,
        ...command.sourceIdentityIds,
      ]),
    ];
    affected.push(command.targetIdentityId, ...command.sourceIdentityIds);
  } else {
    throw new Error(
      "Undo requires loading its explicitly named historical revision."
    );
  }
  content.utterances = deriveUtterances(content.words);
  content.speakers = speakersOf(content.words);
  return {
    affectedIdentityIds: [...new Set(affected)],
    annotations: TranscriptRevisionAnnotationsSchema.parse(annotations),
    content: TranscriptV1Schema.parse(content),
    deletedIdentityIds: [...new Set(deleted)],
  };
}
