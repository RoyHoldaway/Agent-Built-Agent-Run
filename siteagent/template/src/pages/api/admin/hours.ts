import type { APIRoute } from 'astro';
import { TIME_RE, toMin } from '../../../lib/availability';
import { db, tx } from '../../../lib/db';
import { HttpError, guard, json, readJson } from '../../../lib/http';

export const prerender = false;

export const GET: APIRoute = guard(() => {
  const hours = db
    .prepare('SELECT weekday, closed, open_time AS open, close_time AS close FROM weekly_hours ORDER BY weekday')
    .all();
  return json({ hours });
});

/** PUT { hours: [{ weekday: 0-6, closed: bool, open: "HH:MM", close: "HH:MM" } x7] } */
export const PUT: APIRoute = guard(async ({ request }) => {
  const { hours } = await readJson(request);
  if (!Array.isArray(hours) || hours.length !== 7) throw new HttpError(400, 'Send all 7 days');
  const rows = hours.map((h: any) => {
    const weekday = Number(h?.weekday);
    const closed = h?.closed ? 1 : 0;
    if (!Number.isInteger(weekday) || weekday < 0 || weekday > 6) throw new HttpError(400, 'Bad weekday');
    if (!TIME_RE.test(h?.open) || !TIME_RE.test(h?.close)) throw new HttpError(400, 'Times must be HH:MM');
    if (!closed && toMin(h.close) <= toMin(h.open)) throw new HttpError(400, 'Closing time must be after opening time');
    return [weekday, closed, h.open, h.close] as const;
  });
  tx(() => {
    const up = db.prepare('INSERT OR REPLACE INTO weekly_hours (weekday, closed, open_time, close_time) VALUES (?, ?, ?, ?)');
    for (const r of rows) up.run(...r);
  });
  return json({ ok: true });
});
