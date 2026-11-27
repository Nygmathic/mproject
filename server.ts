import express from 'express';
import type { Request, Response, NextFunction } from 'express';
import { execFileSync, execFile } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';

const ROOT_DIR = process.cwd();
const DIST_DIR = path.join(ROOT_DIR, 'dist');
const DIST_PREFIX = DIST_DIR + path.sep;
const STORAGE_DIR = path.join(ROOT_DIR, 'storage');
const SUBSCRIBERS_FILE = path.join(STORAGE_DIR, 'subscribers.json');
const MESSAGES_FILE = path.join(STORAGE_DIR, 'messages.json');
const HUGO_BIN = path.join(ROOT_DIR, 'node_modules', '.bin', 'hugo');
const PORT = Number(process.env.PORT || 3000);
const IS_PROD = process.env.NODE_ENV === 'production';

interface SubscriberRecord {
  email: string;
  subscribedAt: string;
  token: string;
}

interface ContactMessageRecord {
  id: string;
  topic: string;
  name: string;
  email: string;
  articleUrl: string;
  message: string;
  receivedAt: string;
}

interface CachedAsset {
  raw: Buffer;
  gz: Buffer;
  br: Buffer;
  etag: string;
  contentType: string;
  cacheControl: string;
}

const MIME_TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.xml': 'application/xml; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.css': 'text/css; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
};

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const ALLOWED_TOPICS = new Set(['general', 'correction', 'pitch', 'supporter', 'legal']);
const assetCache = new Map<string, CachedAsset>();
const rateLimitMap = new Map<string, { count: number; resetAt: number }>();

function ensureStorageDir(): void {
  if (!fs.existsSync(STORAGE_DIR)) {
    fs.mkdirSync(STORAGE_DIR, { recursive: true });
  }
}

function readJsonArray<T>(filePath: string): T[] {
  ensureStorageDir();
  if (!fs.existsSync(filePath)) return [];
  try {
    const raw = fs.readFileSync(filePath, 'utf8');
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as T[]) : [];
  } catch {
    return [];
  }
}

function writeJsonAtomic<T>(filePath: string, data: T[]): void {
  ensureStorageDir();
  const tmpFile = `${filePath}.${process.pid}.${Date.now()}.tmp`;
  fs.writeFileSync(tmpFile, JSON.stringify(data, null, 2), 'utf8');
  fs.renameSync(tmpFile, filePath);
}

function isRateLimited(ip: string, limit = 15, windowMs = 15 * 60 * 1000): boolean {
  const now = Date.now();
  const entry = rateLimitMap.get(ip);
  if (!entry || entry.resetAt <= now) {
    rateLimitMap.set(ip, { count: 1, resetAt: now + windowMs });
    return false;
  }
  entry.count += 1;
  return entry.count > limit;
}

function buildHugoSync(): void {
  if (!fs.existsSync(HUGO_BIN)) {
    throw new Error(`Hugo binary not found at ${HUGO_BIN}`);
  }
  execFileSync(HUGO_BIN, ['--destination', 'dist', '--cleanDestinationDir', '--minify'], {
    cwd: ROOT_DIR,
    stdio: 'inherit',
  });
  assetCache.clear();
}

let isBuilding = false;
let pendingRebuild = false;

function scheduleHugoRebuild(): void {
  if (isBuilding) {
    pendingRebuild = true;
    return;
  }
  isBuilding = true;
  execFile(HUGO_BIN, ['--destination', 'dist', '--cleanDestinationDir', '--minify'], { cwd: ROOT_DIR }, (err, _stdout, stderr) => {
    isBuilding = false;
    if (err) {
      console.error('Hugo rebuild error:', stderr || err.message);
    } else {
      assetCache.clear();
      console.log('Hugo rebuild complete.');
    }
    if (pendingRebuild) {
      pendingRebuild = false;
      scheduleHugoRebuild();
    }
  });
}

if (!IS_PROD || !fs.existsSync(path.join(DIST_DIR, 'index.html'))) {
  buildHugoSync();
}
ensureStorageDir();

