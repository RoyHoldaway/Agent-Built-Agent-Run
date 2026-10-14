import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import type { AstroCookies } from 'astro';

const COOKIE = 'admin_session';
const TTL_MS = 12 * 60 * 60 * 1000;
const prod = import.meta.env.PROD;

// Without SESSION_SECRET we use a per-process random one (sessions reset on restart; fine for dev).
const secret = process.env.SESSION_SECRET || randomBytes(32).toString('hex');

const sign = (payload: string) => createHmac('sha256', secret).update(payload).digest('hex');
const safeEq = (a: string, b: string) => {
  const A = Buffer.from(a), B = Buffer.from(b);
  return A.length === B.length && timingSafeEqual(A, B);
};

export function checkPassword(attempt: string): boolean {
  const expected = process.env.ADMIN_PASSWORD;
  if (!expected) {
    if (prod) return false; // never allow a default password in production
    return safeEq(attempt, 'dev-password');
  }
  // compare hashes so length differences don't leak
  return safeEq(sign('pw:' + attempt), sign('pw:' + expected));
}

export function startSession(cookies: AstroCookies) {
  const exp = String(Date.now() + TTL_MS);
  cookies.set(COOKIE, `${exp}.${sign(exp)}`, {
    httpOnly: true, sameSite: 'strict', secure: prod, path: '/', maxAge: TTL_MS / 1000,
  });
}

export const endSession = (cookies: AstroCookies) => cookies.delete(COOKIE, { path: '/' });

export function isAuthed(cookies: AstroCookies): boolean {
  const v = cookies.get(COOKIE)?.value;
  if (!v) return false;
  const [exp, sig] = v.split('.');
  return !!exp && !!sig && safeEq(sig, sign(exp)) && Number(exp) > Date.now();
}
