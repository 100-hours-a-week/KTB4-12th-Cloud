import { Rate } from 'k6/metrics';

import { v2Request } from './v2.js';

// V2에서 새로 생긴 읽기 API의 계약. 응답 구조는 BE 2.0.0 코드 기준이다.
// 알림·친구 요청은 픽스처에 데이터가 없어 빈 목록도 정상 응답으로 본다(빈 응답이 빠른지만 확인하는 시험).
const SEARCH_KEYWORDS = ['비타민', '텀블러', '향수', '디올', '스탠리', 'GNC', '에스티로더'];

// 검색 결과가 비는 비율. 알려진 키워드인데 계속 비면 ngram 검색이나 검색 엔진 설정 문제를 의심한다.
const searchEmpty = new Rate('v2_search_empty');

function isObject(data) {
  return data !== null && typeof data === 'object';
}

export const READ_ENDPOINTS = {
  products_search: {
    weight: 25,
    path: () =>
      `/api/products?sort=POPULAR&query=${encodeURIComponent(SEARCH_KEYWORDS[(__VU + __ITER) % SEARCH_KEYWORDS.length])}`,
    validate: (data) => Array.isArray(data?.products) && typeof data?.appliedSort === 'string',
    onSuccess: (data, tags) => searchEmpty.add(data.products.length === 0, tags),
  },
  credits_balance: {
    weight: 20,
    path: () => '/api/credits/balance',
    validate: (data) => Number.isInteger(data?.balance) && data.balance >= 0,
  },
  preferences: {
    weight: 10,
    path: () => '/api/preferences',
    validate: (data) => isObject(data) && Object.prototype.hasOwnProperty.call(data, 'preference'),
  },
  dislike_categories: {
    weight: 10,
    path: () => '/api/preferences/dislike-categories',
    validate: (data) => data?.maxSelectableCount === 5 && Array.isArray(data?.categories),
  },
  notifications_list: {
    weight: 15,
    path: () => '/api/notifications',
    validate: (data) => Array.isArray(data?.items) && isObject(data?.pagination),
  },
  notifications_unread: {
    weight: 10,
    path: () => '/api/notifications/unread-count',
    validate: (data) => Number.isInteger(data?.unreadCount) && data.unreadCount >= 0,
  },
  friend_requests_received: {
    weight: 5,
    path: () => '/api/friend-requests/received',
    validate: (data) => Array.isArray(data?.items) && isObject(data?.pagination),
  },
  friend_requests_sent: {
    weight: 5,
    path: () => '/api/friend-requests/sent',
    validate: (data) => Array.isArray(data?.items) && isObject(data?.pagination),
  },
};

export function callEndpoint(name, workload = 'v2') {
  const definition = READ_ENDPOINTS[name];
  if (!definition) {
    throw new Error(`Unknown V2 read endpoint '${name}'.`);
  }
  return v2Request({
    path: definition.path(),
    endpoint: name,
    workload,
    validate: definition.validate,
    onSuccess: definition.onSuccess,
  });
}

export function weightedEndpointName() {
  const names = Object.keys(READ_ENDPOINTS);
  let position = Math.random() * names.reduce((sum, name) => sum + READ_ENDPOINTS[name].weight, 0);
  for (const name of names) {
    position -= READ_ENDPOINTS[name].weight;
    if (position < 0) {
      return name;
    }
  }
  return names[names.length - 1];
}
