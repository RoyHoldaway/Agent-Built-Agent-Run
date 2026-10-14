import type { APIRoute } from 'astro';
import { db, getSettings, tx } from '../../../lib/db';
import { HttpError, guard, json, readJson } from '../../../lib/http';

export const prerender = false;

export const GET: APIRoute = guard(() => json({ settings: getSettings() }));

const RULES: Record<string, { key: string; min: number; max: number }> = {
  slotStepMin: { key: 'slot_step_min', min: 5, max: 240 },
  capacity: { key: 'capacity', min: 1, max: 100 },
  minNoticeHours: { key: 'min_notice_hours', min: 0, max: 720 },
  maxDaysAhead: { key: 'max_days_ahead', min: 1, max: 730 },
};

/** PUT { slotStepMin?, capacity?, minNoticeHours?, maxDaysAhead? } */
export const PUT: APIRoute = guard(async ({ request }) => {
  const b = await readJson(request);
  const updates: [string, string][] = [];
  for (const [field, rule] of Object.entries(RULES)) {
    if (b[field] === undefined) continue;
    const n = Number(b[field]);
    if (!Number.isInteger(n) || n < rule.min || n > rule.max) throw new HttpError(400, `${field} must be ${rule.min}-${rule.max}`);
    updates.push([rule.key, String(n)]);
  }
  tx(() => {
    const up = db.prepare('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)');
    for (const [k, v] of updates) up.run(k, v);
  });
  return json({ ok: true });
});
