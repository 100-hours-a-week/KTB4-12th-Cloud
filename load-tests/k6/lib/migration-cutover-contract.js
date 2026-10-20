// 의도된 거절도 업무 성공으로 세지 않는다. 실제 Freeze 구간과의 일치는 원장으로 별도 검증한다.
export function classifyCutoverRead(status, body) {
  return status === 200 && body?.error == null && Array.isArray(body?.data?.products)
    ? 'read_success' : 'read_failure';
}

export function classifyCutoverGift(status, body) {
  const id = body?.data?.gift?.giftId;
  if (status === 201 && body?.error == null && Number.isSafeInteger(id) && id > 0) {
    return 'gift_success';
  }
  if (status === 503 && body?.error?.code === 'MIGRATION_WRITE_FROZEN') {
    return 'write_frozen';
  }
  return 'write_failure';
}
