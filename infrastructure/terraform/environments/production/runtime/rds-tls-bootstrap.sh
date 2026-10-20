#!/bin/sh
# 기존 V1 이미지의 기본 신뢰 저장소에 공개 RDS CA만 추가. 이미지 재빌드 없음.
set -eu
umask 077
command -v keytool >/dev/null
test -r "$JAVA_HOME/lib/security/cacerts"
test -n "${RDS_CA_BUNDLE:-}"
tls_dir=$(mktemp -d /tmp/seonjalal-production-tls.XXXXXX)
cp "$JAVA_HOME/lib/security/cacerts" "$tls_dir/cacerts"
printf '%s\n' "$RDS_CA_BUNDLE" > "$tls_dir/bundle.pem"
awk -v directory="$tls_dir" '
  /-----BEGIN CERTIFICATE-----/ { n++; target=directory "/ca-" n ".pem" }
  target != "" { print > target }
  /-----END CERTIFICATE-----/ { close(target); target="" }
' "$tls_dir/bundle.pem"
ca_count=0
for ca in "$tls_dir"/ca-*.pem; do
  test -f "$ca"
  ca_count=$((ca_count + 1))
  keytool -importcert -noprompt -alias "production-rds-$ca_count" \
    -file "$ca" -keystore "$tls_dir/cacerts" \
    -storepass changeit >/dev/null
done
test "$ca_count" = 3
printf 'PRODUCTION_RDS_TRUSTSTORE_READY_CA_COUNT=%s\n' "$ca_count"

# 공용 신뢰 저장소 기본 암호이며 DB/JWT 비밀번호가 아님. 종료 신호를 JVM에 직접 전달.
exec java "-Djavax.net.ssl.trustStore=$tls_dir/cacerts" \
  -Djavax.net.ssl.trustStorePassword=changeit \
  -jar /app/app.jar