function resolveCompressibleFile(reqPath: string): string | null {
  let decoded: string;
  try {
    decoded = decodeURIComponent(reqPath);
  } catch {
    return null;
  }
  const clean = decoded.replace(/^\/+/, '');
  const base = path.resolve(DIST_DIR, clean);
  if (base !== DIST_DIR && !base.startsWith(DIST_PREFIX)) {
    return null;
  }

  const candidates = [base, `${base}.html`, path.join(base, 'index.html')];
  for (const candidate of candidates) {
    if (candidate !== DIST_DIR && !candidate.startsWith(DIST_PREFIX)) continue;
    try {
      const stat = fs.statSync(candidate);
      if (stat.isFile()) {
        const ext = path.extname(candidate).toLowerCase();
        if (ext in MIME_TYPES) return candidate;
        return null;
      }
    } catch {
      // Ignore missing candidates
    }
  }
  return null;
}

function getCachedAsset(filePath: string): CachedAsset | null {
  const cached = assetCache.get(filePath);
  if (cached) return cached;

  try {
    const stat = fs.statSync(filePath);
    const raw = fs.readFileSync(filePath);
    const ext = path.extname(filePath).toLowerCase();
    const contentType = MIME_TYPES[ext] || 'application/octet-stream';
    const etag = `W/"${stat.size.toString(16)}-${Math.floor(stat.mtimeMs).toString(16)}"`;
    const cacheControl = ext === '.json' || ext === '.xml'
      ? 'public, max-age=60, stale-while-revalidate=600'
      : 'public, max-age=30, stale-while-revalidate=300';
    const gz = zlib.gzipSync(raw, { level: 6 });
    const br = zlib.brotliCompressSync(raw, {
      params: { [zlib.constants.BROTLI_PARAM_QUALITY]: 4 },
    });

    const entry: CachedAsset = { raw, gz, br, etag, contentType, cacheControl };
    if (assetCache.size > 400) {
      const firstKey = assetCache.keys().next().value;
      if (firstKey) assetCache.delete(firstKey);
    }
    assetCache.set(filePath, entry);
    return entry;
  } catch {
    return null;
  }
}

const app = express();
app.disable('x-powered-by');
app.use(express.json({ limit: '32kb' }));

app.post('/api/newsletter', (req: Request, res: Response) => {
  res.setHeader('Cache-Control', 'no-store');
  const clientIp = req.ip || 'unknown';
  if (isRateLimited(clientIp, 20)) {
    res.status(429).json({ success: false, error: 'Too many requests. Please try again later.' });
    return;
  }

  const rawEmail = typeof req.body?.email === 'string' ? req.body.email.trim() : '';
  if (!rawEmail || rawEmail.length > 254 || !EMAIL_REGEX.test(rawEmail)) {
    res.status(400).json({ success: false, error: 'Please enter a valid email address.' });
    return;
  }

  const normalized = rawEmail.toLowerCase();
  const subscribers = readJsonArray<SubscriberRecord>(SUBSCRIBERS_FILE);
  const existing = subscribers.find((s) => s.email === normalized);
  if (existing) {
    res.json({
      success: true,
      alreadySubscribed: true,
      message: 'You are already subscribed to the Veridus Post Morning Briefing.',
    });
    return;
  }

  const record: SubscriberRecord = {
    email: normalized,
    subscribedAt: new Date().toISOString(),
    token: crypto.randomBytes(24).toString('hex'),
  };
  subscribers.push(record);
  writeJsonAtomic(SUBSCRIBERS_FILE, subscribers);

  res.status(201).json({
    success: true,
    message: 'Thank you for subscribing to the Veridus Post Morning Briefing.',
    unsubscribePath: `/api/newsletter/unsubscribe?token=${record.token}`,
  });
});

