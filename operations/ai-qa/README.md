# V3 AI 관리자 경로

이슈 #131 대상은 `/admin/*`를 기존 AI Chat으로 연결하는 경로다. ECS·새 AI Pod·별도 ALB를 만들지 않는다.

## 변경 경계

- `service.json`: 기존 AI Pod를 선택하는 별도 NodePort Service. 기존 `ai-chat` ClusterIP와 Helm values를 보존하며 AI Pod template을 변경하지 않음.
- `instance` 대상은 노드 NodePort를 통해 접근하므로 Pod IP의 VPC 직접 라우팅을 전제하지 않음. Calico 등에서 Pod 주소가 VPC ENI 주소와 다를 수 있어 `ip` 모드를 임의로 선택하지 않음.
- Service 단위 ALB annotation으로 관리자 대상 그룹만 HTTP/1·`/healthz` 상태 검사를 사용. 기존 Main 대상 그룹 설정 유지.
- 기존 Ingress의 경로 목록에 `/admin` Prefix → `ai-chat-admin:7860`만 추가. 전체 Ingress를 재작성하지 않고 resourceVersion·기존 경로 test로 동시 변경 차단 필요.
- CloudFront에 `/admin/*` 전용 behavior 추가. 기존 API origin·캐시 비활성화·쿠키/Origin/쿼리 전달을 사용하며 `/api` 제거용 함수와 SPA 함수는 관리자 behavior에 연결하지 않음. `/admin/qa` URI 그대로 보존.
- 이 Service는 운영 manifest로 별도 관리하며 Argo CD의 기존 7개 앱 리소스 승계 범위를 변경하지 않음. 기존 Service/Ingress/CloudFront 전체 삭제·교체 금지.

## 적용 전 조건

1. 운영 이미지에서 `/admin/static/*`와 공개 Origin 기준 검증이 존재하는지 확인. 최신 저장소 코드가 있다는 사실로 운영 반영을 추정하지 않음.
2. 정상 운영 환경·Secure 쿠키 설정, 미인증 관리자 API 401 확인. 실제 로그인 비밀번호는 사용자만 입력하며 로그·명령 인자·Git에 저장하지 않음.
3. 기존 Ingress·CloudFront revision을 대조한 구체적 추가/복구 계획 확인. 이미 다른 관리자 route/behavior가 있으면 임의 덮어쓰기 중단.
4. 컨트롤러가 새 NodePort 대상에 필요한 ALB 출발지 제한 규칙을 관리하는지 확인. 인터넷 전체에 NodePort 개방 금지.

## 완료·복구

- 관리자 HTML·허용 정적 파일 200, 미인증 데이터 API 401, 금지 Origin 403, 로그인 후 정상 조회와 기존 로그인/API/chat 회귀 확인.
- 코드상 로그인 페이지와 정적 파일의 미인증 접근은 정상. 데이터 API의 인증 실패와 구분.
- 되돌림은 관리자 CloudFront behavior와 추가 Ingress 경로만 제거하고, 전달이 멈춘 뒤 전용 Service 정리. 기존 API behavior·원본·Pod·Secret 보존.
- Service 추가 자체는 Pod 재시작을 유발하지 않음. 필수 AI 코드가 없어서 이미지 변경이 필요한 경우는 별도 배포 영향과 중단 시간을 명시한 뒤 진행.

근거: [Service annotation 우선순위와 대상 모드](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/guide/ingress/annotations/).

## 실행과 증거

`python3 scripts/prepare-v3-ai-qa-routing.py`는 계정·운영 이미지 사전 증거·Pod 준비 상태와 현재 설정을 읽고 Service·Ingress의 서버 dry run을 수행한다. 계획은 `/private/tmp/seonjalal-v3-qa/plan.json`에 저장한다. 전체 CloudFront 설정은 기존 원본 헤더를 보호하기 위해 파일에 저장하지 않는다.

`python3 scripts/prepare-v3-ai-qa-routing.py --mode apply`는 계획과 현재 ETag·Ingress revision·Pod UID가 같을 때만 진행한다. Service 생성 → Ingress 경로 추가 → 대상 그룹 healthy 관측 → CloudFront 변경·전파 대기 → 공개 GET 검사를 수행한다. 앱 로그인·대화 생성은 수행하지 않는다. 대상 준비는 최대 180초, CloudFront 전파 대기는 최대 600초이며 API 조회 시간은 별도다.

이번 실행의 변경 기록 이후 오류가 발생하면 추가 관리자 behavior와 경로에 한정해 자동 복구를 시도한다. 요청 타임아웃처럼 적용 여부가 불명확하면 기록만으로 실제 상태를 단정하지 않는다. 복구 실패·부분 적용 시 무조건 재실행하지 않고 실제 상태를 확인한다. Service는 복구 후에도 조사용으로 남긴다. 수동 경로 복구 명령은 `--mode rollback`이며 이미 변경된 관리자 설정은 덮어쓰지 않는다.

결과는 `apply-receipt.json`에 단계 시각·할당 NodePort·등록/healthy 대상 수·HTTP 검사·Pod UID 보존 여부를 기록한다. Pod UID 변화는 별도 스케줄러 교체일 수도 있어 경로 변경에 의한 재시작으로 단정하지 않는다. 최종 사용자의 관리자 로그인·조회와 기존 앱 로그인/chat 회귀는 별도 확인해야 한다. 공개 GET 통과를 전체 업무 정상화로 확대하지 않는다. 기존 ALB를 사용하므로 별도 ALB·EC2를 생성하지 않지만 요청·대상 처리에 기존 ALB/CloudFront 종량 비용이 발생할 수 있다.
