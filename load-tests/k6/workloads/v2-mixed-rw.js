import { collectProducts, runEveryOperationOnce, runWeightedOperation } from '../lib/v2-flows.js';
import { v2WorkloadOptions } from '../lib/v2.js';

// V2 읽기 80% : 쓰기 20% 혼합. 쓰기는 설정 저장, 리뷰, 친구 요청 수락, 결제 → 수락, 결제 → 취소 흐름이다.
// 스테이징 전용이다. 데이터가 계속 쌓이고 크레딧과 재고가 움직인다.
const WORKLOAD = 'v2_rw';

export const options = v2WorkloadOptions({
  workload: WORKLOAD,
  exec: 'mixedRw',
  smokeExec: 'smokeAll',
  rwThresholds: true,
});

export function setup() {
  return collectProducts();
}

export function mixedRw(data) {
  runWeightedOperation({ workload: WORKLOAD, products: data.products });
}

export function smokeAll(data) {
  runEveryOperationOnce({ workload: WORKLOAD, products: data.products });
}
