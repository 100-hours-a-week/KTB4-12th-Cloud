# V2 Develop 부팅 재조정 Rollback 반복 트러블슈팅 기록

- 발견일: 2026-10-12 KST
- 대상 환경: V2 Develop
- 영향 범위: EC2 Stop 후 Start 과정의 부팅 재조정, Tailnet HTTPS 가용성
- 현재 상태: 이전 정상 Candidate Rollback과 반복 재시도 임시 차단 완료
- 공개 기록 제외 대상: AWS Account ID, Instance ID, ECR Registry, Image SHA, Tailnet Domain·IP

## 1. 요약

V2 Develop EC2 Start 후 systemd 부팅 재조정이 최신 희망 FE·BE Candidate를 적용하는 과정에서
`docker compose up --wait` 실패 발생. 이전 Candidate Rollback과 Runtime 검증은 성공했지만
`reconcile.sh`가 원래 배포 실패 코드로 종료하고 systemd의 `Restart=on-failure`가 같은 Candidate를
다시 실행하면서 배포·Rollback 반복 발생.

반복 중 Frontend Container가 교체되어 Tailnet HTTPS가 정상 화면과 HTTP 502를 번갈아 반환.
현재 부팅 세션의 systemd Drop-in으로 `Restart=no`를 적용하고 마지막 Rollback 종료 대기 후
Frontend·Main Backend·Payment Server·MySQL Health와 기능 동작 확인 완료.

최신 Develop Candidate의 정확한 실패 Container와 애플리케이션 원인은 아직 미확정. 현재 Runtime은
최신 희망 SHA가 아닌 이전 정상 Candidate 사용 중.

## 2. 사용자 관찰 현상

| 현상 | 판정 |
|---|---|
| 관리자 Browser의 HTTP 502 | Tailscale HTTPS 성공 후 Loopback Frontend 부재로 인한 Upstream 실패 |
| 팀원 Browser의 `ERR_NAME_NOT_RESOLVED` | `Needs approval` 장치의 Tailnet MagicDNS 미활성 상태 |
| 잠시 정상 접속 후 다시 502 | 신규 Candidate 배포와 이전 Candidate Rollback 반복에 따른 Container 교체 |
| 상태 조회 직후 Container 재시작처럼 보이는 현상 | 읽기 전용 조회와 무관한 systemd 자동 재시도 |
| `terraform output`과 SSM Target 실패 | Local AWS Login 만료로 인한 별도 진단 경로 실패 |
| systemd `failed`와 Container `healthy` 동시 표시 | 최신 Candidate 작업 실패와 이전 Candidate Runtime 복구의 서로 다른 상태 표현 |

## 3. 접근 경로별 판정

```text
Browser
  → MagicDNS
  → 승인된 Tailnet 장치
  → Tailscale Serve HTTPS
  → http://127.0.0.1:8080
  → Frontend Nginx
  → /api Main Backend
```

- HTTP 502 확인으로 MagicDNS·Tailnet 경로·TLS·Tailscale Serve까지 정상임을 판정.
- `curl http://127.0.0.1:8080` 실패로 최초 502의 직접 원인을 Frontend 미기동으로 한정.
- Tailscale Serve Proxy 대상이 `127.0.0.1:8080`으로 유지됨을 확인.
- 팀원 장치의 `Needs approval` 상태와 `ERR_NAME_NOT_RESOLVED`의 별도 원인 확인.

## 4. 확인한 증거

### 4.1 반복 재시도

```text
Restart=on-failure
NRestarts=10
ActiveState=activating
```

Journal 반복 패턴:

```text
docker compose ... up --detach --remove-orphans --wait --wait-timeout 240
ROLLBACK: 이전 Develop Candidate 복구 시작
Develop Runtime 검증 완료
PASS: 이전 Develop Candidate 복구 완료
Main process exited, status=1/FAILURE
Scheduled restart job
```

### 4.2 안정화 이후 Runtime

```text
Restart=no
ActiveState=failed
Result=exit-code
ExecMainStatus=1
NRestarts=10
```

동시 확인 결과:

- Frontend Container `healthy`
- Main Backend Container `healthy`
- Payment Server Container `healthy`
- MySQL Container `healthy`
- Frontend Loopback 응답 성공
- Backend Proxy Health `UP`
- 관리자 Browser 접속과 주요 기능 동작 확인

`ActiveState=failed`는 최신 Candidate 재조정 작업의 실패 상태. Container `healthy`는 Rollback으로
복구된 이전 Candidate의 현재 실행 상태.

## 5. 확인된 원인

### 5.1 최신 Candidate 배포 실패

`deploy.sh`의 다음 단계에서 비정상 종료 확인.

```bash
docker compose up --detach --remove-orphans --wait --wait-timeout 240
```

후속 `verify.sh` 진입 전에 Rollback이 시작됐으므로 최신 Candidate의 하나 이상 Container가 제한 시간
내 Healthy 상태에 도달하지 못했거나 실행에 실패한 것으로 판정.

정확한 실패 서비스와 애플리케이션 원인은 현재 로그만으로 미확정. Rollback 전에 Compose 상태와
Container 로그를 보존하지 않았고, Rollback이 실패 Candidate를 이전 Container로 교체해 증거 소실.

### 5.2 Rollback과 systemd 재시작 정책 충돌

