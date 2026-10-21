#!/bin/sh
set -eu
umask 077
command -v keytool >/dev/null
test -r "$JAVA_HOME/lib/security/cacerts"
test -n "${RDS_CA_BUNDLE:-}"
be_tls_dir=$(mktemp -d /tmp/seonjalal-be-rds-tls.XXXXXX)
be_truststore="$be_tls_dir/cacerts"
cp "$JAVA_HOME/lib/security/cacerts" "$be_truststore"
printf '%s\n' "$RDS_CA_BUNDLE" > "$be_tls_dir/bundle.pem"
awk -v directory="$be_tls_dir" '
  /-----BEGIN CERTIFICATE-----/ { n++; target=directory "/ca-" n ".pem" }
  target != "" { print > target }
  /-----END CERTIFICATE-----/ { close(target); target="" }
' "$be_tls_dir/bundle.pem"
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
exec java "-Djavax.net.ssl.trustStore=$be_truststore" \
  -Djavax.net.ssl.trustStorePassword=changeit \
  -jar /app/app.jar
