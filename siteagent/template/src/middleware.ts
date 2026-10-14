import { defineMiddleware } from 'astro:middleware';
import { isAuthed } from './lib/auth';
import { json } from './lib/http';

// One gate for every admin API route (login is the only exception).
export const onRequest = defineMiddleware(async (ctx, next) => {
  const p = ctx.url.pathname;
  if (p.startsWith('/api/admin/') && p !== '/api/admin/login' && !isAuthed(ctx.cookies)) {
    return json({ error: 'Unauthorized' }, 401);
  }
  const res = await next();
  try {
    res.headers.set('X-Content-Type-Options', 'nosniff');
    res.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');
    if (p.startsWith('/admin')) res.headers.set('X-Robots-Tag', 'noindex');
  } catch { /* immutable headers on some responses; safe to skip */ }
  return res;
});
