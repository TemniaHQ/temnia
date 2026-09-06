"use client";

import dynamic from "next/dynamic";
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
import { formatBytes, formatDuration, statusLabel } from "@/lib/sources/labels";

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
  durationMs: number | null;
  fps: string | null;
  height: number | null;
  id: string;
  originalFilename: string;
  readyAt: string | null;
  sizeBytes: number;
  status: "uploading" | "uploaded" | "processing" | "ready" | "failed";
  title: string;
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
  peaksUrl: string | null;
  playlistUrl: string | null;
  posterUrl: string | null;
  source: SourceSummary;
}

/** Two panes: the player stays put on the left while the right pane scrolls. */
export function SourceWorkspace({
  source,
  artifacts,
  playlistUrl,
  peaksUrl,
  posterUrl,
}: SourceWorkspaceProps) {
  const rows: [string, string][] = [
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
    ["Ready", source.readyAt ? new Date(source.readyAt).toLocaleString() : "—"],
  ];
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <section className="lg:sticky lg:top-6 lg:self-start">
        <h1 className="mb-3 font-semibold text-xl tracking-tight">
          {source.title}
        </h1>
        {playlistUrl ? (
          <SourcePlayer
            peaksUrl={peaksUrl}
            playlistUrl={playlistUrl}
            posterUrl={posterUrl}
          />
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
  );
}
