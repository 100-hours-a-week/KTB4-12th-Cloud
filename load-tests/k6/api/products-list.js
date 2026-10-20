import { getJson } from '../lib/http.js';
import { apiTestOptions } from '../lib/profiles.js';

const ENDPOINT = 'products_list';

export const options = apiTestOptions({ endpoint: ENDPOINT, exec: 'getProducts' });

export function getProducts() {
  getJson({
    path: '/api/products?sort=POPULAR',
    endpoint: ENDPOINT,
    validateData: (data) => Array.isArray(data?.products),
  });
}
