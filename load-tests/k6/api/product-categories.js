import { getJson } from '../lib/http.js';
import { apiTestOptions } from '../lib/profiles.js';

const ENDPOINT = 'product_categories';

export const options = apiTestOptions({ endpoint: ENDPOINT, exec: 'getProductCategories' });

export function getProductCategories() {
  getJson({
    path: '/api/products/categories',
    endpoint: ENDPOINT,
    validateData: (data) => Array.isArray(data?.categories),
  });
}
