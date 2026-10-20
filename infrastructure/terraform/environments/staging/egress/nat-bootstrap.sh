#!/bin/bash
# NAT 설정을 재부팅 후에도 유지. 완료 마커 확인 전 App 경로 연결 금지.
set -euo pipefail
for attempt in $(seq 1 12); do
  if dnf install -y iptables-services; then break; fi
  sleep 5
done
rpm -q iptables-services
systemctl enable --now iptables
printf 'net.ipv4.ip_forward=1\n' > /etc/sysctl.d/90-staging-nat.conf
sysctl --system
nat_interface=$(ip -4 route show default | awk 'NR == 1 {print $5}')
test -n "$nat_interface"
iptables -t nat -C POSTROUTING -o "$nat_interface" -j MASQUERADE 2>/dev/null ||
  iptables -t nat -A POSTROUTING -o "$nat_interface" -j MASQUERADE
iptables -P FORWARD ACCEPT
# 이 인스턴스는 NAT 전용. 배포판 기본 FORWARD 거절 규칙 제거.
iptables -F FORWARD
service iptables save
systemctl enable iptables
systemctl restart iptables
test "$(sysctl -n net.ipv4.ip_forward)" = 1
iptables -t nat -C POSTROUTING -o "$nat_interface" -j MASQUERADE
install -m 600 /dev/null /var/lib/staging-nat-ready
echo NAT_BOOTSTRAP_OK
