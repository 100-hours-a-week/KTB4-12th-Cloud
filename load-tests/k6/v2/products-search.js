import { callEndpoint } from '../lib/v2-endpoints.js';
import { v2Options } from '../lib/v2.js';

const ENDPOINT = 'products_search';

export const options = v2Options({ endpoint: ENDPOINT, exec: 'run' });

export function run() {
  callEndpoint(ENDPOINT);
}
