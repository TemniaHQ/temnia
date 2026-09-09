"use client";

import { VideoPlayer } from "@videojs/react/video";
import dynamic from "next/dynamic";
import type { ReactNode } from "react";
import { ChapterPanel } from "@/components/sources/chapter-panel";
import { SourceTimestamp } from "@/components/sources/source-timestamp";
import { TranscriptPanel } from "@/components/sources/transcript-panel";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { HarnessAvailability } from "@/lib/harness/config";
import type { ChapterView } from "@/lib/harness/queries";
import {
  formatBytes,
  formatDuration,
  formatElapsed,
  statusLabel,
} from "@/lib/sources/labels";
import type { TranscriptRowSummary } from "@/lib/transcript/state";

const SourcePlayer = dynamic(
  () => import("./source-player").then((m) => m.SourcePlayer),
  {
    loading: () => <Skeleton className="aspect-video w-full rounded-lg" />,
    ssr: false,
  }
);

interface SourceSummary {
  audioChannels: number | null;
  audioCodec: string | null;
  createdAt: string;
  durationMs: number | null;
  fps: string | null;
  height: number | null;
  id: string;
  originalFilename: string;
  readyAt: string | null;
  sizeBytes: number;
  status: "uploading" | "uploaded" | "processing" | "ready" | "failed";
  title: string;
  uploadedAt: string | null;
  videoCodec: string | null;
  width: number | null;
}

interface ArtifactSummary {
  contentType: string;
  kind: string;
  metadata: Record<string, unknown>;
  sizeBytes: number;
  storageKey: string;
}

interface SourceWorkspaceProps {
  artifacts: ArtifactSummary[];
  chapterAvailability: HarnessAvailability;
  chapters: ChapterView;
  peaksUrl: string | null;
  playlistUrl: string | null;
  posterUrl: string | null;
  source: SourceSummary;
  speakerLabels: Record<string, string>;
  transcript: TranscriptRowSummary | null;
  transcriptAnnotationsUrl: string | null;
  transcriptRevisions: Array<{
    annotationsUrl: string;
    revision: number;
    url: string;
  }>;
  /** The media-proxy URL of the transcript's current revision, if it has one. */
  transcriptUrl: string | null;
}

/**
 * Two panes: the player stays put on the left while the right pane scrolls.
 *
 * `VideoPlayer` wraps both of them. It renders no element of its own, so the
 * layout is unchanged, and it is what gives the transcript tab the same player
 * the pane beside it is showing: the tab follows and seeks through `usePlayer`
 * rather than through a ref lifted out of the player component (S2 plan §5).
 */
export function SourceWorkspace({
  source,
  artifacts,
  playlistUrl,
  peaksUrl,
  posterUrl,
  speakerLabels,
  transcript,
  transcriptAnnotationsUrl,
  transcriptRevisions,
  transcriptUrl,
  chapterAvailability,
  chapters,
}: SourceWorkspaceProps) {
  // The one row that is not a string: an absolute instant belongs to the
  // reader's time zone, which only the browser knows, so it renders itself in
  // two phases rather than making the server guess (`SourceTimestamp`).
  const readyAt: ReactNode = source.readyAt ? (
    <SourceTimestamp iso={source.readyAt} />
  ) : (
    "—"
  );
  const rows: [string, ReactNode][] = [
    ["Status", statusLabel(source)],
    ["Duration", formatDuration(source.durationMs)],
    [
      "Frame",
      source.width && source.height ? `${source.width}×${source.height}` : "—",
    ],
    ["Frame rate", source.fps ? `${Number(source.fps)} fps` : "—"],
    ["Video", source.videoCodec ?? "—"],
    [
      "Audio",
      source.audioCodec
        ? `${source.audioCodec} · ${source.audioChannels ?? "?"} ch`
        : "—",
    ],
    ["Master", `${source.originalFilename} · ${formatBytes(source.sizeBytes)}`],
    ["Ready", readyAt],
    ["Uploaded in", formatElapsed(source.createdAt, source.uploadedAt)],
    ["Ingested in", formatElapsed(source.uploadedAt, source.readyAt)],
  ];
  return (
    <VideoPlayer poster={posterUrl ?? undefined}>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <section className="lg:sticky lg:top-6 lg:self-start">
          <h1 className="mb-3 font-semibold text-xl tracking-tight">
            {source.title}
          </h1>
          {playlistUrl ? (
            <SourcePlayer peaksUrl={peaksUrl} playlistUrl={playlistUrl} />
          ) : (
            <div className="flex aspect-video items-center justify-center rounded-lg border text-muted-foreground text-sm">
              Playback is not ready yet.
            </div>
          )}
        </section>
        <section>
          <Tabs defaultValue="details">
            <TabsList>
              <TabsTrigger value="details">Details</TabsTrigger>
              <TabsTrigger value="transcript">Transcript</TabsTrigger>
              <TabsTrigger data-testid="chapters-tab" value="chapters">
                Chapters
              </TabsTrigger>
              <TabsTrigger value="artifacts">Artifacts</TabsTrigger>
            </TabsList>
            <TabsContent value="details">
              <dl
                className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm"
                data-testid="source-details"
              >
                {rows.map(([label, value]) => (
                  <div className="contents" key={label}>
                    <dt className="text-muted-foreground">{label}</dt>
                    <dd className="tabular-nums">{value}</dd>
                  </div>
                ))}
              </dl>
            </TabsContent>
            <TabsContent value="transcript">
              <TranscriptPanel
                annotationsUrl={transcriptAnnotationsUrl}
                labels={speakerLabels}
                revisions={transcriptRevisions}
                revisionUrl={transcriptUrl}
                row={transcript}
                sourceId={source.id}
                sourceStatus={source.status}
                title={source.title}
              />
            </TabsContent>
            <TabsContent value="chapters">
              <ChapterPanel
                availability={chapterAvailability}
                initialView={chapters}
                sourceId={source.id}
              />
            </TabsContent>
            <TabsContent value="artifacts">
              <Table data-testid="artifacts-table">
                <TableHeader>
                  <TableRow>
                    <TableHead>Kind</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead className="text-right">Size</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {artifacts.map((a) => (
                    <TableRow key={a.kind}>
                      <TableCell>
                        <Badge variant="outline">{a.kind}</Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {a.contentType}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatBytes(a.sizeBytes)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TabsContent>
          </Tabs>
        </section>
      </div>
    </VideoPlayer>
  );
}
