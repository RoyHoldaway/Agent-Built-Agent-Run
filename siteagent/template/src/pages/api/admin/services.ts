import type { APIRoute } from 'astro';
import { db } from '../../../lib/db';
import { HttpError, guard, json, readJson, str } from '../../../lib/http';

export const prerender = false;

export const GET: APIRoute = guard(() =>
  json({
    services: db
      .prepare('SELECT id, name, duration_min AS durationMin, price, active FROM services ORDER BY id')
      .all(),
  }),
);

/** POST { id?, name, durationMin, price, active } -> create (no id) or update. Services are never deleted, only deactivated. */
export const POST: APIRoute = guard(async ({ request }) => {
  const b = await readJson(request);
  const name = str(b.name, 100, true, 'Name');
  const durationMin = Number(b.durationMin);
  const price = Number(b.price ?? 0);
  if (!Number.isInteger(durationMin) || durationMin < 5 || durationMin > 600) throw new HttpError(400, 'Duration must be 5-600 minutes');
  if (!Number.isFinite(price) || price < 0) throw new HttpError(400, 'Invalid price');
  const active = b.active === false || b.active === 0 ? 0 : 1;

  if (b.id == null) {
    const r = db.prepare('INSERT INTO services (name, duration_min, price, active) VALUES (?, ?, ?, ?)').run(name, durationMin, price, active);
    return json({ ok: true, id: Number(r.lastInsertRowid) }, 201);
  }
  const id = Number(b.id);
  const r = db.prepare('UPDATE services SET name = ?, duration_min = ?, price = ?, active = ? WHERE id = ?').run(name, durationMin, price, active, id);
  if (!r.changes) throw new HttpError(404, 'Service not found');
  return json({ ok: true, id });
});
