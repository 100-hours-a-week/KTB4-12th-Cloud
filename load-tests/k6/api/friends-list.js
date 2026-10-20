import { getJson } from '../lib/http.js';
import { apiTestOptions } from '../lib/profiles.js';

const ENDPOINT = 'friends_list';

export const options = apiTestOptions({ endpoint: ENDPOINT, exec: 'getFriends' });

export function getFriends() {
  getJson({
    path: '/api/friends',
    endpoint: ENDPOINT,
    validateData: (data) => Array.isArray(data?.friends),
  });
}
