import type { APIRoute } from 'astro';
import { getSlots, isDate } from '../../lib/availability';
import { HttpError, guard, json } from '../../lib/http';

export const prerender = false;

/** GET /api/availability?date=YYYY-MM-DD&service=ID  ->  { slots: ["09:00", ...] } */
export const GET: APIRoute = guard(({ url }) => {
  const date = url.searchParams.get('date');
  const service = Number(url.searchParams.get('service'));
  if (!isDate(date) || !Number.isInteger(service)) throw new HttpError(400, 'date and service are required');
  return json({ slots: getSlots(date, service) });
});
