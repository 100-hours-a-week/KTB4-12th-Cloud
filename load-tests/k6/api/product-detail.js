import { requirePositiveInteger } from '../lib/config.js';
import { getJson } from '../lib/http.js';
import { apiTestOptions } from '../lib/profiles.js';

const ENDPOINT = 'product_detail';
const PRODUCT_ID = requirePositiveInteger('PRODUCT_ID');

export const options = apiTestOptions({
  endpoint: ENDPOINT,
  exec: 'getProductDetail',
  changesData: true,
});

export function getProductDetail() {
  getJson({
    path: `/api/products/${PRODUCT_ID}`,
    endpoint: ENDPOINT,
    validateData: (data) => data?.product?.productId === PRODUCT_ID,
  });
}
