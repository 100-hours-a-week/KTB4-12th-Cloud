#!/bin/sh
# 고정1.3.0 이미지의 JRE와 기본 Root CA를 유지하면서 RDS Root만 추가.
# 외부 다운로드/앱 이미지 재빌드/DB 접속/Secret 출력 없음.
set -eu
umask 077
command -v keytool >/dev/null
test -r "$JAVA_HOME/lib/security/cacerts"
migration_tls_dir=$(mktemp -d /tmp/seonjalal-rds-tls.XXXXXX)
migration_truststore="$migration_tls_dir/cacerts"
cp "$JAVA_HOME/lib/security/cacerts" "$migration_truststore"

# 오프라인 이미지 검사는 공개 CA 파일 Mount, ECS에서는 공개 PEM 환경변수 사용.
if [ -n "${STAGING_RDS_CA_FILE:-}" ]; then
  test "${STAGING_TLS_PREPARE_ONLY:-0}" = 1
  cp "$STAGING_RDS_CA_FILE" "$migration_tls_dir/bundle.pem"
else
  test -n "${STAGING_RDS_CA_BUNDLE:-}"
  printf '%s\n' "$STAGING_RDS_CA_BUNDLE" > "$migration_tls_dir/bundle.pem"
fi
awk -v directory="$migration_tls_dir" '
  /-----BEGIN CERTIFICATE-----/ { n++; target=directory "/ca-" n ".pem" }
  target != "" { print > target }
  /-----END CERTIFICATE-----/ { close(target); target="" }
' "$migration_tls_dir/bundle.pem"
migration_ca_count=0
for migration_ca in "$migration_tls_dir"/ca-*.pem; do
  test -f "$migration_ca"
  migration_ca_count=$((migration_ca_count + 1))
  # 공개 Root 인증서 저장소의 기본 암호이며 DB/JWT 비밀번호와 무관.
  keytool -importcert -noprompt -alias "staging-rds-$migration_ca_count" \
    -file "$migration_ca" -keystore "$migration_truststore" \
    -storepass changeit >/dev/null
done
test "$migration_ca_count" = 3
printf 'RDS_TRUSTSTORE_READY_CA_COUNT=%s\n' "$migration_ca_count"

# 로컬 검사는 Java 앱을 기동하지 않고 종료. ECS에는 이 플래그를 설정하지 않음.
if [ "${STAGING_TLS_PREPARE_ONLY:-0}" = 1 ]; then
  keytool -list -keystore "$migration_truststore" -storepass changeit \
    -alias staging-rds-1 >/dev/null
  printf 'OFFLINE_TLS_BOOTSTRAP_OK_NO_APP_OR_DB\n'
  exit 0
fi
exec java "-Djavax.net.ssl.trustStore=$migration_truststore" \
  -Djavax.net.ssl.trustStorePassword=changeit \
  -jar /app/app.jar
