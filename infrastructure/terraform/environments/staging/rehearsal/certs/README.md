# 리허설 RDS 공개 Root CA

- 출처: https://truststore.pki.rds.amazonaws.com/ap-northeast-2/ap-northeast-2-bundle.pem
- 기존 관리 TLS 사전 점검에서 사용한 공개 번들, 2026-10-17 저장소 반영
- 서울 ECC384/RSA2048/RSA4096 G1 Root 3개. 비밀번호·Private Key·DB 데이터 아님
- TLS 준비 시 기본 JRE cacerts의 복사본에만 추가하여 기존 AWS HTTPS Root 유지
- 갱신은 공식 HTTPS 출처/인증서 정보 확인 후 로컬 오프라인 검사와 실제 JDBC 검증 반복
- 이미지 재빌드 없이 ECS Task 설정에 공개 PEM만 전달, 기동 시 외부 다운로드 없음
