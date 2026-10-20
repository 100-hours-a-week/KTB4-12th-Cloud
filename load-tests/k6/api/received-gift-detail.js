import { requirePositiveInteger } from '../lib/config.js';
import { getJson } from '../lib/http.js';
import { apiTestOptions } from '../lib/profiles.js';

const ENDPOINT = 'received_gift_detail';
const GIFT_ID = requirePositiveInteger('GIFT_ID');

export const options = apiTestOptions({ endpoint: ENDPOINT, exec: 'getReceivedGiftDetail' });

export function getReceivedGiftDetail() {
  getJson({
    path: `/api/gifts/received/${GIFT_ID}`,
    endpoint: ENDPOINT,
    validateData: (data) => data?.giftId === GIFT_ID,
  });
}
