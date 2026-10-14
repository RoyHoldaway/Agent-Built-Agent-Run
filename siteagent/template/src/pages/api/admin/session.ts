import type { APIRoute } from 'astro';
import { endSession } from '../../../lib/auth';
import { guard, json } from '../../../lib/http';

export const prerender = false;

// Middleware already rejected unauthenticated calls, so reaching GET means "logged in".
export const GET: APIRoute = guard(() => json({ ok: true }));
export const DELETE: APIRoute = guard(({ cookies }) => {
  endSession(cookies);
  return json({ ok: true });
});
