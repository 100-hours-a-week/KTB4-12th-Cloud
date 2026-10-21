# AI ECS 실행 모듈

## 실행 단위와 입력 경계

- app Task: assets-init 성공 → Search Healthy → Chat/Profiler 기동. awsvpc localhost 통신 유지
- 일회성 migrate Task: CA 준비 후 `integration.migrate_single_server --database ai_db_<environment> --emmet`
- 일회성 catalog Task: 운영은 `catalog_bundle_s3`의 검증된 tar를 `catalog_bundle.py --apply`로 가져와 임베딩 재생성 생략. 스테이징에서 이 값이 없으면 기존 `integration.search_sync` HTTPS Export/Categories 경로 유지
- Console·합성 Backend·smoke provider·PostgreSQL 컨테이너 미포함
- 서울 A Private Subnet만 허용. 기존 Cluster·Capacity Provider를 입력받으며 EC2·ASG를 생성/확장하지 않음
- AI 단일 Task의 중단 허용 배포: minHealthy0/max100. 겹치는 두 세대의 유료 호출·상태 갱신을 피하는 선택이며 무중단 보장 없음
- Circuit Breaker 복귀는 이전 완료 배포가 있어야 가능. 최초 기동 실패 시 정상 이전 Revision이 없으므로 사용자 확인 필요
- App Task의 CPU2048은 유지. 스테이징 메모리4096MiB 계약은 유지하고, 운영은 등록된 t3.medium의 ECS 잔여 MEMORY 실조회 3327MiB(2026-10-21)를 기준으로 Task 3200MiB로 제한. 운영 컨테이너 Hard Limit 합계3008MiB로 Service Connect Proxy에 192MiB, Host 배치에 127MiB를 남김
- 운영 메모리 조정은 배치 조건의 정적 계산일 뿐 실제 안정성 검증이 아님. Service desired0에서 등록된 Host의 가용 CPU·메모리와 Task 예약값 일치를 확인한 뒤 `capacity_verified`를 승인. 최초 Service 기동 직후 Service Connect Proxy 포함 앱 RSS/Peak·OOM·Health를 측정하고, 실패하면 Service를 desired0으로 되돌린 뒤 Host 증설 또는 실행 단위 분리를 검토. 한도를 추가로 낮추며 OOM을 우회하지 않음
- 기본 Root의 미생성 상태와 Service desired_count=0의 휴면 상태는 다름. 배포 후 config 제거로 정리하지 말고 desired_count=0으로 축소

## Terraform과 CD 소유권

- Terraform: IAM·SG·로그·Secret Metadata·Namespace·Task Definition 템플릿·Service 설정/수량
- Cloud CD: Service에 적용할 이미지 Revision. Service의 task_definition은 ignore_changes로 관리
- Task Definition은 skip_destroy=true로 이전 Revision 보존. 설정 변경으로 새 템플릿이 생성되면 CD는 output task_template_arn에 새 이미지를 반영해야 하며, 실행 중 Revision만 복제해 설정 변경을 잃지 않도록 주의
- 이번 변경에는 AI Cloud Delivery Workflow 및 앱/Job 실행 명령 미포함
- BE Main/Payment가 같은 Namespace를 참조하도록 후속 구성 필요. 외부 Chat/Profiler 호출은 Backend SG만 허용하며 Search는 외부 인바운드 미노출
- Export Job은 Service Connect 대상이 아니므로 검증된 HTTPS ALB 주소 사용. 이 모듈은 입력된 Backend/ALB SG에 AI 전용 규칙만 추가하며 기존 규칙 전체를 인수하지 않음
- 운영 Backend ALB Listener는 8443이므로 운영 AI Task→ALB SG 송신과 ALB SG←AI Task 수신을 8443으로 한정. 스테이징의 기존 HTTPS 진입점 443은 유지. 운영 `backend_base_url`·Export/Categories URL은 실제 도달 가능한 Origin·포트로 검증해야 하며, Main Service가 게시하지 않은 `main-backend` Service Connect 별칭을 운영에서 가정하지 않음

## Secret JSON 계약

