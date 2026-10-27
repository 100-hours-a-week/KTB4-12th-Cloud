import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

import { BASE_URL } from './config.js';
import {
  currentUserNumber,
  gateHeaders,
  isSmokeProfile,
  tokenForUserNumber,
  userCount,
  userIdForNumber,
  v2Request,
} from './v2.js';
import { READ_ENDPOINTS } from './v2-endpoints.js';

// V2 읽기 80% : 쓰기 20% 혼합 시나리오의 작업 정의.
// 쓰기는 서로 다른 사용자가 이어지는 흐름으로 만들어, DB에 미리 데이터를 심지 않고도 반복할 수 있게 한다.
// 서명 비밀로 어떤 사용자의 토큰도 만들 수 있으므로 친구 요청·선물을 '받는 사람'의 토큰도 같은 시험 안에서 만든다.

const KEYWORDS = ['비타민', '텀블러', '향수', '디올', '스탠리', 'GNC', '에스티로더'];
const skippedFlows = new Counter('v2_flow_skipped');

const V1_READS = {
  products_list: { path: () => '/api/products?sort=POPULAR', validate: (d) => Array.isArray(d?.products) },
  product_detail: {
    path: (ctx) => `/api/products/${pick(ctx.products).id}`,
    validate: (d) => Number.isInteger(d?.product?.productId),
  },
  user_profile: { path: () => '/api/users/me', validate: (d) => Number.isInteger(d?.userId) && d.userId > 0 },
  friends_list: { path: () => '/api/friends', validate: (d) => Array.isArray(d?.friends) },
  received_gifts_list: { path: () => '/api/gifts/received', validate: (d) => Array.isArray(d?.items) },
};

// 흐름을 건너뛴 이유를 기록한다. 스모크에서는 건너뜀도 실패다(조용히 통과하면 흐름이 검증되지 않았다는 사실이 가려진다).
function skip(ctx, flow, reason) {
  skippedFlows.add(1, { flow, reason });
  if (isSmokeProfile()) {
    check(null, { [`${flow}: flow ran without being skipped (${reason})`]: () => false }, { workload: ctx.workload, flow });
  }
}

function pick(list) {
  return list[Math.floor(Math.random() * list.length)];
}

function uuidV4() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (character) => {
    const random = Math.floor(Math.random() * 16);
    return (character === 'x' ? random : (random & 0x3) | 0x8).toString(16);
  });
}

function call(ctx, options) {
  return v2Request({ workload: ctx.workload, ...options });
}

// 시험 시작 시 한 번만 호출한다. 결제에 쓸 상품과 현재 가격을 검색으로 모은다(가격이 어긋나면 결제가 409로 거절된다).
export function collectProducts() {
  const headers = {
    Accept: 'application/json',
    Authorization: `Bearer ${tokenForUserNumber(1)}`,
    ...gateHeaders(),
  };
  const products = {};
  for (const keyword of KEYWORDS) {
    const response = http.get(`${BASE_URL}/api/products?sort=POPULAR&query=${encodeURIComponent(keyword)}`, {
      headers,
      timeout: '10s',
    });
    if (response.status !== 200) {
      throw new Error(`Product sampling failed with HTTP ${response.status}. Check the gate token, JWT secret and issuer.`);
    }
    for (const item of response.json('data.products') || []) {
      const price = Number(item.price);
      if (Number.isInteger(item.productId) && Number.isInteger(price) && price > 0) {
        products[item.productId] = { id: item.productId, price };
      }
    }
  }
  const list = Object.values(products);
  if (list.length < 10) {
    throw new Error(`Only ${list.length} usable products were found. Payments need at least 10.`);
  }
  return { products: list };
}

function partnerNumber(own) {
  let candidate = 1 + Math.floor(Math.random() * userCount());
  if (candidate === own) {
    candidate = (candidate % userCount()) + 1;
  }
  return candidate;
}

function readOperation(ctx, name) {
  const definition = { ...READ_ENDPOINTS, ...V1_READS }[name];
  return call(ctx, {
    path: definition.path(ctx),
    endpoint: name,
    validate: definition.validate,
    onSuccess: definition.onSuccess,
  });
}

