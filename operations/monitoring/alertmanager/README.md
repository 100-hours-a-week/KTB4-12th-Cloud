# 공용 관측 서버의 BE Discord 전달 준비

- 기존 Prometheus/Grafana 보존. 별도 Alertmanager 한 개 추가 준비이며 자동 기동 없음.
- `alertmanager.yml`은 URL 원문 대신 `/run/secrets/discord-webhook-url` 파일 참조.
- 실제 파일은 Secrets Manager에서 관측 서버에 별도 주입. 코드·SSM 요청에 비밀값 포함 금지.
- `cluster=seonjalal-v3`, `environment=production`, `component=payment-rabbitmq` 세 라벨 동시 일치 시 BE 웹후크 전달. Prometheus 규칙에도 이 라벨 계약 반영 필요.
- 다른 경고는 수신하더라도 `unmatched` 수신기로 종료. 전체 서비스 경고 연결 완료 의미 아님.
- 운영 경고: 첫 그룹 대기 30초, 그룹 변경 확인 5분, 변화 없는 지속 경고 4시간 반복. 경고 규칙의 `for` 시간·Prometheus 평가 주기·전송 지연은 별도 합산.
- 복구 전달 `send_resolved: true`. 실제 Discord 발생·복구·반복 수신은 별도 실검증 필요.
- 예약 이름 `V3MonitoringDeliveryCheck`만 5초/30초/1분 설정. 실제 큐·업무 메시지 없이 전달 경로 검증 목적. 발신 시 명확한 시험 안내·3분 이내 종료 시각 필요. 설정만으로 시험 메시지가 생성되지는 않음.
- Alertmanager는 silences·notification log 영속 볼륨 필요. 기동 시 loopback9093과 기존 Docker network만 사용, 외부 전체 포트 게시 금지. 단일 인스턴스이므로 장애 시 전달 지연 가능.
- 파일 바인드 마운트는 Secret 회전 후 이전 inode를 유지할 수 있음. 파일 교체 후 컨테이너 재생성·발생/복구 재검증 필요. root 전용 상위 디렉터리 전체를 nobody에 공개하지 않고 단일 파일 RO 마운트.
- 현재 운영 파일은 재부팅 후에도 디스크에 유지되지만 재생성·회전 자동화는 후속. 파일 존재만으로 알림 동작 보장 불가.

## 파일 준비·검사

Mac에서 `python3 scripts/prepare-v3-alertmanager.py --instance-id <관측 EC2 ID>` 실행 시 `/private/tmp/seonjalal-v3-monitoring/prepare-alertmanager.json` 생성. 이 스크립트는 AWS 호출 없음.

생성된 SSM 요청은 지정된 공용 관측 EC2 한 대에서만 파일 준비·검사 수행. 기존 파일과 내용이 다르면 덮어쓰기 없이 중단. 동일 내용이면 검사 재실행 가능.

이미지 Digest 고정, UID/GID 65534, 설정/Secret 개별 RO 마운트, network none으로 `amtool check-config` 실행. 실제 webhook 전송·기동·Prometheus 변경 없음. Docker 검사 컨테이너만 생성 후 삭제.

검사 실패 시 기존 config·Secret·데이터를 삭제하지 않고 원인 확인. 비밀값 노출 가능성이 있는 검증 오류 원문은 출력하지 않고 종료 코드만 기록.

근거: [라우팅·반복·Discord 파일 설정](https://prometheus.io/docs/alerting/latest/configuration/), [Alertmanager 0.32.0](https://github.com/prometheus/alertmanager/releases/tag/v0.32.0).
