// 로컬 모의 서버. V2 k6 시나리오 코드(가드, 게이트 쿠키, JWT 서명, 응답 검사, 쓰기 흐름)를 실제 서버에 보내기 전에 검증하는 용도다.
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
const PRICE_SKEW = Number(process.env.MOCK_PRICE_SKEW || 0); // 결제 가격 불일치(409)를 일으키는 시험용
const NO_FRIENDS = process.env.MOCK_NO_FRIENDS === '1'; // 친구 목록을 비워 결제 흐름 건너뜀을 일으키는 시험용

const stats = freshStats();
const state = { reviews: new Set(), orders: {}, gifts: {}, requests: {}, nextId: 1 };

function freshStats() {
  return { hits: {}, subjects: [], rejected: { gate: 0, auth: 0 }, logins: 0, reads: 0, writes: 0, partners: [] };
}

function reply(response, status, body) {
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
  return Number(payload.sub);
}

const pagination = { nextCursor: null, hasNext: false };
const CATALOG = Array.from({ length: 12 }, (_, index) => 101 + index).map((id) => ({ productId: id, brandName: 'B', productName: `P${id}`, price: 10000 + id, thumbnailUrl: 'u' }));
const friendOf = (subject) => 50000 + subject; // 모의 서버에서 사용자 s의 친구는 항상 50000 + s
const ok = (data) => ({ status: 200, body: { message: 'ok', data } });
const created = (data) => ({ status: 201, body: { message: 'created', data } });
const fail = (status, code) => ({ status, body: { error: { code } } });

function readBody(request) {
  return new Promise((resolve) => {
    let raw = '';
    request.on('data', (chunk) => (raw += chunk));
    request.on('end', () => {
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch {
        resolve(null);
      }
    });
  });
}

