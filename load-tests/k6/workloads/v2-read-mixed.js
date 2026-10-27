import { READ_ENDPOINTS, callEndpoint, weightedEndpointName } from '../lib/v2-endpoints.js';
import { v2WorkloadOptions } from '../lib/v2.js';

const WORKLOAD = 'v2_read';

export const options = v2WorkloadOptions({ workload: WORKLOAD, exec: 'mixedRead', smokeExec: 'smokeAll' });

export function mixedRead() {
  callEndpoint(weightedEndpointName(), WORKLOAD);
}

export function smokeAll() {
  for (const name of Object.keys(READ_ENDPOINTS)) {
    callEndpoint(name, WORKLOAD);
  }
}
