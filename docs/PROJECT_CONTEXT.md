# 프로젝트 컨텍스트

## 서비스

- 서비스명: 선잘알
- 목적: 사용자의 비선호 카테고리와 선물 이력을 반영해 선물 선택의 불확실성을 줄인다.
- 주요 기능:
  - 비선호 카테고리 반영
  - 받은 선물 이력 조회
  - 상황 맞춤 추천
- Cloud 담당자: 1명

## 현재 저장소에서 확인한 V1 구성

| 영역 | 구성 |
|---|---|
| 실행 방식 | Docker Compose |
| 진입점·정적 파일 제공 | Nginx 기반 Frontend 컨테이너 |
| Backend | Backend 컨테이너, Actuator health check 사용 |
| Database | MySQL 8.4 컨테이너, Docker named volume 사용 |
| 이미지 저장소 | Docker Hub 이미지 사용 |
| 네트워크 | 단일 Docker bridge network |
| 운영 오버레이 | 모든 서비스에 `restart: unless-stopped` 적용 |

## V1 환경 구분

| 환경 | 목적 | 배포 기준 | 현재 상태 |
|---|---|---|---|
| Local | 개인 개발·단위/통합 검증 | 각 개발자 작업 브랜치 | 각 저장소별 로컬 실행 |
| Develop | 팀 통합과 빠른 기능 확인 | `develop` Candidate Image | V2 전용 EC2·Tailscale HTTPS·FE/BE Merge 기반 자동 배포 검증 완료 |
| V1 Benchmark | V1·V2 동일 Image 비교와 부하 시험 | 고정 Image Digest·Fixture | `v1-benchmark.seonjalal.com` HTTPS 검증 완료 |
| Production | 실제 사용자 서비스 | Develop에서 검증한 Release Image | 기존 EC2와 수동 승인 CD 유지 |

- 기존 V1 비운영 EC2는 지속 배포용 Develop이 아니라 비교 시험용 V1 Benchmark로 분리했다.
- 신규 V2 Develop은 Tailscale로 승인된 장치만 접근하며 평상시 `develop` 변경을 빠르게 통합한다.
- V1 Benchmark와 향후 V2 Staging의 정식 시험 Window에는 배포를 동결하고 FE·BE Image Digest를 고정한다.
- Production 배포는 Develop 자동 배포와 분리하며, 기존 수동 승인 절차를 유지한다.

## AI 서비스 연동의 현재 상태

- Backend `1.1.0`은 시작 시 `AI_PROFILE_BASE_URL`과 `PROFILING_SERVICE_TOKEN`을 요구한다.
- AI Repository의 운영 이미지 CI와 AI Container 배포는 아직 구성되지 않았다.
- AI 서비스가 배포되기 전까지 Backend는 `SCHEDULING_ENABLED=false`로 실행한다.
- `AI_PROFILE_BASE_URL=http://ai:8000`은 향후 동일 Compose Network의 AI Service Name을 기준으로 한 주소다.
- AI 비활성 상태에서는 일반 FE·BE 기능을 우선 운영하되 AI 프로파일링과 추천 결과 갱신은 제공하지 않는다.
- 운영용 `PROFILING_SERVICE_TOKEN`은 저장소가 아니라 운영 서버 `.env`에서 관리한다.

## 제공된 V1 트래픽 가정

- 초기 접근 가능 인원: 약 250명
- DAU: 35명
- 예상 요청량: 약 630 requests/day
- 캐시: 미적용
- Auto Scaling: 미적용

## 제공된 V2 성장 가정

- MAU: 100,000
- 평상시 Peak: 약 30 RPS
- 이벤트 순간: 최대 600 RPS
- 선물 이벤트에 요청이 집중되는 스파이크성 트래픽

위 수치는 요구사항 검증이나 부하 테스트로 확인된 결과가 아닌 설계 입력값이다.

## 설계 제약

- 초기 비용을 중요하게 고려한다.
- Cloud 담당자 1명이 운영할 수 있어야 한다.
- 초기에는 단일 AZ를 허용한다.
- V1 Production Release는 기존 Docker Hub 경로를 유지하고, V2 Develop Candidate는 ECR Private
  Repository를 사용한다.
- 최종 구조와 용량은 부하 테스트 결과를 바탕으로 결정한다.
- 실제 AWS 리소스 작업은 사용자의 명시적 승인 후 수행한다.
