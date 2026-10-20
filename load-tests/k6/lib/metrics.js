import { Rate } from 'k6/metrics';

export const businessErrors = new Rate('business_errors');
