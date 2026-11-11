#!/bin/sh
set -eu
umask 077

# 기존 ECS와 동일하게 기본 Java CA에 RDS 공개 CA를 병합.
# 쓰기 위치는 Pod 전용 /tmp이며 이미지·공유 볼륨은 변경하지 않습니다.
command -v keytool >/dev/null
test -r "$JAVA_HOME/lib/security/cacerts"
test -r /app/bootstrap/rds-ca.pem
be_tls_dir=$(mktemp -d /tmp/seonjalal-rds-tls.XXXXXX)
be_truststore="$be_tls_dir/cacerts"
cp "$JAVA_HOME/lib/security/cacerts" "$be_truststore"
awk -v directory="$be_tls_dir" '
  /-----BEGIN CERTIFICATE-----/ { n++; target=directory "/ca-" n ".pem" }
  target != "" { print > target }
  /-----END CERTIFICATE-----/ { close(target); target="" }
' /app/bootstrap/rds-ca.pem
be_ca_count=0
for be_ca in "$be_tls_dir"/ca-*.pem; do
  test -f "$be_ca"
  be_ca_count=$((be_ca_count + 1))
  keytool -importcert -noprompt -alias "rds-root-$be_ca_count" \
    -file "$be_ca" -keystore "$be_truststore" \
    -storepass changeit >/dev/null
done
test "$be_ca_count" -ge 1
printf 'RDS_TRUSTSTORE_READY_CA_COUNT=%s\n' "$be_ca_count"

# changeit은 원래 Java cacerts의 공개 기본 비밀번호이며 앱/DB 인증값이 아닙니다.
exec java "-Djavax.net.ssl.trustStore=$be_truststore" \
  -Djavax.net.ssl.trustStorePassword=changeit \
  -jar /app/app.jar
