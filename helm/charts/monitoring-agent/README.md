# V3 수집 전용 Agent

EKS RabbitMQ를 수집해 기존 공용 Prometheus로 remote_write 전송. 기존 서버가 저장·Grafana·규칙 평가를 담당하며 독립 관측 스택을 중복 설치하지 않음.

## 범위·이유

- 주소가 바뀌는 Pod를 namespace 내부 discovery로 추적.
- RabbitMQ prometheus 이름 포트만 선택. Secret·큐·Binding·소비자·업무 플래그 변경 제외.
- Role은 Pod get/list/watch만 허용. ClusterRole·Secret 읽기·exec/proxy·쓰기 권한 없음.
- 기본 /metrics 유지. 상세 지표는 gift 지정 큐, queue_metrics는 결제 DLQ5개 한정.
- 대상 큐가 아직 없으면 지표도 없을 수 있으며 이 Chart가 토폴로지를 생성하지 않음.
- Main은 관리 포트 분리와 기존 probe/ALB/공개 health의 호환성 검토가 별도 필요해 아직 포함하지 않음.

## 실행 경계

기본 replicas=0. 실행에는 receiverReviewed=true·이미지 Digest·실제 내부 URL 필요. 이 값은 검토 선언이며 실제 네트워크를 자동 검증하는 기능 아님.

중앙 remote-write receiver·private listener·출발지 SG 제한·실행 옵션/볼륨/retention 보존 계획을 검토하고 승인 후 적용. 인터넷 전체 공개 금지. HTTP 내부 수신은 VPC 네트워크 신뢰에 의존하므로 인증/TLS가 필요한 정책이면 이 초기 경로를 그대로 실행하지 않고 재설계.

현재 NetworkPolicy·API·시스템 노드 예약 여유·브로커별 지표/queue 필터 지원·시계열량 검증 필요. 실제 이미지 pull·클러스터/호스트 변경은 별도 승인 경계.

## 수명·트레이드오프

- 단일 Agent·Recreate로 중복 수집/전송 방지, 교체 중 수집 공백 가능.
- emptyDir WAL은 전송 버퍼이며 장기 보관소 아님. Pod 재생성·2시간 상한·디스크 한도로 미전송 표본 손실 가능.
- 100m/256Mi는 초기 requests, 15초 수집·5초 timeout·target당 sample_limit 15000은 초기 제한. 실측 최적값 아님.
- Agent의 targets와 중앙 최신 표본을 따로 확인. 중앙 targets에 remote-write 원본 target이 직접 표시되는 것은 아님.
- Service·ALB·PVC·Grafana·Alertmanager·경고 규칙은 이 Chart에서 추가하지 않음. 후속 경고 발생/복구 수신은 미완료.

## 공식 근거

- https://prometheus.io/docs/prometheus/latest/prometheus_agent/
- https://prometheus.io/docs/prometheus/latest/configuration/configuration/
- https://www.rabbitmq.com/docs/prometheus
