import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import config from '../../site.config.json';

const DAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'] as const;

const file = process.env.DB_PATH ?? './data/site.db';
mkdirSync(dirname(file), { recursive: true });

export const db = new DatabaseSync(file);
db.exec(`
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS services (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  duration_min INTEGER NOT NULL,
  price REAL NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS weekly_hours (
  weekday INTEGER PRIMARY KEY,           -- 0 = Sunday
  closed INTEGER NOT NULL,
  open_time TEXT NOT NULL,
  close_time TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS days_off (date TEXT PRIMARY KEY, reason TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS bookings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  service_id INTEGER NOT NULL REFERENCES services(id),
  date TEXT NOT NULL,
  start_time TEXT NOT NULL,
  end_time TEXT NOT NULL,
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  phone TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'confirmed',  -- confirmed | cancelled
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_bookings_date ON bookings(date, status);
`);

/** Run fn inside an immediate transaction (prevents double-booking races). */
export function tx<T>(fn: () => T): T {
  db.exec('BEGIN IMMEDIATE');
  try {
    const out = fn();
    db.exec('COMMIT');
    return out;
  } catch (e) {
    db.exec('ROLLBACK');
    throw e;
  }
}

// ---- first-run seed from site.config.json (admin owns the data after this) ----
if (!db.prepare("SELECT 1 FROM settings WHERE key = 'seeded'").get()) {
  tx(() => {
    const setSetting = db.prepare('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)');
    setSetting.run('timezone', config.business.timezone);
    setSetting.run('slot_step_min', String(config.booking.slotStepMin));
    setSetting.run('capacity', String(config.booking.capacity));
    setSetting.run('min_notice_hours', String(config.booking.minNoticeHours));
    setSetting.run('max_days_ahead', String(config.booking.maxDaysAhead));

    const addService = db.prepare('INSERT INTO services (name, duration_min, price) VALUES (?, ?, ?)');
    for (const s of config.services) addService.run(s.name, s.durationMin, s.price ?? 0);

    const addHours = db.prepare('INSERT INTO weekly_hours (weekday, closed, open_time, close_time) VALUES (?, ?, ?, ?)');
    DAYS.forEach((d, i) => {
      const h = (config.hours as Record<string, string[] | null>)[d];
      addHours.run(i, h ? 0 : 1, h?.[0] ?? '09:00', h?.[1] ?? '17:00');
    });
    setSetting.run('seeded', '1');
  });
}

export interface Settings {
  timezone: string;
  slotStepMin: number;
  capacity: number;
  minNoticeHours: number;
  maxDaysAhead: number;
}

export function getSettings(): Settings {
  const rows = db.prepare('SELECT key, value FROM settings').all() as { key: string; value: string }[];
  const m = Object.fromEntries(rows.map((r) => [r.key, r.value]));
  return {
    timezone: m.timezone ?? 'America/Denver',
    slotStepMin: Number(m.slot_step_min ?? 30),
    capacity: Number(m.capacity ?? 1),
    minNoticeHours: Number(m.min_notice_hours ?? 0),
    maxDaysAhead: Number(m.max_days_ahead ?? 60),
  };
}