`reconcile.sh`는 Rollback 성공 후에도 원래 `deploy_status`를 반환해 신규 배포 실패를 숨기지 않는 구조.
systemd Unit은 `Restart=on-failure`, `RestartSec=30` 구성. 두 계약의 결합으로 동일한 희망 Candidate의
반복 적용 발생.

```text
신규 Candidate 실패
  → 이전 Candidate Rollback 성공
  → 원래 실패 코드 반환
  → systemd 자동 재시작
  → 같은 신규 Candidate 재시도
```

`StartLimitBurst=5`, `StartLimitIntervalSec=600`이 존재하지만 배포·Rollback Cycle이 길어 관찰 구간에서
반복 차단 효과 없음. `NRestarts=10`까지 증가 확인.

## 6. 수행한 안정화

### 6.1 적용 내용

현재 부팅 세션 전용 systemd Drop-in 생성.

```ini
[Service]
Restart=no
```

적용 경로:

```text
/run/systemd/system/seonjalal-develop-reconcile.service.d/10-disable-restart.conf
```

- 실행 중인 Candidate 시도와 Rollback 강제 중단 없음.
- 마지막 Rollback 완료 후 추가 자동 재시도 차단.
- Container 중지나 Data 변경 없음.
- `/run` 기반 임시 설정으로 EC2 재부팅 시 자동 소멸.

### 6.2 초기 runtime mask가 효과 없었던 이유

`systemctl mask --runtime` 실행 후에도 `systemctl is-enabled` 결과가 `enabled`로 유지. 서비스 원본이
`/etc/systemd/system`에 직접 설치된 현재 구성에서 `/run/systemd/system`의 runtime mask가 실제 Unit
선택을 덮어쓰지 못한 상태 확인. runtime Drop-in 방식으로 재시작 속성만 명시적 변경.

## 7. 현재 운영 판단

| 항목 | 상태 |
|---|---|
| 팀 개발 서버 사용 | 가능 |
| 관리자 Browser·주요 기능 | 정상 확인 |
| 팀원 장치 접근 | 장치 승인 및 Tailnet 연결 후 HTTPS 443만 허용, 실제 전원 End-to-End 확인 필요 |
| 현재 실행 버전 | 이전 정상 Candidate |
| 최신 희망 Candidate | 미적용 |
| 자동 배포 서비스 | 실패 상태 유지, 현재 부팅에서 재시도 차단 |
| EC2 재부팅·Stop/Start | 영구 수정 전 반복 재발 가능 |

## 8. 남은 작업

### P0: 다음 EC2 재부팅 전 처리

- `deploy.sh` 실패 Trap에서 Rollback 전 `docker compose ps --all` 수집 추가.
- 실패 서비스의 `docker inspect` Health Log·Exit Code·OOM 여부 수집 추가.
- Secret Redaction을 적용한 제한된 `docker compose logs --tail` 수집 추가.
- Rollback 성공 후 동일 Candidate 무한 재시도 방지 계약 확정.
- Unit·Script 수정 후 Local 정적 검사와 Develop 단일 재현 검증.

### P1: 최신 Candidate 원인 규명

- FE·BE 희망 SHA와 현재 Rollback Image Tag의 불일치 범위 확인.
- 점검 시간에 최신 Candidate 한 번만 적용.
- 실패 Container 로그 확보 후 Application 설정·Migration·Resource 사용량 순서 조사.
- 원인 수정 Image 발행 후 네 Container Health와 Tailnet 기능 재검증.

### P2: 운영성 보완

- Rollback 발생 시 GitHub Workflow 또는 별도 Alert로 실패 전달.
- systemd 작업 상태와 현재 실행 Candidate 상태의 분리 표시.
- 팀원 전원 장치 승인·MagicDNS HTTPS 접속 결과 기록.
- EC2 Start Runbook에 재조정 완료 전 502 가능성과 완료 판정 기준 추가.

## 9. 재부팅 전 주의사항

- 현재 runtime Drop-in은 재부팅 시 삭제됨.
- 영구 수정 전 EC2 Stop·Start 시 기존 `Restart=on-failure` 복원과 반복 배포 재발 가능.
- 불가피한 재부팅 전 희망 SHA를 이전 정상 Candidate로 되돌리거나 영구 수정 반영 필요.
- 현재 `failed` 상태를 단순 `systemctl restart`로 해제하지 않음. 동일 실패 Candidate 재실행 가능.

## 10. GitHub Issue 초안

### 제목

`fix: Develop 부팅 재조정 실패 후 Rollback 반복 방지`

### 본문 요약

- 현상: EC2 Start 후 최신 Candidate Health 실패, 이전 Candidate Rollback 성공 직후 동일 배포 반복
- 영향: Tailnet HTTPS의 정상 응답과 502 반복, 팀 개발환경 간헐적 중단
- 원인: Rollback 성공 후 원래 실패 코드 반환과 systemd `Restart=on-failure`의 결합
- 미확정 항목: 최신 Candidate의 실제 실패 Container와 애플리케이션 원인
- 임시 조치: 현재 부팅 전용 `Restart=no` Drop-in, 이전 Candidate 안정화
- 완료 조건: 실패 로그 보존, 제한된 재시도, Rollback 후 안정 유지, 최신 Candidate 원인 해결과 재검증

## 11. 관련 코드

- `operations/develop/runtime/scripts/reconcile.sh`
- `operations/develop/runtime/scripts/deploy.sh`
- `operations/develop/runtime/scripts/verify.sh`
- `operations/develop/runtime/systemd/seonjalal-develop-reconcile.service`
- `operations/develop/runtime/compose.yaml`

