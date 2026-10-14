import type { APIRoute } from 'astro';
import { addDays, getSlots, isDate } from '../../lib/availability';
import { HttpError, guard, json } from '../../lib/http';

export const prerender = false;

/** GET /api/calendar?month=YYYY-MM&service=ID  ->  { days: { "YYYY-MM-DD": true|false } } */
export const GET: APIRoute = guard(({ url }) => {
  const month = url.searchParams.get('month') ?? '';
  const service = Number(url.searchParams.get('service'));
  if (!/^\d{4}-\d{2}$/.test(month) || !isDate(month + '-01') || !Number.isInteger(service)) {
    throw new HttpError(400, 'month and service are required');
  }
  const days: Record<string, boolean> = {};
  for (let d = month + '-01'; d.startsWith(month); d = addDays(d, 1)) {
    days[d] = getSlots(d, service).length > 0;
  }
  return json({ days });
});
