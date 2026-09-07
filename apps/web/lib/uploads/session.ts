import type { UploadedPart } from "./parts";

/** What POST /api/uploads returns: the row, the store's multipart identity, and the parts already held. */
export interface UploadSession {
  /** The store's object key; the browser signs parts against it. */
  key: string;
  /** The store's multipart UploadId, injected into Uppy's file state so it resumes rather than creates. */
  multipartUploadId: string;
  partCount: number;
  partSize: number;
  resumed: boolean;
  sourceId: string;
  /** Parts the store already holds; the browser skips these. */
  uploaded: UploadedPart[];
  /** The upload row id: every control call is addressed to it. */
  uploadId: string;
}
