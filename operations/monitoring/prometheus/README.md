# 기존 공용 Prometheus 연결 준비

- 기존 job·global·데이터 볼륨·실행 옵션을 보존하고 Alertmanager 정적 대상 및 결제 경고 5개만 추가하는 변경안.
- 이 디렉터리는 기존 prometheus.yml 전체를 대체하는 템플릿이 아님. 원격 서버의 실제 설정을 읽어 병합 필요.
- `prepare-v3-prometheus.py`는 Mac에서 SSM 요청 파일만 생성. 사용자가 실행하는 원격 준비 단계도 활성 설정·컨테이너·보안 그룹 변경 없이 백업/후보/계획 생성과 오프라인 검사만 수행.
- 기존 rule_files가 이미 있으면 검증 경로를 임의로 바꾸지 않고 중단. 기존 Alertmanager 설정은 보존 후 새 정적 대상 추가.
- Docker inspect 원본은 환경변수를 포함할 수 있어 서버 root 전용 파일로 보존하며 출력·Git 저장 금지. 복구 시 원본과 config 백업 사용. 같은 TSDB 볼륨을 두 Prometheus에서 동시에 열면 안 됨.
- 향후 승인된 전환에서는 수신 옵션 `--web.enable-remote-write-receiver`와 내부 IP의 9090 게시 필요. localhost 기존 게시도 유지. EKS 노드 SG로만 TCP9090 허용하고 실제 Pod 접근 확인 필요.
- 내부 HTTP 수신은 VPC/SG 경계에 의존. 지표 전송에 별도 TLS·사용자 인증이 없으며, 이 전제를 승인하지 않으면 그대로 적용하지 않음. 인터넷 전체 개방·관리 API 활성화 금지.
- 실행 옵션 변경에는 기존 단일 Prometheus 재기동 필요. 그동안 수집·조회·규칙 평가 공백 가능. 기존 TSDB 볼륨 보존은 무중단 보장과 구분. Agent의 WAL은 연결 중단 버퍼지만 교체 중 손실 가능성은 별도.

## 결제 경고 원본·운영 차이

원본: [BE main 5a32a6aa](https://github.com/100-hours-a-week/KTB4-12th-BE/blob/5a32a6aa638e1f78d056a112bc311bf08853a3dd/monitoring/payment-rabbitmq-alerts.yml).

- DLQ 메시지 >0, DLQ 25일 초과/5분 지속, ready>0이면서 Consumer0/2분 지속, 최근5분 Return, 노드 delivery-limit 초과 유지.
- 모든 selector에 V3 운영 cluster/environment와 해당 job 추가. 집계 시 cluster/environment 보존 및 Alertmanager 라우팅용 component 라벨 유지.
- timestamp0은 빈 DLQ일 수 있어 25일 경고에서 제외. missing 시계열을0이나정상으로 채우지 않음. 실제 exporter timestamp 단위·head 존재 조건·TTL은 런타임 확인 필요.
- delivery-limit는 노드 지표이므로 결제 큐 단독 장애로 단정하지 않음. Return도 일부 바인딩 누락을 모두 감지하지 못해 대상 큐 집합 검증 별도 필요.
- 아직 결제 큐/업무 기능이 비활성일 수 있음. 이 구성은 큐·소비자·기능 플래그를 생성하거나 활성화하지 않음.
- 테스트 YAML은 30일을 가상 평가 시각으로 사용. 실제 대기·메시지 생성 없음. 빈 timestamp0 무발화/오래된 양수 timestamp 발화 확인 목적.
- 규칙은 기존 전역 평가 주기 상속. `for`·평가 주기·Alertmanager group_wait·네트워크 지연을 합친 실제 전달 시간은 별도 측정.

## 준비 도구

Mac: `python3 scripts/prepare-v3-prometheus.py --instance-id <관측 EC2 ID>`.

기존 확인 이미지 Digest·640MiB/0.75CPU·volume·loopback 게시 조건이 달라졌으면 중단. 수집 job과 global 동등성 검사 후 후보 작성, 동일 이미지 promtool로 config/rules/unit tests 검사. 실제 Prometheus 재기동은 포함하지 않음.

근거: [receiver 실행 옵션](https://prometheus.io/docs/prometheus/latest/command-line/prometheus/), [경고 규칙 테스트](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/).
