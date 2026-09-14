/**
 * A retry that the worker refuses fails within seconds, before the run is claimed, so
 * nothing writes the refusal to the run row. The action waits this long for that early
 * outcome and reports it; a run that is still going after the window is a started retry.
 */
export const EARLY_FAILURE_WINDOW_MS = 8000;

interface ResultHandle {
  result?: () => Promise<unknown>;
}

/** The innermost message of a Temporal failure chain: the activity's own sentence. */
export function deepestMessage(error: unknown): string {
  let current: unknown = error;
  let message = "The retry failed before it started.";
  while (current && typeof current === "object") {
    const candidate = (current as { message?: unknown }).message;
    if (typeof candidate === "string" && candidate.trim()) {
      message = candidate;
    }
    current = (current as { cause?: unknown }).cause;
  }
  return message;
}

/** The failure message if the execution fails inside the window; null if it is still running. */
export async function earlyFailure(
  handle: ResultHandle,
  windowMs: number
): Promise<string | null> {
  if (typeof handle.result !== "function") {
    return null;
  }
  let timer: ReturnType<typeof setTimeout> | undefined;
  const window = new Promise<null>((resolve) => {
    timer = setTimeout(() => resolve(null), windowMs);
  });
  const outcome = handle.result().then(
    () => null,
    (error: unknown) => deepestMessage(error)
  );
  try {
    return await Promise.race([outcome, window]);
  } finally {
    if (timer) {
      clearTimeout(timer);
    }
  }
}
