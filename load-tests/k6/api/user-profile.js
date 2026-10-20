import { getJson } from '../lib/http.js';
import { apiTestOptions } from '../lib/profiles.js';

const ENDPOINT = 'user_profile';

export const options = apiTestOptions({ endpoint: ENDPOINT, exec: 'getUserProfile' });

export function getUserProfile() {
  getJson({
    path: '/api/users/me',
    endpoint: ENDPOINT,
    validateData: (data) => Number.isInteger(data?.userId) && data.userId > 0,
  });
}
