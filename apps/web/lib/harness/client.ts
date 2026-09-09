export function stableSessionUuid(key: string): string {
  const existing = sessionStorage.getItem(key);
  if (existing) {
    return existing;
  }
  const created = crypto.randomUUID();
  sessionStorage.setItem(key, created);
  return created;
}

export function clearSessionUuid(key: string): void {
  sessionStorage.removeItem(key);
}

export function readSessionIntent<T>(key: string): T | null {
  const value = sessionStorage.getItem(key);
  if (!value) {
    return null;
  }
  try {
    return JSON.parse(value) as T;
  } catch {
    sessionStorage.removeItem(key);
    return null;
  }
}

export function writeSessionIntent(key: string, value: unknown): void {
  sessionStorage.setItem(key, JSON.stringify(value));
}

export function clearSessionIntent(key: string): void {
  sessionStorage.removeItem(key);
}
