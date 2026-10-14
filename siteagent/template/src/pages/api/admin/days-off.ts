import type { APIRoute } from 'astro';
import { addDays, isDate, nowIn } from '../../../lib/availability';
import { db, getSettings, tx } from '../../../lib/db';
import { HttpError, guard, json, readJson, str } from '../../../lib/http';

export const prerender = false;

export const GET: APIRoute = guard(() => {
  const today = nowIn(getSettings().timezone).date;
  // keep a week of history so admins can see what they just did
  const rows = db
    .prepare('SELECT date, reason FROM days_off WHERE date >= ? ORDER BY date')
    .all(addDays(today, -7));
  return json({ daysOff: rows });
});

/** POST { from, to?, reason? } closes every date in the range (max 366 days). */
export const POST: APIRoute = guard(async ({ request }) => {
  const b = await readJson(request);
  const from = b.from, to = b.to ?? b.from;
  if (!isDate(from) || !isDate(to) || to < from) throw new HttpError(400, 'Pick a valid date range');
  const reason = str(b.reason, 200);

  const dates: string[] = [];
  for (let d = from; d <= to; d = addDays(d, 1)) {
    dates.push(d);
    if (dates.length > 366) throw new HttpError(400, 'Range too long (max 1 year)');
  }
  tx(() => {
    const ins = db.prepare('INSERT OR REPLACE INTO days_off (date, reason) VALUES (?, ?)');
    for (const d of dates) ins.run(d, reason);
  });
  // Existing bookings are NOT auto-cancelled. Tell the admin so they can contact people.
  const q = db.prepare("SELECT COUNT(*) AS n FROM bookings WHERE status = 'confirmed' AND date BETWEEN ? AND ?");
  const affected = (q.get(from, to) as { n: number }).n;
  return json({ ok: true, closed: dates.length, affectedBookings: affected });
});

/** DELETE ?date=YYYY-MM-DD reopens a day. */
export const DELETE: APIRoute = guard(({ url }) => {
  const date = url.searchParams.get('date');
  if (!isDate(date)) throw new HttpError(400, 'date required');
  db.prepare('DELETE FROM days_off WHERE date = ?').run(date);
  return json({ ok: true });
});