app.get('/api/newsletter/unsubscribe', (req: Request, res: Response) => {
  res.setHeader('Cache-Control', 'no-store');
  const token = typeof req.query.token === 'string' ? req.query.token.trim() : '';
  if (!token || !/^[a-f0-9]{48}$/.test(token)) {
    res.status(400).type('html').send('<!doctype html><meta charset=utf-8><title>Unsubscribe | Veridus Post</title><body style="font-family:sans-serif;max-width:520px;margin:4rem auto;padding:0 1.25rem;line-height:1.6"><h1>Invalid unsubscribe link</h1><p>This unsubscribe token is missing or invalid. Please write to <a href="mailto:contact@veriduspost.com">contact@veriduspost.com</a>.</p><p><a href="/">Return to Veridus Post</a></p></body>');
    return;
  }

  const subscribers = readJsonArray<SubscriberRecord>(SUBSCRIBERS_FILE);
  const next = subscribers.filter((s) => s.token !== token);
  if (next.length !== subscribers.length) {
    writeJsonAtomic(SUBSCRIBERS_FILE, next);
  }

  res.status(200).type('html').send('<!doctype html><meta charset=utf-8><title>Unsubscribed | Veridus Post</title><body style="font-family:sans-serif;max-width:520px;margin:4rem auto;padding:0 1.25rem;line-height:1.6"><h1>You have been unsubscribed</h1><p>Your email address has been removed from the Veridus Post Morning Briefing.</p><p><a href="/">Return to Veridus Post</a></p></body>');
});

app.post('/api/newsletter/unsubscribe', (req: Request, res: Response) => {
  res.setHeader('Cache-Control', 'no-store');
  const token = typeof req.body?.token === 'string' ? req.body.token.trim() : '';
  if (!token || !/^[a-f0-9]{48}$/.test(token)) {
    res.status(400).json({ success: false, error: 'Invalid unsubscribe token.' });
    return;
  }
  const subscribers = readJsonArray<SubscriberRecord>(SUBSCRIBERS_FILE);
  const next = subscribers.filter((s) => s.token !== token);
  if (next.length !== subscribers.length) {
    writeJsonAtomic(SUBSCRIBERS_FILE, next);
  }
  res.json({ success: true, message: 'You have been unsubscribed from the Morning Briefing.' });
});

app.post('/api/contact', (req: Request, res: Response) => {
  res.setHeader('Cache-Control', 'no-store');
  const clientIp = req.ip || 'unknown';
  if (isRateLimited(clientIp, 12)) {
    res.status(429).json({ success: false, error: 'Too many submissions. Please wait a few minutes and try again.' });
    return;
  }

  const topic = typeof req.body?.topic === 'string' ? req.body.topic.trim().toLowerCase() : 'general';
  const name = typeof req.body?.name === 'string' ? req.body.name.trim() : '';
  const email = typeof req.body?.email === 'string' ? req.body.email.trim().toLowerCase() : '';
  const articleUrl = typeof req.body?.articleUrl === 'string' ? req.body.articleUrl.trim() : '';
  const message = typeof req.body?.message === 'string' ? req.body.message.trim() : '';

  if (!ALLOWED_TOPICS.has(topic)) {
    res.status(400).json({ success: false, error: 'Please choose a valid inquiry topic.' });
    return;
  }
  if (name.length < 2 || name.length > 120) {
    res.status(400).json({ success: false, error: 'Please enter your name (2 to 120 characters).' });
    return;
  }
  if (!email || email.length > 254 || !EMAIL_REGEX.test(email)) {
    res.status(400).json({ success: false, error: 'Please enter a valid email address.' });
    return;
  }
  if (articleUrl && (articleUrl.length > 300 || !/^(https?:\/\/|\/)/i.test(articleUrl))) {
    res.status(400).json({ success: false, error: 'Article URL must begin with https://, http://, or /.' });
    return;
  }
  if (message.length < 10 || message.length > 5000) {
    res.status(400).json({ success: false, error: 'Please include a message between 10 and 5,000 characters.' });
    return;
  }

  const messages = readJsonArray<ContactMessageRecord>(MESSAGES_FILE);
  const record: ContactMessageRecord = {
    id: crypto.randomUUID(),
    topic,
    name,
    email,
    articleUrl,
    message,
    receivedAt: new Date().toISOString(),
  };
  messages.push(record);
  writeJsonAtomic(MESSAGES_FILE, messages);

  res.status(201).json({
    success: true,
    message: 'Thank you. Your message has been received by the Veridus Post newsroom.',
    referenceId: record.id.slice(0, 8),
  });
});

