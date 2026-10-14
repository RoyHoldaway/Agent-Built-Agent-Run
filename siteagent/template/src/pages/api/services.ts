import type { APIRoute } from 'astro';
import { db, getSettings } from '../../lib/db';
import { guard, json } from '../../lib/http';

export const prerender = false;

export const GET: APIRoute = guard(() => {
  const services = db
    .prepare('SELECT id, name, duration_min AS durationMin, price FROM services WHERE active = 1 ORDER BY id')
    .all();
  const s = getSettings();
  return json({ services, maxDaysAhead: s.maxDaysAhead, timezone: s.timezone });
});
