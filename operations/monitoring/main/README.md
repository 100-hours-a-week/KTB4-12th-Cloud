# Main JVM 연결 실행 조건

Main 5개(member/catalog/gifting/recommendation/notification)만 대상. Payment Server 제외.

## 왜 필요한가

기존 관리 포트 분리 설계를 유지하면서 Kubernetes·ALB가 사용하는 앱 8080 Health 경로를 보존하기 위한 PR #616이 선행 조건. 기존 이미지에서 monitoring만 켜면 앱의 Health 경로가 사라질 수 있어 기존 Digest는 활성화 거부.

## 설정과 영향

- Backend `monitoring.enabled=false` 기본. 기존 렌더·리소스 수·이미지·실행 상태 유지.
- 새 Main Digest에 Health 호환 수정 포함 확인 후 `healthBridgeImageReviewed=true`와 enabled를 함께 지정. 검토 플래그는 이미지 검증 자체를 대신하지 않음.
- 추가 profile monitoring, 관리 8081/0.0.0.0, Health bridge 활성화. Hikari max5/min1을 명시해 프로필 기본10/10으로 확대되지 않도록 유지.
- 앱 8080 probe·Service·Ingress 경로 보존. 관리 포트는 Pod 선언만 추가하며 Service/NodePort/Ingress에 추가하지 않음.
- NetworkPolicy는 해당 Release Pod만 선택. 업무 8080은 기존처럼 출발지 제한 없이 허용, 관리8081은 같은 production의 v3-metrics-agent만 허용. Egress 정책 미변경. 기존 Main 선택 정책 없음이라는 2026-11-12 조회 기준이며 적용 직전 재확인 필요.
- 다른 허용 정책과 합산되거나 CNI enforcement가 꺼져 있으면 예상한 차단이 보장되지 않음. Agent 허용과 비Agent 차단을 실제 검증한 뒤 완료 판정.
- credentials.yaml은 기존 Secrets Manager의 MONITORING_PASSWORD 하나만 K8s password로 투영. 값 복사·조회 RBAC 확장 없음. Main env와 Agent password_file이 같은 전용 Secret 참조.
- 원본 키는 현재 32자 조건 미충족. 파일을 적용하는 것만으로 해결되지 않음. 비밀번호 값은 명령행·Git·로그에 쓰지 않음.

## 실행 순서

1. PR616의 Health 수정과 develop → main 승격 후 3.0.2 이미지 발행·정적 검증 완료. Main Index Digest는 `sha256:e4b71d4de5f2cbb3cd62b1a7a447ef09b9043316dd29648fb739687f0998c6c8`. 기존 CI는 Main/Payment 이미지를 함께 발행하지만 이번 운영 적용 대상은 Main만 유지.
2. 인증값 안전 준비, ExternalSecret Ready와 비밀번호 길이 조건 확인. Main과 Agent 활성화 전 수행.
3. 새 Digest·옵션을 Cloud PR에서 리뷰 후 main 반영. 기존 Argo 배포 소유권 유지, 수동 helm upgrade 혼용 금지.
4. Main 순차 Ready·앱 Health·상품 조회 확인. 관리8081 인증 없음401/정상인증200, 앱8080 metrics 차단 확인.
5. Agent main.enabled=true 후 기존17target 보존 및 Main 현재5Pod UP/중앙 JVM·Hikari·HTTP 지표 확인. 롤링 중 일시적으로 old/new Pod가 함께 발견될 수 있음.
6. 기존 Gift/JVM의 잘못된 datasource UID만 실제 최신 대시보드에서 seonjalal-prometheus로 교체, 팀원 패널 변경 보존.

변경 시 Main은 순차 재시작, Agent는 Recreate 수집 공백 가능. 되돌림은 Cloud Git에서 Main 옵션·Digest 복구와 Agent 옵션 비활성화. 전용 Secret 삭제를 먼저 하지 않음. 자격 변경 자체는 별도 수명·회전 작업이며 앱 Pod env와 파일 투영의 반영 시점이 다름.

근거: [Kubernetes NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/), [Prometheus 설정](https://prometheus.io/docs/prometheus/latest/configuration/configuration/).