app.use((req: Request, res: Response, next: NextFunction) => {
  if ((req.method !== 'GET' && req.method !== 'HEAD') || req.path.startsWith('/api/')) {
    next();
    return;
  }
  const filePath = resolveCompressibleFile(req.path);
  if (!filePath) {
    next();
    return;
  }
  const asset = getCachedAsset(filePath);
  if (!asset) {
    next();
    return;
  }

  res.setHeader('Content-Type', asset.contentType);
  res.setHeader('Vary', 'Accept-Encoding');
  res.setHeader('ETag', asset.etag);
  res.setHeader('Cache-Control', asset.cacheControl);

  if (req.headers['if-none-match'] === asset.etag) {
    res.status(304).end();
    return;
  }

  const acceptEncoding = String(req.headers['accept-encoding'] || '');
  let body = asset.raw;
  if (/\bbr\b/.test(acceptEncoding)) {
    res.setHeader('Content-Encoding', 'br');
    body = asset.br;
  } else if (/\bgzip\b/.test(acceptEncoding)) {
    res.setHeader('Content-Encoding', 'gzip');
    body = asset.gz;
  }

  res.setHeader('Content-Length', body.length);
  if (req.method === 'HEAD') {
    res.status(200).end();
    return;
  }
  res.status(200).end(body);
});

app.use(
  '/fonts',
  express.static(path.join(DIST_DIR, 'fonts'), {
    maxAge: '30d',
    immutable: true,
  }),
);

app.use(
  express.static(DIST_DIR, {
    extensions: ['html'],
    setHeaders: (res, filePath) => {
      if (filePath.includes('_hu_')) {
        res.setHeader('Cache-Control', 'public, max-age=2592000, immutable');
      } else if (/\.(jpe?g|png|webp|avif|gif|ico)$/i.test(filePath)) {
        res.setHeader('Cache-Control', 'public, max-age=86400, stale-while-revalidate=604800');
      }
    },
  }),
);

app.use((req: Request, res: Response) => {
  const notFoundPage = path.join(DIST_DIR, '404.html');
  const asset = fs.existsSync(notFoundPage) ? getCachedAsset(notFoundPage) : null;
  if (asset) {
    res.setHeader('Content-Type', asset.contentType);
    res.setHeader('Vary', 'Accept-Encoding');
    const acceptEncoding = String(req.headers['accept-encoding'] || '');
    let body = asset.raw;
    if (/\bbr\b/.test(acceptEncoding)) {
      res.setHeader('Content-Encoding', 'br');
      body = asset.br;
    } else if (/\bgzip\b/.test(acceptEncoding)) {
      res.setHeader('Content-Encoding', 'gzip');
      body = asset.gz;
    }
    res.setHeader('Content-Length', body.length);
    res.status(404).end(req.method === 'HEAD' ? undefined : body);
    return;
  }
  res.status(404).send('404 Not Found');
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Veridus Post Hugo server running on http://0.0.0.0:${PORT}`);
});

if (!IS_PROD) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const triggerRebuild = () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(scheduleHugoRebuild, 250);
  };

  const watchTargets = ['hugo.toml', 'content', 'layouts', 'assets', 'data', 'static'];
  for (const target of watchTargets) {
    const fullPath = path.join(ROOT_DIR, target);
    if (!fs.existsSync(fullPath)) continue;
    try {
      const stat = fs.statSync(fullPath);
      fs.watch(fullPath, { recursive: stat.isDirectory() }, triggerRebuild);
    } catch {
      // Ignore recursive watch limitations if any
    }
  }
}
