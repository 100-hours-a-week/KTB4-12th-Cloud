# BE Main/Payment ECS 공통 모듈

이 모듈은 기존 V2 ECS Cluster, Capacity Provider, 내부 ALB, MySQL RDS, Private Subnet을 입력으로 받아 Main Backend와 Payment Server를 별도 Task Definition/Service로 구성한다. 새 EC2/ASG/RDS는 만들지 않는다. `desired_count=0`은 Task Definition과 Service만 보존하는 휴면 상태다.

- Main `8080`만 기존 내부 ALB의 별도 IP Target Group에 연결한다. 운영에서는 ECS Service 생성 전에 우선순위 8의 `.invalid` Host·전용 Path 대기 규칙으로 Target Group을 Listener에 연결한다. 이 규칙은 정상 운영 Host 요청과 일치하지 않으며 기존 Listener 기본 Target을 유지한다. `traffic_cutover=true`는 Task2개 이상·사전 Health 확인 뒤 별도 Plan에서 우선순위 9의 실제 Host Rule을 생성한다.
- 스테이징은 HTTPS 443, 운영은 CloudFront VPC Origin의 HTTPS 8443 Listener를 검증한다. 운영 Host는 `v2-origin.seonjalal.com`만 허용하고, Cutover는 기존 우선순위 10 규칙 앞의 우선순위 9로 제한한다.
- Payment `8090`은 ALB에 공개하지 않는다. Main과 Payment가 같은 AI Root의 Service Connect namespace를 사용하며 Main은 `http://payment-server:8090`으로 호출한다. AI Chat/Profiler 주소도 같은 namespace의 별도 Service를 참조한다.
- 두 Task 모두 EC2/awsvpc/linux/amd64, Private Subnet, Digest 고정 이미지를 사용한다. 활성화 시 각 Service 최소2개, 정상 유지100%/추가 용량200%, Circuit Breaker를 사용한다.
- 실행 Secret 값은 Terraform에 넣지 않는다. Secret metadata만 생성하고 Execution Role은 이 Secret만 조회한다. Main Task Role에만 지정 상품 Prefix의 S3 읽기를 준다.
- `DB_URL`은 두 컨테이너 모두 확인한 RDS hostname/DB와 JDBC `sslMode=VERIFY_IDENTITY` 한 개를 사용한다. 공개 RDS CA를 JVM Truststore에 추가한 뒤 앱을 실행한다. DB Migration은 이 모듈에서 실행하지 않는다.
- Main의 일반 스케줄러와 AI Profiling 스케줄러는 기본 비활성이다. 운영 업무 계약을 확인한 후 각각 `scheduling_enabled`, `ai_profile_scheduling_enabled`로 켠다.
- Service의 `task_definition`은 Cloud CD가 소유한다. Terraform 템플릿 변경을 배포할 때는 출력된 새 `task_template_arns`에서 Revision을 만들어야 한다. 기존 Revision 복제만 하면 설정 변경이 빠질 수 있다.

현재 Cloud V2 CD는 `seonjalal/production/backend`에서 Main/Payment 불변 태그를 찾아 고정 Digest로 배포한다. 스테이징도 해당 ECR 저장소의 검증된 동일 이미지를 사용하며, 스테이징 ECR로 분리하려면 CD의 이미지 조회 계약을 먼저 변경해야 한다. 동일 `source_sha`와 Main/Payment 각 Digest를 릴리스 영수증에 기록한다.

활성화 전에 별도 확인: 실제 BE main Merge 이미지, MySQL 최신 스키마와 역할, Secret JSON 값, Product S3 Prefix/정책, AI namespace/Service 상태, ALB Listener Rule 우선순위, 두 AZ EC2 여유와 배포 중 최대8개 Task, Payment/AI/상품 API 호환성, 내부 DNS 및 Health. 초기 Task 메모리 Main1536MiB/Payment1024MiB는 추정치라 실측 전 확정값이 아니다.

## Flyway 자동 실행 (`flyway_enabled`)

- 기본값 `false`. 운영은 기존처럼 Flyway를 끄고 별도 절차로 마이그레이션
- `true`이면 **Main에서만** `SPRING_FLYWAY_ENABLED=true`. Payment는 마이그레이션 파일이 없어 항상 `false`
- Flyway 전용 계정 필수: `SPRING_FLYWAY_USER`(환경변수, 앱 `DB_USERNAME`과 달라야 함)와 `SPRING_FLYWAY_PASSWORD`(Secret JSON 키). 앱 런타임 계정에 DDL 권한을 주지 않기 위함
- 시작 유예 확대: Main 컨테이너 `startPeriod` 90→300초(ECS 허용 최대), Main 서비스 `health_check_grace_period` 180→600초. 마이그레이션 중 ECS가 Task를 종료하지 않게 함. 300초 이상 걸리는 마이그레이션은 여전히 중단될 수 있음
- 위험: MySQL DDL은 트랜잭션이 아니라 중간 중단 시 `flyway_schema_history`에 실패 이력과 부분 적용이 남아 수동 복구가 필요. 첫 실행 전 DB 스냅샷 확보 권장
- `activation_checks.database_migrated`는 이 모드에서 "스냅샷 확보와 Fixture 적재를 확인했고 시작 시 자동 마이그레이션이 수행됨을 인지함"의 의미로 사용
