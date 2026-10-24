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

## 네트워크 모드 (`network_mode`)

- 기본값 `awsvpc`(기존 동작). 운영은 이 값을 유지한다.
- `bridge`: Task가 호스트 네트워크를 공유해 **Task별 ENI 한도(`t3.large`는 호스트당 2개)가 사라지고**, 롤링 배포의 추가 Task는 CPU·메모리만 있으면 배치된다. 호스트 포트는 동적(`hostPort=0`)이다.
- `bridge`에서 바뀌는 것
  - Task 보안 그룹과 Task 기준 규칙을 만들지 않고, 호스트(ECS 컨테이너 인스턴스) 보안 그룹 `container_instance_security_group_id`(필수, 같은 VPC) 기준 규칙을 만든다.
  - ALB ↔ 호스트 동적 포트 범위 `32768-65535`, 호스트 자기 참조 동일 범위(Service Connect, 인바운드·송신 양쪽), DB는 호스트 보안 그룹 허용(DB 인바운드)과 호스트 송신 3306
  - 호스트 송신 규칙을 모듈이 만드는 이유: 운영 호스트 보안 그룹은 443 송신만 허용하고 있어 bridge Task의 DB 연결과 호스트 간 Service Connect가 막힌다(스테이징은 전체 송신 허용이라 드러나지 않았음)
  - 대상 그룹 `target_type = instance`, ECS Service에서 `network_configuration` 제거
- **보안 격리 약화**: awsvpc에서는 DB가 Main·Payment Task 그룹만 허용했지만, bridge에서는 호스트의 모든 Task가 같은 접근 범위를 가진다. 같은 호스트에 다른 서비스(예: AI)를 올릴 때 DB 접근이 함께 열리는 점을 고려할 것
- 호스트 보안 그룹이 Secrets 엔드포인트 443과 외부 송신에 접근 가능한 규칙은 호스트를 소유한 Root가 이미 가지고 있어야 한다(이 모듈은 만들지 않음, 중복 규칙 오류 방지)
- **전환 주의**: awsvpc → bridge는 `target_type`과 `network_configuration` 변경이라 대상 그룹·Service가 교체될 수 있다. Service가 `task_definition` 변경을 무시(CD 소유)하므로 기존 Service를 그대로 두고는 새 Task Definition이 반영되지 않는다. Task가 0개인 환경에서 먼저 시험하고, 운영은 별도의 무중단 전환 계획이 필요하다.
- Service Connect는 bridge를 지원하나 `hostname` 컨테이너 설정과 TLS는 지원하지 않는다(AWS 문서). 현재 구성은 둘 다 사용하지 않는다.

## 서비스 교체 시 주의

- awsvpc ↔ bridge처럼 `target_type`·`network_configuration`이 바뀌면 Service는 `-replace`로 교체해야 한다. 이때 이전 Service가 `DRAINING`인 동안 같은 이름으로 만들면 "Create service is not idempotent" 오류가 난다. 이전 Service가 `INACTIVE`가 된 뒤 다시 Plan·Apply한다.
- 교체 Plan에서 Service가 in-place로만 보이면 새 Task Definition이 반영되지 않는다. 반드시 교체(`-replace`)로 계획되었는지 확인한다.
- 이 방식은 교체 동안 서비스가 중단된다. 무중단이 필요하면 아래 블루-그린을 쓴다.

## 블루-그린 슬롯 (무중단 전환)

같은 Root에서 모듈을 두 번 호출해 새 슬롯을 기존 슬롯 옆에 띄우고, ALB 가중치로 트래픽을 옮긴다. 기본값에서는 기존 단일 슬롯과 동일하게 동작한다.

| 설정(`config`) | 용도 |
|---|---|
| `name_suffix` (예 `"-b"`) | 서비스·Task 패밀리·Service Connect 이름에 접미사. `shared_resources`와 함께만 사용 |
| `shared_resources` | 기존 슬롯의 실행 Role·Task Role·Secret ARN 재사용(새로 만들지 않음) |
| `parking_rule_priority` | 예약 Rule 우선순위. 기존 8, 새 슬롯은 겹치지 않는 값(예 7) |
| `manage_host_rules` | 호스트 보안 그룹 규칙 소유 여부. 두 슬롯이 모두 bridge면 한쪽만 true |
| `green_target_group` | **기존 슬롯**의 전환 Rule에 새 슬롯 대상 그룹과 가중치(0~100) 지정 |

- 새 슬롯의 Main은 `payment-server-b`(접미사 이름)로 같은 슬롯의 Payment만 호출한다. awsvpc와 bridge 사이의 Service Connect 혼합 통신에 의존하지 않는다.
- 전환 Rule(우선순위 9)은 기존 슬롯 하나만 소유한다. 새 슬롯은 `traffic_cutover`나 `green_target_group`을 쓸 수 없다(가드가 거부).
- Root에서는 두 슬롯을 독립 입력(`deployment`, `deployment_green`)으로 받는다. 모듈 간 참조는 의존성 순환을 만들기 때문에 기존 슬롯 ARN과 새 대상 그룹 ARN은 tfvars로 전달한다.
- 절차(각 단계가 별도 Plan·승인)
  1. 새 슬롯 생성(`desired_count = 0`) → 대상 그룹을 리스너에 연결(예약 Rule)
  2. 새 슬롯 기동(Task·헬스 확인), 호스트 용량 확인
  3. 기존 슬롯 전환 Rule에 `green_target_group` 가중치 0 지정 → 10 → 50 → 100
  4. 문제 시 가중치를 되돌린다(롤백)
  5. 안정화 후 기존 슬롯 입력을 제거해 정리
- 한계: 새 슬롯을 기존 슬롯의 서비스 이름으로 되돌리는 이름 정리는 하지 않는다. CD가 어느 슬롯이 현재 서비스 중인지 알아야 한다.
