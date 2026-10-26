// 로컬 모의 서버. V2 k6 시나리오 코드(가드, 게이트 쿠키, JWT 서명, 응답 검사)를 실제 서버에 보내기 전에 검증하는 용도다.
// 127.0.0.1에서만 듣고, 실제 비밀은 사용하지 않는다(시험 전용 값만 환경변수로 받는다).
import crypto from 'node:crypto';
import http from 'node:http';

const SECRET = Buffer.from(process.env.MOCK_JWT_SECRET_BASE64 || '', 'base64');
const ISSUER = process.env.MOCK_ISSUER || '';
const GATE_TOKEN = process.env.MOCK_GATE_TOKEN || '';
const PASSWORD = process.env.MOCK_PASSWORD || '';
const ID_BASE = Number(process.env.MOCK_ID_BASE || 1000);
const ID_GAP = Number(process.env.MOCK_ID_GAP || 0);
const FAIL_PATH = process.env.MOCK_FAIL_PATH || '';

const stats = { hits: {}, subjects: [], rejected: { gate: 0, auth: 0 }, logins: 0 };

function envelope(response, status, body) {
  response.writeHead(status, { 'Content-Type': 'application/json' });
  response.end(JSON.stringify(body));
}

function verifyBearer(header) {
  const match = /^Bearer (.+)$/.exec(header || '');
  if (!match) return null;
  const parts = match[1].split('.');
  if (parts.length !== 3) return null;
  const expected = crypto.createHmac('sha256', SECRET).update(`${parts[0]}.${parts[1]}`).digest('base64url');
  const given = Buffer.from(parts[2]);
  const wanted = Buffer.from(expected);
  if (given.length !== wanted.length || !crypto.timingSafeEqual(given, wanted)) return null;
  let payload;
  try {
    payload = JSON.parse(Buffer.from(parts[1], 'base64url').toString());
  } catch {
    return null;
  }
  const now = Math.floor(Date.now() / 1000);
  if (payload.iss !== ISSUER) return null;
  if (!/^[1-9][0-9]*$/.test(String(payload.sub))) return null;
  if (!(payload.iat <= now)) return null; // 서버는 미래 iat를 거부한다.
  if (!(payload.exp > now)) return null;
  return String(payload.sub);
}

const pagination = { nextCursor: null, hasNext: false };
const routes = {
  '/credits/balance': () => ({ balance: 500000 }),
  '/preferences': () => ({ preference: null }),
  '/preferences/dislike-categories': () => ({
    maxSelectableCount: 5,
    categories: [{ categoryId: 1, name: '뷰티', isSelected: false }],
  }),
  '/products': () => ({
    products: [{ productId: 1, brandName: 'B', productName: 'P', price: 1000, thumbnailUrl: 'u' }],
    appliedSort: 'POPULAR',
    pagination,
  }),
  '/notifications': () => ({ items: [], pagination }),
  '/notifications/unread-count': () => ({ unreadCount: 0 }),
  '/friend-requests/received': () => ({ items: [], pagination }),
  '/friend-requests/sent': () => ({ items: [], pagination }),
};

const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://127.0.0.1');

  if (url.pathname === '/__stats') return envelope(response, 200, stats);
  if (url.pathname === '/__reset') {
    stats.hits = {};
    stats.subjects = [];
    stats.rejected = { gate: 0, auth: 0 };
    stats.logins = 0;
    return envelope(response, 200, {});
  }

  if (!(request.headers.cookie || '').split(/;\s*/).includes(`stg_gate=${GATE_TOKEN}`)) {
    stats.rejected.gate += 1;
    response.writeHead(403, { 'Content-Type': 'text/plain' });
    return response.end('Forbidden');
  }

  const path = url.pathname.replace(/^\/api/, '');

  if (request.method === 'POST' && path === '/auth/login') {
    let raw = '';
    request.on('data', (chunk) => (raw += chunk));
    request.on('end', () => {
      stats.logins += 1;
      let body = {};
      try {
        body = JSON.parse(raw);
      } catch {
        return envelope(response, 400, { error: { code: 'INVALID_REQUEST' } });
      }
      const match = /^loadtest(\d{5})@seonjalal\.invalid$/.exec(body.email || '');
      if (!match || body.password !== PASSWORD) {
        return envelope(response, 401, { error: { code: 'INVALID_CREDENTIALS' } });
      }
      const number = Number(match[1]);
      const userId = ID_BASE + number + (number >= 501 ? ID_GAP : 0);
      return envelope(response, 200, {
        message: 'ok',
        data: { accessToken: 'mock', expiresIn: 3600, user: { userId, name: 'n', email: body.email }, isFirstLogin: false },
      });
    });
    return;
  }

  const handler = routes[path];
  if (!handler || request.method !== 'GET') {
    return envelope(response, 404, { error: { code: 'NOT_FOUND' } });
  }

  const subject = verifyBearer(request.headers.authorization);
  if (subject === null) {
    stats.rejected.auth += 1;
    return envelope(response, 401, { error: { code: 'UNAUTHORIZED' } });
  }
  stats.hits[path] = (stats.hits[path] || 0) + 1;
  if (!stats.subjects.includes(subject)) stats.subjects.push(subject);
  if (FAIL_PATH && path.includes(FAIL_PATH)) {
    return envelope(response, 500, { error: { code: 'INTERNAL_SERVER_ERROR' } });
  }
  return envelope(response, 200, { message: 'ok', data: handler() });
});

server.listen(0, '127.0.0.1', () => {
  console.log(`PORT=${server.address().port}`);
});
