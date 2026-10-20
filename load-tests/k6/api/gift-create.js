import { requireEnv, requirePositiveInteger } from '../lib/config.js';
import { postJson } from '../lib/http.js';
import { apiTestOptions } from '../lib/profiles.js';

const ENDPOINT = 'gift_create';
const PRODUCT_ID = requirePositiveInteger('PRODUCT_ID');
const RECIPIENT_USER_ID = requirePositiveInteger('RECIPIENT_USER_ID');
const QUANTITY = requirePositiveInteger('QUANTITY');
const EXPECTED_UNIT_PRICE = Number(requireEnv('EXPECTED_UNIT_PRICE'));

if (!Number.isInteger(EXPECTED_UNIT_PRICE) || EXPECTED_UNIT_PRICE < 0) {
  throw new Error('EXPECTED_UNIT_PRICE must be a non-negative integer.');
}

export const options = apiTestOptions({
  endpoint: ENDPOINT,
  exec: 'createGift',
  changesData: true,
});

export function createGift() {
  postJson({
    path: '/api/gifts',
    endpoint: ENDPOINT,
    expectedStatus: 201,
    headers: { 'Idempotency-Key': uuidV4() },
    body: {
      productId: PRODUCT_ID,
      recipientUserId: RECIPIENT_USER_ID,
      quantity: QUANTITY,
      expectedUnitPrice: EXPECTED_UNIT_PRICE,
    },
    validateData: (data) => Number.isInteger(data?.gift?.giftId),
  });
}

function uuidV4() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (character) => {
    const random = Math.floor(Math.random() * 16);
    const value = character === 'x' ? random : (random & 0x3) | 0x8;
    return value.toString(16);
  });
}