| Secret suffix | 필요한 JSON key |
| --- | --- |
| runtime | AI_INTERNAL_SERVICE_TOKEN, BE_SERVICE_TOKEN, SEARCH_CATALOG_DATABASE_URL, PROFILING_DATABASE_URL, DATABASE_URL, OPENROUTER_API_KEY_PROFILER, OPENROUTER_API_KEY_CHAT, SESSION_SECRET, QA_ADMIN_PASSWORD, DEMO_ACCESS_TOKEN |
| migration | MIGRATION_DATABASE_URL |
| catalog | CATALOG_SYNC_DATABASE_URL, BACKEND_SERVICE_TOKEN |

- Secret 이름은 `seonjalal/<environment>/ai/<suffix>`. 값은 Terraform 밖에서 안전하게 기록
- runtime BE_SERVICE_TOKEN은 BE에서 AI를 호출하고 callback을 검증하는 값과 계약 일치 필요. AI_INTERNAL_SERVICE_TOKEN은 AI 내부 통신용으로 분리
- 관리자 DB Secret을 앱에 주입하지 않음. DSN 사용자는 각각 ai_chat_service/ai_profiler_service/ai_search_service/ai_migrator/ai_catalog_sync
- launch.py는 Host·환경별 DB명·역할·5432 및 sslmode=verify-full·공개 CA 경로 검사 후 실행. DSN 원문 출력 없음
- Secret 변경은 기존 Task에 자동 반영되지 않으므로 새 Task 배포 필요
- AI_ENVIRONMENT=staging 허용은 현재 AI 코드의 선행 보완 과제. production은 현재 허용값이나 해당 배포 Image에서 재확인 필요
- provider-key-limits 사용. 승인된 모델·키 제한과 비용 책임 확인 후 provider_budget_approved 활성화. STORE_TOKEN/Guarded Gateway를 준비한 것으로 해석 금지
- 현재 자산 정책은 S3 GetObject와 지정 Prefix만 허용. 사용자 관리 KMS 자산이나 Secret은 정확한 Decrypt 권한 검토 후 별도 지원 필요

## 자산 manifest 계약

```json
{"files":[
  {"path":"assets/models/<model.lock.json의 path>","key":"<승인된 Prefix>/<S3 object>","bytes":123,"sha256":"<파일의 64자리 SHA256>"},
  {"path":"media/<실제 상품 이미지>","key":"<승인된 Prefix>/<S3 object>","bytes":123,"sha256":"<파일의 64자리 SHA256>"}
]}
```

- manifest 파일 자체 SHA256과 각 파일 크기/Hash를 확인하며, 모델은 Image 내부 `/opt/encoder/model.lock.json`과도 일치 필요
- 첫 운영 배포는 BE가 상품 ID로 기존 S3 이미지를 제공하므로 모델 파일만 포함한 Manifest도 허용. 이때 AI `/media`는 빈 디렉터리이며 AI가 상품 이미지를 제공한다고 간주하지 않음
- 모델 누락·경로 탈출·중복 경로·Prefix 외 참조 차단. manifest최대1MiB·파일당256MiB·합계1GiB로 초기 제한
- 압축 해제/임의 코드 실행 없이 S3 Object를 Task 임시 Volume에 기록. 새 Task에서 다시 검증·다운로드
- Init만 root 및 CHOWN 권한 사용, 앱은 UID10001·read-only root·ALL capabilities 제거
- ECS Container dependency startTimeout은 최대120초. 실제 Init/Search 준비가 시간 안에 완료되지 않으면 기동 중단하며, 자산 선준비 방식을 재검토
- 데이터 복구 원본은 RDS와 승인된 S3 자산. 임시 Volume은 Task 종료 시 사라질 수 있음

## 활성화 확인값

desired_count=1은 database_migrated/catalog_seeded/network_verified/capacity_verified/runtime_contract_verified/credentials_ready/provider_budget_approved가 모두 true일 때만 허용. 확인값은 실검증 결과이며 테스트 우회용 기본 true를 제공하지 않음. 일회성 Job 실행 권한·실행 대상은 별도 배포 절차에서 검토 필요.

## 근거

- [awsvpc와 Task 내부 localhost](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-networking-awsvpc.html)
- [ECS Container dependency·timeout](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters_ec2.html)
- [Secret 주입과 회전 후 재배포](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html)
- [Service Connect 발견과 배포 순서](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service-connect-concepts-deploy.html)
- [SSE용 Service Connect 요청 시간 제한](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_TimeoutConfiguration.html)
