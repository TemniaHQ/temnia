"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { SourceRowActions } from "@/components/sources/source-row-actions";
import { Badge } from "@/components/ui/badge";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";
import { Progress } from "@/components/ui/progress";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  formatBytes,
  formatDuration,
  progressLabel,
} from "@/lib/sources/labels";
import type { SourceRow } from "@/lib/sources/queries";

const REFRESH_MS = 3500;

type Row = SourceRow & { posterUrl: string | null };

const STATUS_VARIANT: Record<
  SourceRow["status"],
  "default" | "secondary" | "destructive" | "outline"
> = {
  failed: "destructive",
  processing: "secondary",
  ready: "default",
  uploaded: "outline",
  uploading: "outline",
};

/** Lists a project's sources and refreshes while any is still moving. */
export function SourcesTable({ rows }: { rows: Row[] }) {
  const router = useRouter();
  const active = rows.some(
    (r) => r.status === "uploaded" || r.status === "processing"
  );
  useEffect(() => {
    if (!active) {
      return;
    }
    const timer = setInterval(() => {
      if (navigator.onLine) {
        router.refresh();
      }
    }, REFRESH_MS);
    return () => clearInterval(timer);
  }, [active, router]);

  if (rows.length === 0) {
    return (
      <Empty>
        <EmptyHeader>
          <EmptyTitle>No sources yet</EmptyTitle>
          <EmptyDescription>
            Drop a master above to start the ingest.
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }
  return (
    <Table data-testid="sources-table">
      <TableHeader>
        <TableRow>
          <TableHead>Source</TableHead>
          <TableHead>Status</TableHead>
          <TableHead className="text-right">Duration</TableHead>
          <TableHead className="text-right">Size</TableHead>
          <TableHead className="w-12" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow
            data-source-id={row.id}
            data-status={row.status}
            key={row.id}
          >
            <TableCell>
              {row.status === "ready" ? (
                <Link
                  className="font-medium underline-offset-4 hover:underline"
                  href={`/sources/${row.id}`}
                >
                  {row.title}
                </Link>
              ) : (
                <span className="font-medium">{row.title}</span>
              )}
              <div className="text-muted-foreground text-xs">
                {row.originalFilename}
              </div>
            </TableCell>
            <TableCell>
              <div className="flex flex-col gap-1.5">
                <Badge variant={STATUS_VARIANT[row.status]}>
                  {progressLabel(row)}
                </Badge>
                {row.status === "processing" && row.ingestPercent !== null ? (
                  <Progress className="w-40" value={row.ingestPercent} />
                ) : null}
                {row.status === "failed" && row.errorMessage ? (
                  <span className="text-destructive text-xs">
                    {row.errorMessage}
                  </span>
                ) : null}
              </div>
            </TableCell>
            <TableCell className="text-right tabular-nums">
              {formatDuration(row.durationMs)}
            </TableCell>
            <TableCell className="text-right tabular-nums">
              {formatBytes(row.sizeBytes)}
            </TableCell>
            <TableCell className="text-right">
              <SourceRowActions
                sourceId={row.id}
                status={row.status}
                title={row.title}
              />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
