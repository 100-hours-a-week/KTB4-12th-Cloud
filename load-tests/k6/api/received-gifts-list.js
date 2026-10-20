import { getJson } from '../lib/http.js';
import { apiTestOptions } from '../lib/profiles.js';

const ENDPOINT = 'received_gifts_list';

export const options = apiTestOptions({ endpoint: ENDPOINT, exec: 'getReceivedGifts' });

export function getReceivedGifts() {
  getJson({
    path: '/api/gifts/received',
    endpoint: ENDPOINT,
    validateData: (data) => Array.isArray(data?.items),
  });
}
