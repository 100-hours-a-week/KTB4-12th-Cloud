# 기존 Redis·Sentinel 수집

데이터 Pod 4개·Sentinel 3개에 exporter를 각각 별도 Deployment로 연결. 기존 Redis StatefulSet/Pod/Service/Secret/ACL 변경 없음. 기본 replicas=0이며 실행에는 launchReviewed=true와 확인한 image Digest 필요.

## 선택 이유와 경계

- Sidecar 추가는 기존 Redis Pod 교체를 유발하므로 제외. 단일 multi-target exporter보다 Pod7개가 늘지만 대상별 Secret1개와 egress를 분리. 비밀번호3종을 한 프로세스에 모으지 않음.
- 공식 redis_exporter v1.93.0 검토 기준. 이미지 확인 후 `image.digest`로 고정. 태그만으로 기동 금지.
- 기존 production redis-auth/product/sentinel Secret의 password 키를 env로 주입. 비밀번호를 values·args·URL·ConfigMap에 기록하지 않음. Secret 회전 후 env 갱신에는 해당 exporter 재시작 필요.
- 현재 기존 default 사용자 자격 재사용안으로, 모니터링 전용 최소권한 ACL은 아님. exporter 설정은 조회 목적이어도 자격 자체의 권한은 제한되지 않음. 별도 ACL 도입에는 Redis 설정 변경·운영 승인이 필요하므로 초기 범위에서 분리. 적용 전에 이 트레이드오프 확인 필요.
- 키 조회/SCAN 패턴·Lua 스크립트 미설정, key 값 출력 비활성. CONFIG 조회 및 Search 인덱스 조회 비활성. INFO/CLIENT/CLUSTER/SLOWLOG 등 exporter 기본 조회가 발생할 수 있어 수집 자체가 무부하라는 뜻 아님. 3초 연결 timeout, Agent는 15초 간격/5초 scrape timeout.
- 임의 target를 받는 `/scrape` endpoint도 비활성화. `/metrics`는 배포에 고정한 단일 대상만 수집.
- exporter root(`/`) HTTP probe는 프로세스 응답 확인. Redis 인증/연결 성공은 `redis_up`으로 별도 확인. exporter up=1과 redis_up=1은 다른 의미.
- 기존 Redis 접근 라벨 사용. 새 exporter만 선택하는 NetworkPolicy로 ingress는 동일namespace Agent9121, egress는 해당 Redis Pod6379 또는 Sentinel26379 및 CoreDNS53만 허용. Service/NodePort/ALB/RBAC 생성 없음. HTTP/TCP는 기존 내부 평문 경계이며 TLS 추가 구성 아님.
- 요청25m/32Mi·상한100m/64Mi씩 7개. 합계 요청175m/224Mi·상한700m/448Mi는 초기 예산이며 실사용 측정값 아님. 기존 노드 여유가 부족하면 추가 노드/비용 가능, 적용 전 스케줄링 확인 필요.
- Agent Chart redis.enabled=true는 선택적 redis/redis-sentinel 2job 추가. RabbitMQ 기존4job·권한·remote_write 보존. Agent Recreate 적용 시 짧은 수집 공백 및 emptyDir WAL 미전송 표본 손실 가능. Redis 업무 Pod는 변경하지 않음.

## 적용·확인·복구

사용자 승인 후 exporter 먼저 Helm 배포하고 /metrics의 redis_up·인증 오류 여부 확인. 그 후 기존 Agent 실행 values를 보존하고 redis.enabled=true만 추가해 렌더 비교·문법 검사 후 반영. Redis 업무 Pod UID/재시작 변화 없음, exporter7Ready, Agent Redis targets7up·중앙 redis_up7개·수집 오류 없음 확인. Sentinel별 세부 지표는 버전별 실제 표본 확인 후 대시보드 판정.

되돌림은 Agent redis.enabled=false 및 이번 exporter Release 중지/제거로 분리. Redis 자체를 rollback/restart/uninstall하지 않음. Secret·기존 NetworkPolicy 삭제 금지. 모니터링 손실과 업무 장애 구분.

근거: [공식 v1.93.0 옵션·인증·Sentinel 지원](https://github.com/oliver006/redis_exporter/blob/v1.93.0/README.md), [공식 이미지 실행 UID59000](https://github.com/oliver006/redis_exporter/blob/v1.93.0/Dockerfile).
