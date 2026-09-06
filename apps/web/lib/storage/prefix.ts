import { DeleteObjectsCommand, ListObjectsV2Command } from "@aws-sdk/client-s3";
import { storage, storageSettings } from "@/lib/storage/client";

/** Delete every object under a prefix; returns how many were removed. */
export async function deletePrefix(prefix: string): Promise<number> {
  const { bucket } = storageSettings();
  let token: string | undefined;
  let removed = 0;
  do {
    // biome-ignore lint/performance/noAwaitInLoops: pages are sequential by protocol
    const page = await storage().send(
      new ListObjectsV2Command({
        Bucket: bucket,
        ContinuationToken: token,
        Prefix: prefix,
      })
    );
    const keys = (page.Contents ?? []).flatMap((o) =>
      o.Key ? [{ Key: o.Key }] : []
    );
    if (keys.length > 0) {
      await storage().send(
        new DeleteObjectsCommand({
          Bucket: bucket,
          Delete: { Objects: keys, Quiet: true },
        })
      );
      removed += keys.length;
    }
    token = page.IsTruncated ? page.NextContinuationToken : undefined;
  } while (token);
  return removed;
}
