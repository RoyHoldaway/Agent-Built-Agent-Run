import type { APIRoute } from 'astro';
import { TIME_RE, fromMin, getService, getSlots, isDate, toMin } from '../../lib/availability';
import { db, tx } from '../../lib/db';
import { HttpError, guard, json, rateLimit, readJson, str } from '../../lib/http';
import { onBookingCreated } from '../../lib/notify';

export const prerender = false;

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export const POST: APIRoute = guard(async ({ request, clientAddress }) => {
  rateLimit('book:' + clientAddress, 10, 10 * 60 * 1000);
  const b = await readJson(request);

  if (b.website) return json({ ok: true }); // honeypot: bots fill hidden fields, humans don't

  const date = b.date, time = b.time;
  const serviceId = Number(b.serviceId);
  if (!isDate(date) || typeof time !== 'string' || !TIME_RE.test(time) || !Number.isInteger(serviceId)) {
    throw new HttpError(400, 'Invalid date, time or service');
  }
  const name = str(b.name, 100, true, 'Name');
  const email = str(b.email, 200, true, 'Email');
  if (!EMAIL_RE.test(email)) throw new HttpError(400, 'Enter a valid email');
  const phone = str(b.phone, 40);
  const notes = str(b.notes, 1000);

  const svc = getService(serviceId);
  if (!svc) throw new HttpError(400, 'Unknown service');
  const end = fromMin(toMin(time) + svc.duration_min);

  // Re-check availability inside the write lock so two people can't grab the same slot.
  const id = tx(() => {
    if (!getSlots(date, serviceId).includes(time)) throw new HttpError(409, 'That time was just taken. Pick another.');
    const r = db
      .prepare(`INSERT INTO bookings (service_id, date, start_time, end_time, name, email, phone, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)`)
      .run(serviceId, date, time, end, name, email, phone, notes);
    return Number(r.lastInsertRowid);
  });

  void onBookingCreated({ id, service: svc.name, date, start: time, end, name, email, phone, notes });
  return json({ ok: true, id, date, start: time, end, service: svc.name }, 201);
});
