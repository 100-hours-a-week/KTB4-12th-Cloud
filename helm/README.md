# V3 Backend Helm 실행 안내

공통 Chart 하나를 Main 기반 5개 서비스와 Payment에서 재사용하는 구성.
앱마다 별도 Helm Release/Argo CD Application으로 렌더링하는 용도.
이 디렉터리 작성·렌더링만으로 클러스터 배포가 발생하지 않음.

## 파일 구조

- `charts/backend`: Deployment·Service·ConfigMap 2개·PDB 공통 템플릿
- `charts/backend/files/start.sh`: 기본 Java CA에 RDS CA를 병합하고 JVM 실행
- `charts/backend/files/rds-ca.pem`: 기존 V2에서 사용한 공개 RDS CA 3개. 개인키 없음
- `environments/production/backend/common.yaml`: 공통 DB·기동·노드 설정
- `environments/production/backend/main.yaml`: Main 이미지·제품 연결·Secret 참조
- `environments/production/backend/*-service.yaml`: Main 도메인별 서비스 이름
- `environments/production/backend/payment-server.yaml`: Payment 이미지·포트·Secret 참조

## 로컬 검토

실행 위치: Mac의 Cloud V3 worktree 루트. 아래 명령은 AWS 변경 없는 로컬 검사.

```bash
# member-service 설정의 형식·템플릿 검사
helm lint helm/charts/backend --strict --namespace production \
  --values helm/environments/production/backend/common.yaml \
  --values helm/environments/production/backend/main.yaml \
  --values helm/environments/production/backend/member-service.yaml

# 실제 적용 없이 Kubernetes 리소스 출력
helm template member-service helm/charts/backend --namespace production \
  --values helm/environments/production/backend/common.yaml \
  --values helm/environments/production/backend/main.yaml \
  --values helm/environments/production/backend/member-service.yaml
```

다른 Main 서비스는 마지막 values와 Release 이름을 동일 서비스 이름으로 변경.
Payment는 common.yaml과 payment-server.yaml만 사용하고 main.yaml은 사용하지 않음.

## 준비 상태와 실행 조건

- 현재 `replicas: 0`. 기본 운영 목표는 서비스별 2개이며 현재 실행 중이라는 의미가 아님
- Main·Payment 3.0.1의 사용자 확인 ECR Image Index Digest로 고정. Main은 Sentinel 코드·STS 빌드 확인 기준으로 이미지 조건 반영, 실제 EKS 연결 검증은 별도
- `replicas > 0`은 `database.schemaReviewed: true` 필요
- Main은 추가로 `image.mainRuntimeCompatible: true` 필요
- 위 두 값은 확인 결과를 기록하는 수동 선언. 코드·DB·실제 접속을 자동 검증하는 기능이 아님
- 공유 운영 DB에 V3.0.1 필수 Migration 16개 적용 및 신규 13테이블 앱 DML 권한 확인 후 common.yaml의 schemaReviewed: true 반영
- V45·V46은 V2가 사용하는 SSE 구조 변경·삭제이므로 V2 종료 후 별도 적용. 현재 최고 이력 V54가 모든 중간 버전 적용을 의미하지 않음
- 앱의 Flyway는 비활성 유지. schemaReviewed는 운영 검토 기록이며 데이터 정합성·무중단이나 향후 스키마 변경의 자동 승인이 아님
- SecretStore·ExternalSecret·ServiceAccount·IRSA는 기존 준비 리소스 참조. 이 Chart에서 재생성하지 않음
- S3 인증은 Java STS 모듈·ServiceAccount·IAM·STS 네트워크 연결까지 갖춰야 완료
- AI 주소는 향후 생성할 `ai-chat`·`ai-profiler` Service 이름. 현재 AI 배포 완료가 아님
- SMTP·Sheets의 비밀 아닌 설정, 스케줄러·신규 MQ 소비자 활성화, 검색 초기 적재는 별도 전환 작업
- Ingress/ALB·HPA·Argo CD Application·앱 NetworkPolicy·AI 배포는 이번 Chart 범위 밖. 출시 전에 별도 준비

## 초기 자원과 DB 연결 가정

- Main requests 500m/1536Mi, Payment 500m/1Gi: 기존 ECS의 CPU·메모리 할당을 초기 기준으로 사용
- limits Main 1CPU/2Gi, Payment 1CPU/1536Mi: requests와 별도 실행 상한이며 측정된 최적값이 아님
- CPU requests 합계: 기본 12 Pod × 0.5CPU = 6CPU. Redis·RabbitMQ·시스템 자원은 별도 합산
- 앱 Hikari max 5/minIdle 1: 기본 12 Pod의 최대 연결 수 60개로 제한하는 초기 예산
- V2·AI·관리 연결, Rolling 중 추가 Pod는 60개에 포함되지 않으므로 실제 DB max_connections 확인 필요
- 연결 풀이 부족하면 요청 대기가 증가할 수 있음. 처리량 보장값이 아니며 지표·부하테스트로 조정
- 3AZ spread와 노드 anti-affinity 적용. Pod의 운영 배치 조건이며 AZ 장애 시 처리량 보장 검증과 구분

## 연결·수명주기

- Main NodePort: 설계의 ALB instance target용. 내부 Pod도 같은 Service의 ClusterIP/DNS로 접근 가능
- Payment ClusterIP: Main에서 호출하는 내부 API용, 별도 외부 노출 없음
- `seonjalal.io/redis-access=true`: 기존 Redis NetworkPolicy의 명시적 허용 라벨
- ES CA는 Secret의 공개 CA 키 하나만 `/app/certs/es-http-ca.crt`로 마운트
- CA와 자격증명은 별도 경로이며 ES CA를 RDS의 Java 전역 truststore로 대체하지 않음
- RDS truststore는 Pod의 `/tmp`에 생성. Pod 종료 시 삭제, RDS 원본·앱 이미지 변경 없음
- Startup 최대 약 5분, readiness는 요청 수용 상태, liveness는 프로세스 생존 상태 확인
- Spring graceful 종료45초·preStop10초·종료 유예75초. 실제 요청 무중단 보장은 별도 확인 필요
- ConfigMap 변경 시 checksum으로 Pod template 갱신. Secret 값 변경은 환경변수를 자동 갱신하지 않으므로 명시적 Pod 교체 필요
- CA Secret volume 갱신 후에도 클라이언트가 재로딩한다고 가정하지 않고 인증서 교체 시 Pod 교체 필요
- Argo CD 도입 후 같은 앱에 수동 Helm 배포와 Argo CD Sync를 병행하지 않음

## 근거

- [Kubernetes Secret 환경변수](https://kubernetes.io/docs/tasks/inject-data-application/distribute-credentials-secure/)
- [Kubernetes Probe](https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/)
- [Kubernetes topology spread](https://kubernetes.io/docs/concepts/scheduling-eviction/topology-spread-constraints/)
- [Java SDK 인증 체인](https://docs.aws.amazon.com/sdk-for-java/latest/developer-guide/credentials-chain.html)
