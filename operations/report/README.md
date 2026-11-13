# V3 신고 worker 활성화

신고 API는 Outbox 저장 이후 비동기 전달하므로 API 202만으로 Discord·Sheets 성공을 판정하지 않는다.

운영 실행 주체는 notification-service 하나다. V2 production Main은 사전 조회에서 desired/running/pending 모두 0이었다.

- REPORT_MQ_ENABLED / REPORT_DISCORD_CONSUMER_ENABLED / REPORT_SHEETS_CONSUMER_ENABLED=true
- OUTBOX_ENABLED=true, OUTBOX_SEARCH_RELAY_ENABLED=false
- SCHEDULING_ENABLED=false 유지: 신고 전용 루프를 포함한 새 Main 이미지가 필요하다. 구 3.0.3에 플래그만 적용하면 안 된다.
- 기존 Discord 웹후크·Google 서비스 계정은 Secret에서 참조하고, Sheets 대상 ID·탭만 notification values에 지정한다.
- 사용자의 2026-11-13 추가 승인으로 Main 5개와 Payment 이미지는 모두 3.0.4로 갱신한다. 신고 실행 플래그는 notification에만 적용하며 AI·replicas·모니터링·일반 알림/검색/결제/프로파일링 플래그는 바꾸지 않는다.

사전 확인: 기존 서비스 계정으로 Sheets metadata HTTP200, '버그 제보' 탭 존재. 이는 쓰기 권한 및 실제 행 기록 성공 증거가 아니다. RabbitMQ Report 큐·binding·policy 계약을 읽기로 확인했다.

배포 후 notification Ready/재시작/Health, 정확한 이미지·환경값, Report.discord/Report.sheets consumer 각1을 확인한다. Report.retry/DLQ는 처리 consumer가 없는 구성이다. topology preflight 오류가 있으면 성공 처리하지 않는다.

실제 신고 한 건을 전송할 때는 검증 내용과 Discord·Sheets 대상이 명확한 최종 검증을 별도로 수행한다. 기존 outbox가 있다면 활성화 직후 실제 과거 신고가 전달될 수 있다. Outbox/큐 삭제로 이를 숨기지 않는다.

롤백은 notification values 변경을 revert하여 develop→main으로 승격한다. 메시지·Outbox·큐는 삭제하지 않는다. 외부에 이미 전달된 메시지는 롤백으로 취소되지 않는다.
