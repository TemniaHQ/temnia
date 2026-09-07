"use client";

import { useEffect, useState } from "react";
import { formatUtcTimestamp } from "@/lib/sources/labels";

interface SourceTimestampProps {
  /** ISO 8601, as the instant crosses the server-to-client boundary. */
  iso: string;
}

/**
 * An absolute instant, in the reader's own zone once there is a reader.
 *
 * Only the browser knows the viewer's time zone and locale, so `toLocaleString`
 * in a render body produces one string on the server and a different one in the
 * browser, and on a production build that is React #418 and a regenerated tree
 * (invisible under `next dev`; the S1 lesson). The server and the first client
 * render therefore agree on the one form that depends on nothing — the instant
 * in UTC — and an effect replaces it with the local one after mount. The
 * `dateTime` attribute carries the machine-readable instant in either phase.
 */
export function SourceTimestamp({ iso }: SourceTimestampProps) {
  const [local, setLocal] = useState<string | null>(null);

  useEffect(() => {
    const at = Date.parse(iso);
    setLocal(Number.isNaN(at) ? null : new Date(at).toLocaleString());
  }, [iso]);

  return <time dateTime={iso}>{local ?? formatUtcTimestamp(iso)}</time>;
}