// [메서드, 경로 패턴, 처리 함수]. 패턴의 :id는 숫자나 UUID 하나에 대응한다.
const routes = [
  ['GET', '/credits/balance', () => ok({ balance: 500000 })],
  ['GET', '/preferences', () => ok({ preference: null })],
  ['GET', '/preferences/dislike-categories', () => ok({ maxSelectableCount: 5, categories: [{ categoryId: 1, name: '뷰티', isSelected: false }] })],
  ['GET', '/products', () => ok({ products: CATALOG, appliedSort: 'POPULAR', pagination })],
  ['GET', '/products/:id', (c) => ok({ product: { productId: Number(c.params[0]) } })],
  ['GET', '/users/me', (c) => ok({ userId: c.subject })],
  ['GET', '/friends', (c) => ok({ friends: NO_FRIENDS ? [] : [{ friendId: 1, userId: friendOf(c.subject), name: 'f', email: 'f@x', birth: null }], pagination })],
  // 실제 BE처럼 결제 주문과 연결된 선물만 보인다(order_record_id IS NOT NULL). 픽스처 선물은 보이지 않는다.
  ['GET', '/gifts/received', (c) => ok({
    items: Object.entries(state.gifts)
      .filter(([, gift]) => gift.recipient === c.subject)
      .map(([giftId, gift]) => ({ giftId: Number(giftId), orderId: gift.orderId, giftStatus: gift.status })),
    pagination,
  })],
  ['GET', '/notifications', () => ok({ items: [], pagination })],
  ['GET', '/notifications/unread-count', () => ok({ unreadCount: 0 })],
  ['GET', '/friend-requests/received', () => ok({ items: [], pagination })],
  ['GET', '/friend-requests/sent', () => ok({ items: [], pagination })],
  ['PUT', '/preferences', (c) => (typeof c.body.preference === 'string' ? ok({ preference: c.body.preference }) : fail(400, 'INVALID_REQUEST'))],
  ['PUT', '/preferences/dislike-categories', (c) => (Array.isArray(c.body.categoryIds) ? ok({ selectedCategoryIds: c.body.categoryIds }) : fail(400, 'INVALID_REQUEST'))],
  ['POST', '/gifts/:id/review', (c) => {
    const key = `${c.subject}:${c.params[0]}`;
    const gift = state.gifts[c.params[0]];
    // 리뷰는 받는 사람이 완료(수락)한 선물에만 쓸 수 있다.
    if (!gift || gift.recipient !== c.subject || gift.status !== 'COMPLETED') return fail(404, 'GIFT_NOT_FOUND');
    if (!(c.body.rating >= 1 && c.body.rating <= 5)) return fail(400, 'INVALID_REQUEST');
    if (state.reviews.has(key)) return fail(409, 'REVIEW_ALREADY_EXISTS');
    state.reviews.add(key);
    return created({ review: { reviewId: state.nextId++, giftId: Number(c.params[0]) } });
  }],
  ['DELETE', '/gifts/:id/review', (c) => {
    const key = `${c.subject}:${c.params[0]}`;
    if (!state.reviews.delete(key)) return fail(404, 'REVIEW_NOT_FOUND');
    return ok({ giftId: Number(c.params[0]) });
  }],
  ['POST', '/friend-requests', (c) => {
    if (!Number.isInteger(c.body.receiverId) || c.body.receiverId <= 0) return fail(400, 'INVALID_REQUEST');
    if (c.body.receiverId === c.subject) return fail(422, 'FRIEND_CANNOT_ADD_SELF');
    const requestId = state.nextId++;
    stats.partners.push(c.body.receiverId);
    state.requests[requestId] = { from: c.subject, to: c.body.receiverId, status: 'PENDING' };
    return created({ requestId, status: 'PENDING', requesterId: c.subject, receiverId: c.body.receiverId });
  }],
  ['POST', '/friend-requests/:id/accept', (c) => {
    const found = state.requests[c.params[0]];
    if (!found || found.to !== c.subject) return fail(404, 'FRIEND_REQUEST_NOT_FOUND');
    if (found.status !== 'PENDING') return fail(409, 'FRIEND_REQUEST_ALREADY_PROCESSED');
    found.status = 'ACCEPTED';
    return ok({ requestId: Number(c.params[0]), status: 'ACCEPTED' });
  }],
  ['POST', '/payments', (c) => {
    if (!/^[A-Za-z0-9:_-]{1,100}$/.test(c.headers['idempotency-key'] || '')) return fail(400, 'INVALID_REQUEST');
    const { productId, recipientUserId, quantity, expectedUnitPrice } = c.body;
    const product = CATALOG.find((item) => item.productId === productId);
    if (!product) return fail(404, 'PRODUCT_NOT_FOUND');
    if (recipientUserId !== friendOf(c.subject)) return fail(422, 'RECIPIENT_NOT_FRIEND');
    if (expectedUnitPrice !== product.price + PRICE_SKEW) return fail(409, 'GIFT_CONDITIONS_CHANGED');
    if (!(quantity >= 1 && quantity <= 10)) return fail(400, 'INVALID_REQUEST');
    const orderId = crypto.randomUUID();
    const giftId = state.nextId++;
    state.orders[orderId] = { payer: c.subject, recipient: recipientUserId, giftId, status: 'COMPLETED' };
    state.gifts[giftId] = { recipient: recipientUserId, orderId, status: 'WAITING_FOR_ACCEPTANCE' };
    return created({ orderId, paymentKey: 'pk', paymentStatus: 'COMPLETED', giftId, giftStatus: 'WAITING_FOR_ACCEPTANCE', amount: product.price });
  }],
  ['GET', '/payments/:id', (c) => {
    const order = state.orders[c.params[0]];
    if (!order || order.payer !== c.subject) return fail(404, 'PAYMENT_NOT_FOUND');
    return ok({ orderId: c.params[0], paymentStatus: order.status, giftId: order.giftId });
  }],
  ['POST', '/payments/:id/cancellations', (c) => {
    if (!/^[A-Za-z0-9:_-]{1,100}$/.test(c.headers['idempotency-key'] || '')) return fail(400, 'INVALID_REQUEST');
    const order = state.orders[c.params[0]];
    if (!order || order.payer !== c.subject) return fail(404, 'PAYMENT_NOT_FOUND');
    if (state.gifts[order.giftId].status !== 'WAITING_FOR_ACCEPTANCE') return fail(409, 'PAYMENT_NOT_CANCELABLE');
    order.status = 'CANCELED';
    state.gifts[order.giftId].status = 'CANCELED';
    return created({ orderId: c.params[0], paymentStatus: 'CANCELED', giftStatus: 'CANCELED' });
  }],
  ['POST', '/gifts/received/:id/accept', (c) => {
    const gift = state.gifts[c.params[0]];
    if (!gift || gift.recipient !== c.subject) return fail(404, 'GIFT_NOT_FOUND');
    if (gift.status !== 'WAITING_FOR_ACCEPTANCE') return fail(409, 'GIFT_NOT_ACCEPTABLE');
    gift.status = 'COMPLETED';
    return ok({ giftId: Number(c.params[0]), giftStatus: 'COMPLETED', rewardAmount: 100000 });
  }],
].map(([method, pattern, handler]) => ({
  method,
  pattern,
  handler,
  regex: new RegExp(`^${pattern.replace(/:id/g, '([0-9A-Za-z-]+)')}$`),
}));

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url, 'http://127.0.0.1');

  if (url.pathname === '/__stats') return reply(response, 200, stats);
  if (url.pathname === '/__reset') {
    Object.assign(stats, freshStats());
    state.reviews.clear();
    state.orders = {};
    state.gifts = {};
    state.requests = {};
    return reply(response, 200, {});
  }

  if (!(request.headers.cookie || '').split(/;\s*/).includes(`stg_gate=${GATE_TOKEN}`)) {
    stats.rejected.gate += 1;
    response.writeHead(403, { 'Content-Type': 'text/plain' });
    return response.end('Forbidden');
  }

  const path = url.pathname.replace(/^\/api/, '');
  const body = request.method === 'GET' || request.method === 'DELETE' ? {} : await readBody(request);

  if (request.method === 'POST' && path === '/auth/login') {
    stats.logins += 1;
    const match = /^loadtest(\d{5})@seonjalal\.invalid$/.exec(body?.email || '');
    const number = match ? Number(match[1]) : 0;
    // 기존 500명(loadtest001~500, 3자리)은 5자리 형식이 아니므로 로그인되지 않는다.
    if (!match || number < 501 || body.password !== PASSWORD) {
      return reply(response, 401, { error: { code: 'INVALID_CREDENTIALS' } });
    }
    const userId = ID_BASE + number + (number >= 5000 ? ID_GAP : 0);
    return reply(response, 200, {
      message: 'ok',
      data: { accessToken: 'mock', expiresIn: 3600, user: { userId, name: 'n', email: body.email }, isFirstLogin: false },
    });
  }

  const route = routes.find((item) => item.method === request.method && item.regex.test(path));
  if (!route) return reply(response, 404, { error: { code: 'NOT_FOUND' } });
  if (body === null) return reply(response, 400, { error: { code: 'INVALID_REQUEST' } });

  const subject = verifyBearer(request.headers.authorization);
  if (subject === null) {
    stats.rejected.auth += 1;
    return reply(response, 401, { error: { code: 'UNAUTHORIZED' } });
  }

  const key = request.method === 'GET' ? path : `${request.method} ${path}`;
  const normalized = key.replace(/\/(\d+|[0-9a-f]{8}-[0-9a-f-]{27,})(?=\/|$)/g, '/:id');
  stats.hits[normalized] = (stats.hits[normalized] || 0) + 1;
  if (request.method === 'GET') stats.reads += 1;
  else stats.writes += 1;
  if (!stats.subjects.includes(String(subject))) stats.subjects.push(String(subject));
  if (FAIL_PATH && path.includes(FAIL_PATH)) {
    return reply(response, 500, { error: { code: 'INTERNAL_SERVER_ERROR' } });
  }

  const result = route.handler({ subject, body, headers: request.headers, params: route.regex.exec(path).slice(1) });
  return reply(response, result.status, result.body);
});

server.listen(0, '127.0.0.1', () => {
  console.log(`PORT=${server.address().port}`);
});
