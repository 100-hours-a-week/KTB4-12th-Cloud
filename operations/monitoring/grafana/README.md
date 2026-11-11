# V3 결제 RabbitMQ 대시보드

`rabbitmq.json`은 별도 UID `seonjalal-v3-rabbitmq`의 브로커/큐 전용 화면. 노드별 스크랩 up·메모리·디스크·수집 대상 큐의 ready/unacked·Consumer·재전달률 제공. 기존 Agent 수집 범위만 조회하며 quorum 상태·전체 큐 존재·실제 업무 성공 보장으로 확대 해석 금지.

`redis.json`은 별도 UID `seonjalal-v3-redis`의 Redis/Sentinel 전용 화면. 연결·메모리·명령 처리량·클라이언트·적중률·eviction·복제 offset·Sentinel 감시/quorum/TILT 제공. exporter 배포·Agent redis.enabled 활성화·중앙 실제 표본 확인 후 사용. maxmemory=0 및 hit+miss rate=0은 비율 계산에서 제외. Pod 메모리 상한·업무 RPS·HTTP 성공률·초 단위 복제 지연과 구분. 실제 failover/정합성 시험은 수행하지 않음. 집계 전 원본 노드별 지표를 보존해 primary/replica를 이중 합산하지 않음.

원본: [BE 5a32a6aa의 결제 MQ 대시보드](https://github.com/100-hours-a-week/KTB4-12th-BE/blob/5a32a6aa638e1f78d056a112bc311bf08853a3dd/monitoring/grafana/dashboards/gift-payment-rabbitmq.json).

- 원본 8개 패널 유지. V3 `cluster`·`environment` 및 수집 job 필터 추가로 공용 Prometheus의 다른 환경 혼합 방지.
- 별도 UID `seonjalal-v3-payment-rabbitmq` 사용. 원본/기존 대시보드 덮어쓰기 금지. JSON 작성만으로 Grafana에 등록되지 않음.
- 데이터 소스 UID는 기존 공용 `seonjalal-prometheus` 참조. 등록 전 실제 UID/type 및 조회 권한 확인 필요. 다른 환경에서 가져오려면 모든 panel/target의 datasource UID를 실제 연결에 맞춰 변경.
- `rabbitmq`는 기본 메모리·디스크, `rabbitmq-queues`는 큐/Exchange, `rabbitmq-dlq-age`는 DLQ head timestamp 수집 job.
- 결제 큐 미생성·시계열 부재를 `0`으로 채우지 않음. `No data`는 정상 0건이 아니며 큐 존재·up·수집 필터 확인 필요. 빈 DLQ timestamp 0은 나이 계산에서 제외. 양수 timestamp의 단위는 실제 메시지가 있을 때 검증 필요.
- 원본의 Exchange rate `max by(exchange,vhost)` 유지. 노드별 rate의 최댓값이며 클러스터 발행량 합계가 아님. Confirm은 브로커 확인으로 업무 처리 성공과 구분. Return만으로 일부 바인딩 누락을 모두 감지하지 못함.
- 기존 Report/profiling 메시지·Consumer 패널 2개는 수집 연결 대조용. 실제 결제 처리·실패 복구 검증을 대신하지 않음.
- 화면 새로고침 15초, 기본 범위 최근6시간 유지. 실제 전송 지연이나 가용성 보장값 아님. 원본 설명과 위 한계를 화면의 설명 패널에 표시.
- 등록은 기존 Grafana UI Import 또는 로컬 API로 새 UID 생성. API 이용 시 관리자 자격은 서버에서 메모리로만 읽고 오류 원문·Authorization·Secret 출력 금지. 기존 UID 존재 시 중단하고 검토. Grafana/Prometheus 재시작·새 컨테이너·큐 변경 불필요.
- 복구는 이번 UID만 대상으로 검토 후 제거. 기존 대시보드/데이터 소스/시계열은 보존.

근거: [Grafana 대시보드 API](https://grafana.com/docs/grafana/latest/developers/http_api/dashboard/), [RabbitMQ 상세 지표](https://www.rabbitmq.com/docs/prometheus).
