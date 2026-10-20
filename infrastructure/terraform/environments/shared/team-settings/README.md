# 팀별 비운영 설정 접근

개인 IAM 계정4개, 기존fs-theo조회, 팀별그룹2개, 팀/환경별 빈Secret4개와MFA필수설정접근권한 구성.

- Fullstack: fs-neo/fs-theo/fs-ava, AI: ai-dylan/ai-emet
- 담당Secret: `/seonjalal/{develop|staging}/teams/{fullstack|ai}`
- 담당일반설정: `/seonjalal/{develop|staging}/config/{fullstack|ai}/<KEY>`
- 기존DB/JWT Secret·타팀·Production·release배포기준값·SSM서버명령접근제외
- 새Secret4개는보관비용발생. 기존Secret값복사/삭제나운영·QA재배포는수행하지않음
- 초기비밀번호/AccessKey/Secret값/파라미터값은Terraform에생성하지않음. MFA등록·자기비밀번호변경만자가관리허용,초기로그인은Cloud별도절차

## 적용 순서

사용자가Mac Cloud저장소에서직접실행. Plan결과검토/승인후별도Apply,동시실행금지.

```bash
# 별도State초기화 — 기존Develop/Staging/운영State와분리
terraform -chdir=infrastructure/terraform/environments/shared/team-settings init -backend-config=backend.hcl
```

```bash
# 생성계획저장 — AWS실제변경없음
terraform -chdir=infrastructure/terraform/environments/shared/team-settings plan -out=team-settings.tfplan
```

Plan검토후Apply단계를별도안내. 기존fs-theo의AdministratorAccess/AmazonS3FullAccess는이Terraform코드에서자동회수하지않음. 팀그룹·정책반영확인후Cloud가해당계정의두정책만분리하는절차로수행. Theo를위한권한회수승인은확보했으나실제회수전후정책목록·시뮬레이션검증필요. 새정책추가만으로fs-theo의광역권한제거완료표시금지.

MFA로검증된세션에서본인팀비운영설정읽기/쓰기허용,타팀·운영·플랫폼/배포경로·서버조작거부를검증. Parameter상위경로의재귀조회도거부확인. 일반콘솔목록을위한전계정Secrets/Parameters나열권한은부여하지않으므로정확한리소스주소로접근필요.

팀별저장소를현재배포로더/ECS정의에연동하기전까지기존런타임값유지. 이기반생성만으로팀입력값자동주입완료는아님. 현재설정권한은운영값을조회하거나배포를실행하는권한이아님.

## 로컬 검사

```bash
# AWS연결없는구문및mock정책범위검사
terraform -chdir=infrastructure/terraform/environments/shared/team-settings validate
terraform -chdir=infrastructure/terraform/environments/shared/team-settings test
```

정책상세설계·작업기록은로컬docs에보존,코드Repository/Wiki에자동게시하지않음.
