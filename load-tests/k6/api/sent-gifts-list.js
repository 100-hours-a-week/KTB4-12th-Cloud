import { getJson } from '../lib/http.js';
import { apiTestOptions } from '../lib/profiles.js';

const ENDPOINT = 'sent_gifts_list';

export const options = apiTestOptions({ endpoint: ENDPOINT, exec: 'getSentGifts' });

export function getSentGifts() {
  getJson({
    path: '/api/gifts/sent',
    endpoint: ENDPOINT,
    validateData: (data) => Array.isArray(data?.items),
  });
}