function preferenceUpdate(ctx) {
  call(ctx, {
    method: 'PUT',
    path: '/api/preferences',
    endpoint: 'preference_update',
    body: { preference: `부하 시험 취향 ${Math.floor(Math.random() * 1e9)}` },
    expectedErrorStatuses: [422],
    validate: (d) => typeof d?.preference === 'string',
  });
}

function dislikeUpdate(ctx) {
  const ids = [];
  const count = 1 + Math.floor(Math.random() * 3);
  while (ids.length < count) {
    const id = 1 + Math.floor(Math.random() * 10);
    if (!ids.includes(id)) ids.push(id);
  }
  call(ctx, {
    method: 'PUT',
    path: '/api/preferences/dislike-categories',
    endpoint: 'dislike_update',
    body: { categoryIds: ids },
    expectedErrorStatuses: [422],
    validate: (d) => Array.isArray(d?.selectedCategoryIds),
  });
}

// 사용자 A가 B에게 친구 요청 → B가 수락(양쪽에 크레딧 +100,000, 친구 관계 생성, 알림 생성).
function friendFlow(ctx) {
  const own = currentUserNumber();
  const partner = partnerNumber(own);
  const created = call(ctx, {
    method: 'POST',
    path: '/api/friend-requests',
    endpoint: 'friend_request_create',
    body: { receiverId: userIdForNumber(partner) },
    successStatuses: [201],
    expectedErrorStatuses: [409, 422, 404],
    validate: (d) => Number.isInteger(d?.requestId),
  });
  if (created.response.status !== 201) return;
  call(ctx, {
    method: 'POST',
    path: `/api/friend-requests/${created.response.json('data.requestId')}/accept`,
    endpoint: 'friend_request_accept',
    userId: userIdForNumber(partner),
    expectedErrorStatuses: [409, 404, 403],
    validate: (d) => d?.status === 'ACCEPTED',
  });
}

function pickFriend(ctx) {
  const list = readOperation(ctx, 'friends_list');
  if (!list.valid) return null;
  const friends = list.response.json('data.friends') || [];
  if (friends.length === 0) {
    skip(ctx, 'payment', 'no_friend');
    return null;
  }
  return (isSmokeProfile() ? friends[0] : pick(friends)).userId;
}

function createPayment(ctx, friendId) {
  const product = pick(ctx.products);
  return call(ctx, {
    method: 'POST',
    path: '/api/payments',
    endpoint: 'payment_create',
    headers: { 'Idempotency-Key': uuidV4() },
    body: { productId: product.id, recipientUserId: friendId, quantity: 1, expectedUnitPrice: product.price },
    successStatuses: [201],
    expectedErrorStatuses: [202, 409, 422, 503],
    validate: (d) =>
      typeof d?.orderId === 'string' &&
      Number.isInteger(d?.giftId) &&
      d?.paymentStatus === 'COMPLETED' &&
      d?.giftStatus === 'WAITING_FOR_ACCEPTANCE',
  });
}

// 친구 목록 → 결제(Main → Payment 서버) → 상태 조회 → 받는 사람이 선물 수락(보낸 사람에게 +100,000).
function paymentAcceptFlow(ctx) {
  const friendId = pickFriend(ctx);
  if (friendId === null) return;
  const payment = createPayment(ctx, friendId);
  if (payment.response.status !== 201) return;
  const orderId = payment.response.json('data.orderId');
  const giftId = payment.response.json('data.giftId');
  call(ctx, {
    path: `/api/payments/${orderId}`,
    endpoint: 'payment_status',
    validate: (d) => d?.paymentStatus === 'COMPLETED',
  });
  acceptGift(ctx, giftId, friendId);
}

function acceptGift(ctx, giftId, recipientId) {
  return call(ctx, {
    method: 'POST',
    path: `/api/gifts/received/${giftId}/accept`,
    endpoint: 'gift_accept',
    userId: recipientId,
    expectedErrorStatuses: [409, 404],
    validate: (d) => d?.giftStatus === 'COMPLETED',
  });
}

