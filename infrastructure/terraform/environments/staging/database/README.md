# 스테이징 MySQL (운영 동일 구성)

검토한 스냅샷에서 복원하는 스테이징 전용 MySQL Root. 부하 시험·QA 용도이며 운영 DB와 State·파라미터 그룹·보안 그룹을 공유하지 않음.

| 항목 | 값 |
| --- | --- |
| 식별자 | `seonjalal-v2-staging` |
| 엔진·사양 | MySQL 8.4.11, `db.t4g.small`, Multi-AZ, gp3 20GiB(자동 확장 상한 50GiB) |
| 네트워크 | 운영·AI DB와 공유하는 Data Subnet 2개, 비공개, 새 보안 그룹(인바운드 비어 있음) |
| 파라미터 | 전용 그룹, `log_bin_trust_function_creators=1` 하나 |
| 보호 | 삭제 보호, `prevent_destroy`, 최종 스냅샷, 백업 7일 |
| 기본값 | `enable_database=false`. 리소스 0개 |

- 보안 그룹 인바운드(스테이징 Main/Payment Task → 3306)는 `be_split_runtime` 모듈이 이 그룹 ID를 입력받아 추가
- 마스터 비밀번호는 RDS가 Secrets Manager에서 관리. 값은 Terraform·문서에 기록하지 않음
- 리허설용 DB 보안 그룹(V1 원본·DMS 허용 규칙 잔존)은 재사용하지 않음

## 활성화 전 확인

1. 스냅샷 데이터 성격: 실사용자 정보 포함 여부. 스테이징을 공개로 운영하려면 확인 필수
2. 스냅샷 스키마 버전(Flyway 이력): 복원 후 읽기 전용 확인. 스테이징 BE는 `SPRING_FLYWAY_ENABLED=false`라 자동 마이그레이션 없음
3. 마스터 비밀번호 관리 전환: 스냅샷 복원과 `manage_master_user_password` 조합의 실제 동작은 Plan·Apply에서 확인 필요(미검증)
4. 암호화 스냅샷의 KMS 키: `snapshot_kms_key_arn` 지정 여부

## 적용 순서

사용자가 Mac Cloud 저장소에서 직접 실행. Plan 검토·승인 후 별도 Apply.

```bash
terraform -chdir=infrastructure/terraform/environments/staging/database init -backend-config=backend.hcl
terraform -chdir=infrastructure/terraform/environments/staging/database plan -out=staging-database.tfplan
```

Plan 합격 기준: 서브넷 그룹·보안 그룹·파라미터 그룹·로그 그룹·DB 인스턴스 추가, 기존 리소스 변경·삭제 0.

## 운영 시 주의

- Multi-AZ는 가용 영역을 지정할 수 없음. 복원 후 Primary가 앱과 다른 AZ이면 `--force-failover` 재부팅으로 조정(별도 승인)
- 중지 가능(Multi-AZ 포함), 최대 7일 후 자동 재시작. 중지 중에도 스토리지·백업 비용 발생. 시작 후 Primary·Standby AZ가 바뀔 수 있음
- 인스턴스 크기 변경은 부하 시험 수치를 운영 근거로 쓸 수 없게 하므로 검증 조건(`database_instance_class`)을 먼저 수정해야 함

## 로컬 검사

```bash
terraform -chdir=infrastructure/terraform/environments/staging/database validate
terraform -chdir=infrastructure/terraform/environments/staging/database test
```

AWS 연결 없는 Mock 검사. 실제 AWS Plan·Apply·복원·접속은 미검증.
