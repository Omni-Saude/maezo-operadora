// Q1 serializes aware datetimes with up to six fractional digits. Keep those
// digits for ordering/expiry; Date.parse alone discards submillisecond facts.
export function timestampMicroseconds(value: string): bigint | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|([+-])(\d{2}):(\d{2}))$/.exec(value);
  if (match === null || match[0] !== value) return null;
  const [, y, mo, d, h, mi, s, fraction = "", zone, sign, oh = "0", om = "0"] = match;
  const [year, month, day, hour, minute, second, offsetHour, offsetMinute] =
    [y, mo, d, h, mi, s, oh, om].map(Number);
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > 31 ||
      hour > 23 || minute > 59 || second > 59 || offsetHour > 23 || offsetMinute > 59) return null;
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  date.setUTCHours(hour, minute, second, 0);
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return null;
  const offset = zone === "Z" ? 0 : (sign === "-" ? -1 : 1) * (offsetHour * 60 + offsetMinute);
  const utcYear = new Date(date.getTime() - offset * 60_000).getUTCFullYear();
  if (utcYear < 1 || utcYear > 9999) return null;
  return BigInt(date.getTime()) * 1000n + BigInt(fraction.padEnd(6, "0")) - BigInt(offset) * 60_000_000n;
}

export function isCurrent(validUntil: string): boolean {
  return timestampMicroseconds(validUntil)! > BigInt(Date.now()) * 1000n;
}

export function retainedCeiling(left: string, right: string): string {
  return timestampMicroseconds(left)! < timestampMicroseconds(right)! ? left : right;
}

export function expiryDelay(validUntil: string): number {
  // Browser timers have millisecond resolution: round down, never extend the
  // retained grant into the following millisecond. Late I/O rechecks it too.
  const remaining = timestampMicroseconds(validUntil)! - BigInt(Date.now()) * 1000n;
  return Math.max(0, Math.min(2_147_483_647, Number(remaining / 1000n)));
}