// 친구 목록 → 결제 → 받는 사람이 수락 → 받는 사람이 리뷰 작성 → 삭제(소프트 삭제).
// 픽스처 선물은 결제 주문과 연결되지 않아 V2 목록·상세 API에 보이지 않으므로, 리뷰도 결제로 만든 선물에 쓴다.
function paymentAcceptReviewFlow(ctx) {
  const friendId = pickFriend(ctx);
  if (friendId === null) return;
  const payment = createPayment(ctx, friendId);
  if (payment.response.status !== 201) return;
  const giftId = payment.response.json('data.giftId');
  const accepted = acceptGift(ctx, giftId, friendId);
  if (accepted.response.status !== 200) return;
  const created = call(ctx, {
    method: 'POST',
    path: `/api/gifts/${giftId}/review`,
    endpoint: 'review_create',
    userId: friendId,
    body: { rating: 1 + Math.floor(Math.random() * 5), content: '부하 시험 리뷰' },
    successStatuses: [201],
    expectedErrorStatuses: [409, 404],
    validate: (d) => Number.isInteger(d?.review?.reviewId),
  });
  if (created.response.status === 201 || created.response.status === 409) {
    call(ctx, {
      method: 'DELETE',
      path: `/api/gifts/${giftId}/review`,
      endpoint: 'review_delete',
      userId: friendId,
      expectedErrorStatuses: [404],
      validate: (d) => Number.isInteger(d?.giftId),
    });
  }
}

// 친구 목록 → 결제 → 결제 취소(환불, 재고 복구, 결제 서버 취소 호출).
function paymentCancelFlow(ctx) {
  const friendId = pickFriend(ctx);
  if (friendId === null) return;
  const payment = createPayment(ctx, friendId);
  if (payment.response.status !== 201) return;
  call(ctx, {
    method: 'POST',
    path: `/api/payments/${payment.response.json('data.orderId')}/cancellations`,
    endpoint: 'payment_cancel',
    headers: { 'Idempotency-Key': uuidV4() },
    successStatuses: [201],
    expectedErrorStatuses: [409, 422, 503],
    validate: (d) => d?.paymentStatus === 'CANCELED',
  });
}

const reads = (name) => (ctx) => readOperation(ctx, name);

// 가중치는 반복 단위 %다. 요청 단위 쓰기 비율은 약 19.8%(읽기 97 : 쓰기 24)가 되도록 맞췄다.
export const OPERATIONS = [
  { name: 'products_search', weight: 14, run: reads('products_search') },
  { name: 'products_list', weight: 11, run: reads('products_list') },
  { name: 'product_detail', weight: 12, run: reads('product_detail') },
  { name: 'user_profile', weight: 6, run: reads('user_profile') },
  { name: 'friends_list', weight: 6, run: reads('friends_list') },
  { name: 'received_gifts_list', weight: 8, run: reads('received_gifts_list') },
  { name: 'credits_balance', weight: 8, run: reads('credits_balance') },
  { name: 'notifications_list', weight: 6, run: reads('notifications_list') },
  { name: 'notifications_unread', weight: 5, run: reads('notifications_unread') },
  { name: 'preferences', weight: 3, run: reads('preferences') },
  { name: 'dislike_categories', weight: 2, run: reads('dislike_categories') },
  { name: 'friend_requests_received', weight: 3, run: reads('friend_requests_received') },
  { name: 'friend_requests_sent', weight: 3, run: reads('friend_requests_sent') },
  { name: 'preference_update', weight: 2, run: preferenceUpdate },
  { name: 'dislike_update', weight: 2, run: dislikeUpdate },
  { name: 'friend_flow', weight: 3, run: friendFlow },
  { name: 'payment_accept_flow', weight: 4, run: paymentAcceptFlow },
  { name: 'payment_accept_review_flow', weight: 1, run: paymentAcceptReviewFlow },
  { name: 'payment_cancel_flow', weight: 1, run: paymentCancelFlow },
];

export function runWeightedOperation(ctx) {
  let position = Math.random() * OPERATIONS.reduce((sum, operation) => sum + operation.weight, 0);
  for (const operation of OPERATIONS) {
    position -= operation.weight;
    if (position < 0) {
      return operation.run(ctx);
    }
  }
  return OPERATIONS[OPERATIONS.length - 1].run(ctx);
}

export function runEveryOperationOnce(ctx) {
  for (const operation of OPERATIONS) {
    operation.run(ctx);
  }
}
