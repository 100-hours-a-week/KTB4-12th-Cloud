import { requirePositiveInteger } from '../lib/config.js';
import { postJson } from '../lib/http.js';
import { apiTestOptions } from '../lib/profiles.js';

const ENDPOINT = 'gift_preflight';
const PRODUCT_ID = requirePositiveInteger('PRODUCT_ID');
const RECIPIENT_USER_ID = requirePositiveInteger('RECIPIENT_USER_ID');
const QUANTITY = requirePositiveInteger('QUANTITY');

export const options = apiTestOptions({ endpoint: ENDPOINT, exec: 'preflightGift' });

export function preflightGift() {
  postJson({
    path: '/api/gifts/preflight',
    endpoint: ENDPOINT,
    body: {
      productId: PRODUCT_ID,
      recipientUserId: RECIPIENT_USER_ID,
      quantity: QUANTITY,
    },
    validateData: (data) => data !== null && typeof data === 'object',
  });
}
