import type { APIRoute } from 'astro';
import { isDate, nowIn } from '../../../lib/availability';
import { db, getSettings } from '../../../lib/db';
import { HttpError, guard, json, readJson } from '../../../lib/http';
import { onBookingCancelled } from '../../../lib/notify';

export const prerender = false;

const SELECT = `SELECT b.id, b.date, b.start_time AS start, b.end_time AS end, b.name, b.email, b.phone,
                       b.notes, b.status, s.name AS service
                FROM bookings b JOIN services s ON s.id = b.service_id`;

/** GET ?from=YYYY-MM-DD (default today) -> upcoming bookings, oldest first */
export const GET: APIRoute = guard(({ url }) => {
  const from = url.searchParams.get('from');
  const start = isDate(from) ? from : nowIn(getSettings().timezone).date;
  const bookings = db.prepare(`${SELECT} WHERE b.date >= ? ORDER BY b.date, b.start_time LIMIT 500`).all(start);
  return json({ bookings });
});

/** PATCH { id, status: "cancelled" | "confirmed" } */
export const PATCH: APIRoute = guard(async ({ request }) => {
  const b = await readJson(request);
  const id = Number(b.id);
  if (!Number.isInteger(id) || (b.status !== 'cancelled' && b.status !== 'confirmed')) throw new HttpError(400, 'id and status required');
  const r = db.prepare('UPDATE bookings SET status = ? WHERE id = ?').run(b.status, id);
  if (!r.changes) throw new HttpError(404, 'Booking not found');
  if (b.status === 'cancelled') {
    const row = db.prepare(`${SELECT} WHERE b.id = ?`).get(id) as any;
    void onBookingCancelled({ ...row, start: row.start, end: row.end });
  }
  return json({ ok: true });
});
