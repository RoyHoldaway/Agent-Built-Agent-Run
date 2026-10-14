import type { APIRoute } from 'astro';
import { checkPassword, startSession } from '../../../lib/auth';
import { HttpError, guard, json, rateLimit, readJson } from '../../../lib/http';

export const prerender = false;

export const POST: APIRoute = guard(async ({ request, cookies, clientAddress }) => {
  rateLimit('login:' + clientAddress, 8, 15 * 60 * 1000);
  const { password } = await readJson(request);
  if (typeof password !== 'string' || !checkPassword(password)) throw new HttpError(401, 'Wrong password');
  startSession(cookies);
  return json({ ok: true });
});
