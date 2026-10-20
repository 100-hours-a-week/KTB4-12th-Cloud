import { requireEnv } from '../lib/config.js';
import { getJson } from '../lib/http.js';
import { apiTestOptions } from '../lib/profiles.js';

const ENDPOINT = 'friends_search';
const SEARCH_QUERY = encodeURIComponent(requireEnv('SEARCH_QUERY'));

export const options = apiTestOptions({ endpoint: ENDPOINT, exec: 'searchFriends' });

export function searchFriends() {
  getJson({
    path: `/api/friends/search?query=${SEARCH_QUERY}`,
    endpoint: ENDPOINT,
    validateData: (data) => Array.isArray(data?.friends),
  });
}
