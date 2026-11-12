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

## 전환·복구 도구

`prepare-v3-prometheus-transition.py`는 검사 완료 디렉터리·후보 SHA256·대상 EC2를 지정해 SSM 요청 생성. 기본 `--mode check`는 Docker 조회와 root 전용 후보 생성 요청만 준비하며 기존 프로세스·활성 설정은 변경하지 않음.

- `check`: 기존 컨테이너 ID/Config/HostConfig/Mounts·활성 파일·후보/규칙 해시를 대조. 기준이 바뀌면 중단.
- `apply`: 해당 변경 범위 승인 후 요청 생성에 `--approved` 필요. 기존 컨테이너 정지→이름 변경·보존→검증된 설정 교체→동일 TSDB 볼륨으로 새 컨테이너 생성·시작 순서. 동시 TSDB writer 금지.
- 기존 Env·Entrypoint·명시적 실행 옵션·HostConfig·prometheus alias 보존. 일회성 컨테이너 ID hostname/alias는 새 컨테이너로 갱신. receiver 옵션·private IP 게시·규칙 RO 마운트·추적 라벨만 추가.
- 준비 상태는 0.5초 간격으로 최대90초 확인. 성공 후 실행 전후 정규화 설정·receiver flag·규칙5개·같은 고정 시각의 `up` 표본·볼륨·자원·포트 확인.
- 실패 시 새 컨테이너 중지·별도 이름으로 보존→원본 설정 복원→원래 컨테이너 이름/실행 복구. 다른 변경의 컨테이너는 중지하지 않음. 컨테이너·볼륨 삭제 없음.
- 프로세스 강제 종료·SSM timeout·호스트 장애로 자동복구 코드 자체가 실행되지 않을 수 있음. 수동 `--mode rollback --approved` 요청 경로 별도 제공. 원본 컨테이너·백업 디렉터리는 안정화 전 삭제 금지.
- 전환 시각·단계·단조시계 경과는 서버 root 전용 `transition-apply.json`에 기록. `stopRequestToReadySeconds`는 정지 요청부터 준비 상태 첫 관측까지의 창이며 실제 요청 실패 구간의 정밀 측정값 아님. `stopRequestToVerificationSeconds`는 후속 검사까지 포함. SSM 전체 실행시간·업무 API 중단시간과 구분.
- 과거 `up` 표본 비교는 특정 시각의 일부 시계열 보존 확인이며 TSDB 전체 이력의 완전성 검증 아님. 자동복구 로컬 테스트는 모의 Docker이며 실제 호스트 장애 복구 보장 아님.
- 내부9090을 게시하면 remote_write뿐 아니라 Prometheus 조회 API도 EKS 노드 SG 범위에서 접근 가능. HTTP·무인증 초기 경로의 범위·트레이드오프를 실제 적용 전 확인. 사용자 승인 없이 SG 변경·실제 전환 실행 금지.
