# V3 AI runtime Chart

Search·Profiler·Chat을 같은 Pod에 배치. 최종 이미지의 Profiler launcher가 Search loopback 주소를 강제하므로 공유 네트워크 사용. 이미지 소스는 `ae5ce38ea929b3ad6087336f00d98199775c3309`, 운영 values에 ECR Digest 4개 고정.

## 실행 전 조건

- 기존 `production/ai-runtime` ServiceAccount 및 자산 읽기 IRSA 역할 필요
- `ai-search-runtime`, `ai-profiler-runtime`, `ai-chat-runtime` Secret 필요. 앱별 키만 주입하며 DB migration 자격증명 제외
- AI PostgreSQL migration `0012` 및 앱 권한 확인 필요
- `replicas: 0`, `launchApproved: false`가 준비 상태. 실기동에는 승인과 `profiler.tagEngine` 명시 필요
- Modal v3는 `profiler.modalSecretName`에 URL·API key·proxy key·proxy secret 4개 키 필요. 비밀값은 values에 작성 금지
- AI 팀 확인 기준 Modal v3 사용, 장애 시 기존 OpenRouter 모델 `google/gemini-3.5-flash-lite` 폴백 허용. 운영 values에 반영, Secret 동기화·실기동은 별도 확인

## 자산·기동 순서

Init container가 S3의 고정 Manifest와 모델 4개 SHA256·크기 검증 후 공유 볼륨 초기화. 약134.32MiB, Pod 재생성마다 다운로드. 데이터 원본은 S3이며 emptyDir은 영속 DB 저장소가 아님. `/shared/media`는 빈 디렉터리 생성만 하므로 미디어 제공 확인 필요.

Search 준비 후 Profiler·Chat 실행. 역할별 이미지 제공 readiness 명령은 모델 호출 없이 검사하며 Chat 인증도 처리. 준비되지 않은 Pod는 두 Service의 트래픽 대상에서 제외. 프로세스 종료 시 Kubernetes 재시작, 외부 DB 장애를 강제 재시작으로 확산시키는 liveness는 초기 구성에서 생략.

## 초기 자원·가용성 한계

사용자 선택에 따라 기존 AI Spot 풀, Pod 1개. Search/Profiler/Chat 메모리 requests 1280/640/896Mi는 기존 ECS 메모리 한도를 초기 기준으로 채택. CPU requests 합계1코어, 메모리 합계2816Mi. 부하테스트 최적값 아님. 제한 합계 CPU3코어·메모리5Gi는 노드 보장량이 아니며 CPU는 후보2vCPU 노드에서 경합 가능.

Pod 1개 교체·Spot 중단 시 세 서비스가 함께 일시 중단될 수 있음. MQ 재배달과 DB 전달 기록으로 복구하되 무손실·무중단 검증 완료 아님. Chat SSE 연결은 재연결 필요할 수 있음. SLOTS8 기준 추가 Consumer 중첩을 막기 위해 maxSurge0/maxUnavailable1. 복제본 증가·무중단 업데이트는 전체 슬롯 예산과 함께 별도 검토.

종료 유예120초는 MQ 작업 대기35초와 발행 확인·정리 시간을 고려한 초기 여유. Spot 종료 통보가 항상120초 확보된다는 보장 아님.

## 로컬 검증

Cloud 저장소 루트에서 실행. 실제 클러스터 변경 없음.

```bash
helm lint helm/charts/ai --values helm/environments/production/ai/runtime.yaml
helm template ai-runtime helm/charts/ai --namespace production --values helm/environments/production/ai/runtime.yaml
```

실제 Helm 적용 전 모델 설정·노드 자원·Secret 준비 및 변경 범위 검토 필요. rollback도 같은 Pod 교체 영향을 가지며 DB 전달 기록을 삭제하지 않음. ServiceAccount·ExternalSecret은 기존 준비 리소스 참조, 이 Chart에서 재생성하지 않음.
