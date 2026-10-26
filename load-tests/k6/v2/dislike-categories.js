import { callEndpoint } from '../lib/v2-endpoints.js';
import { v2Options } from '../lib/v2.js';

const ENDPOINT = 'dislike_categories';

export const options = v2Options({ endpoint: ENDPOINT, exec: 'run' });

export function run() {
  callEndpoint(ENDPOINT);
}
