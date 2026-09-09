const DOLLARS = /^(?:0|[1-9]\d*)(?:\.(\d{1,6}))?$/;

/** Parse user-entered dollars into integer millionths without binary floats. */
export function parseDollarMicros(
  value: string,
  maximum: number
): number | null {
  const match = DOLLARS.exec(value.trim());
  if (!match) {
    return null;
  }
  const [whole = "", fraction = ""] = value.trim().split(".");
  const micros =
    BigInt(whole) * BigInt(1_000_000) + BigInt(fraction.padEnd(6, "0"));
  if (
    micros <= 0 ||
    micros > BigInt(maximum) ||
    micros > BigInt(Number.MAX_SAFE_INTEGER)
  ) {
    return null;
  }
  return Number(micros);
}

export function formatMicros(value: number): string {
  return new Intl.NumberFormat(undefined, {
    currency: "USD",
    maximumFractionDigits: 4,
    minimumFractionDigits: 2,
    style: "currency",
  }).format(value / 1_000_000);
}
