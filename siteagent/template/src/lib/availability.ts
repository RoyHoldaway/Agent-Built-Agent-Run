import { db, getSettings } from './db';

export const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
export const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;

export const isDate = (s: unknown): s is string =>
  typeof s === 'string' && DATE_RE.test(s) && !Number.isNaN(Date.parse(s + 'T00:00:00Z')) &&
  new Date(s + 'T00:00:00Z').toISOString().slice(0, 10) === s;

export const toMin = (t: string) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5));
export const fromMin = (n: number) =>
  `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}`;

export const weekdayOf = (date: string) => new Date(date + 'T00:00:00Z').getUTCDay();
export const addDays = (date: string, n: number) =>
  new Date(Date.parse(date + 'T00:00:00Z') + n * 86400000).toISOString().slice(0, 10);

/** "Now" as the business sees it (date + minutes since midnight), not the server's zone. */
export function nowIn(tz: string, at = new Date()) {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).formatToParts(at).map((x) => [x.type, x.value]),
  );
  return { date: `${p.year}-${p.month}-${p.day}`, minutes: Number(p.hour) * 60 + Number(p.minute) };
}

export interface Service { id: number; name: string; duration_min: number; price: number; active: number }

export function getService(id: number): Service | undefined {
  return db.prepare('SELECT * FROM services WHERE id = ?').get(id) as Service | undefined;
}

/** All bookable start times ("HH:MM") for a date + service. Pure read, no side effects. */
export function getSlots(date: string, serviceId: number): string[] {
  const svc = getService(serviceId);
  if (!svc || !svc.active) return [];
  const s = getSettings();

  const now = nowIn(s.timezone);
  if (date < now.date || date > addDays(now.date, s.maxDaysAhead)) return [];
  if (db.prepare('SELECT 1 FROM days_off WHERE date = ?').get(date)) return [];

  const wh = db.prepare('SELECT * FROM weekly_hours WHERE weekday = ?').get(weekdayOf(date)) as
    | { closed: number; open_time: string; close_time: string } | undefined;
  if (!wh || wh.closed) return [];

  const booked = db
    .prepare("SELECT start_time, end_time FROM bookings WHERE date = ? AND status = 'confirmed'")
    .all(date) as { start_time: string; end_time: string }[];
  const ranges = booked.map((b) => [toMin(b.start_time), toMin(b.end_time)] as const);

  const open = toMin(wh.open_time);
  const close = toMin(wh.close_time);
  const earliestToday = now.minutes + s.minNoticeHours * 60;
  const out: string[] = [];

  for (let t = open; t + svc.duration_min <= close; t += s.slotStepMin) {
    if (date === now.date && t < earliestToday) continue;
    const end = t + svc.duration_min;
    const overlapping = ranges.filter(([a, b]) => a < end && b > t).length;
    if (overlapping < s.capacity) out.push(fromMin(t));
  }
  return out;
}
