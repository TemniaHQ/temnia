import {
  ARTIFACT_PATHS,
  sourcePrefix,
  transcriptRevisionKey,
} from "@temnia/contracts";
import Link from "next/link";
import { notFound } from "next/navigation";
import { SourceWorkspace } from "@/components/sources/source-workspace";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { resolveScope } from "@/lib/scope/resolve-scope";
import { getSourceWithArtifacts } from "@/lib/sources/queries";
import { getTranscript } from "@/lib/transcript/queries";

export const dynamic = "force-dynamic";

export default async function SourcePage({
  params,
}: PageProps<"/sources/[sourceId]">) {
  const { sourceId } = await params;
  const [found, transcript] = await Promise.all([
    getSourceWithArtifacts(sourceId),
    getTranscript(sourceId),
  ]);
  if (!found) {
    notFound();
  }
  const { source, artifacts, project } = found;
  const prefix = `/api/media/${sourcePrefix(resolveScope().organizationId, source.id)}`;
  const has = (kind: (typeof artifacts)[number]["kind"]) =>
    artifacts.some((a) => a.kind === kind);
  const row = transcript?.row;
  // The words are fetched by the browser from the media proxy, not serialised
  // into the page: a 2.5-hour episode is about two megabytes and it would ride
  // in the RSC payload on every poll.
  const transcriptUrl = row?.currentRevision
    ? `${prefix}${transcriptRevisionKey("", row.currentRevision)}`
    : null;
  return (
    <main className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-6 px-6 py-8">
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink render={<Link href="/projects" />}>
              Projects
            </BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbLink render={<Link href={`/projects/${project.id}`} />}>
              {project.name}
            </BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbPage>{source.title}</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>
      <SourceWorkspace
        artifacts={artifacts.map((a) => ({
          contentType: a.contentType,
          kind: a.kind,
          metadata: a.metadata,
          sizeBytes: a.sizeBytes,
          storageKey: a.storageKey,
        }))}
        peaksUrl={has("peaks") ? `${prefix}${ARTIFACT_PATHS.peaks}` : null}
        playlistUrl={has("hls") ? `${prefix}${ARTIFACT_PATHS.hlsMaster}` : null}
        posterUrl={
          has("thumbnails") ? `${prefix}${ARTIFACT_PATHS.poster}` : null
        }
        source={{
          audioChannels: source.audioChannels,
          audioCodec: source.audioCodec,
          createdAt: source.createdAt.toISOString(),
          durationMs: source.durationMs,
          fps: source.fps,
          height: source.height,
          id: source.id,
          originalFilename: source.originalFilename,
          readyAt: source.readyAt?.toISOString() ?? null,
          sizeBytes: source.sizeBytes,
          status: source.status,
          title: source.title,
          uploadedAt: source.uploadedAt?.toISOString() ?? null,
          videoCodec: source.videoCodec,
          width: source.width,
        }}
        speakerLabels={row?.speakerLabels ?? {}}
        transcript={
          row
            ? {
                currentRevision: row.currentRevision,
                errorMessage: row.errorMessage,
                heartbeatAt: row.heartbeatAt?.toISOString() ?? null,
                percent: row.percent,
                stage: row.stage,
                status: row.status,
                wordCount: transcript?.current?.wordCount ?? null,
              }
            : null
        }
        transcriptUrl={transcriptUrl}
      />
    </main>
  );
}
