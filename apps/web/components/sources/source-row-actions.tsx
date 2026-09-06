"use client";

import { MoreHorizontalIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { deleteSource, retryIngest } from "@/app/actions/sources";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface SourceRowActionsProps {
  sourceId: string;
  status: "uploading" | "uploaded" | "processing" | "ready" | "failed";
  title: string;
}

/** The per-row menu: retry the ingest, or delete the source and its files. */
export function SourceRowActions({
  sourceId,
  title,
  status,
}: SourceRowActionsProps) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const canRetry =
    status === "failed" || status === "uploaded" || status === "ready";

  const retry = () => {
    startTransition(async () => {
      const result = await retryIngest(sourceId);
      setMessage(result.ok ? null : result.message);
      router.refresh();
    });
  };

  const remove = () => {
    startTransition(async () => {
      const result = await deleteSource(sourceId);
      setMessage(result.ok ? null : result.message);
      setConfirming(false);
      router.refresh();
    });
  };

  const openConfirm = () => setConfirming(true);

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              aria-label={`Actions for ${title}`}
              data-testid="source-actions"
              size="icon"
              variant="ghost"
            />
          }
        >
          <HugeiconsIcon icon={MoreHorizontalIcon} />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem disabled={!canRetry || pending} onClick={retry}>
            {status === "ready" ? "Re-run ingest" : "Retry ingest"}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            disabled={pending}
            onClick={openConfirm}
            variant="destructive"
          >
            Delete source
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <AlertDialog onOpenChange={setConfirming} open={confirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {title}?</AlertDialogTitle>
            <AlertDialogDescription>
              The master and everything made from it are removed from storage.
              This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction
              data-testid="confirm-delete"
              disabled={pending}
              onClick={remove}
              variant="destructive"
            >
              {pending ? "Deleting…" : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {message ? (
        <span className="text-destructive text-xs" role="alert">
          {message}
        </span>
      ) : null}
    </>
  );
}
