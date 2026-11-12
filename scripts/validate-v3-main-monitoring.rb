#!/usr/bin/env ruby
# Main 관리 포트 연결의 배포 경계 검사. Secret 값·클러스터·AWS 접근 없음.
require 'yaml'
require 'open3'
require 'digest'
require 'json'
ROOT = File.expand_path('..', __dir__)
MAIN = %w[member-service catalog-service gifting-service recommendation-service notification-service].freeze

def check(value, message)
  raise message unless value
end

def render(name, *extra, payment: false)
  args = ['helm', 'template', name, 'helm/charts/backend', '--namespace', 'production',
          '-f', 'helm/environments/production/backend/common.yaml']
  args += ['-f', 'helm/environments/production/backend/main.yaml'] unless payment
  args += ['-f', "helm/environments/production/backend/#{name}.yaml"]
  Open3.capture3(*args, *extra, chdir: ROOT)
end

def objects(name, *extra, payment: false)
  out, _, status = render(name, *extra, payment: payment)
  check(status.success?, "렌더 실패: #{name}")
  YAML.load_stream(out).compact
end

begin
  enabled = ['--set', 'monitoring.enabled=true', '--set', 'monitoring.healthBridgeImageReviewed=true',
             '--set', "image.digest=sha256:#{'1' * 64}"]
  MAIN.each do |name|
    before = objects(name, '--set', 'monitoring.enabled=false', '--set', 'monitoring.healthBridgeImageReviewed=false')
    after = objects(name, *enabled)
    check(before.length == 5 && after.length == 6, '기본 리소스 또는 정책 수 변경')
    check(before.none? { |o| o['kind'] == 'NetworkPolicy' }, '기본 정책 자동 추가')
    old_dep = before.find { |o| o['kind'] == 'Deployment' }
    dep = after.find { |o| o['kind'] == 'Deployment' }
    old_container = old_dep.dig('spec', 'template', 'spec', 'containers', 0)
    container = dep.dig('spec', 'template', 'spec', 'containers', 0)
    check(container['ports'] == old_container['ports'] + [{'name' => 'management', 'containerPort' => 8081}],
          '관리 포트 외 추가 포트 변경')
    %w[startupProbe readinessProbe livenessProbe resources envFrom].each do |field|
      check(container[field] == old_container[field], "기존 probe/자원/주입 변경: #{field}")
    end
    check(container['env'] == old_container['env'] + [{'name' => 'MONITORING_PASSWORD', 'valueFrom' => {
          'secretKeyRef' => {'name' => 'v3-main-monitoring', 'key' => 'password'}}}], '전용 인증 주입 오류')
    check(after.find { |o| o['kind'] == 'Service' } == before.find { |o| o['kind'] == 'Service' },
          '관리 포트 Service 공개 또는 기존 Service 변경')
    old_data = before.find { |o| o.dig('metadata', 'name') == "#{name}-runtime" }.fetch('data')
    data = after.find { |o| o.dig('metadata', 'name') == "#{name}-runtime" }.fetch('data')
    check(old_dep.dig('spec', 'template', 'metadata', 'annotations', 'checksum/runtime') ==
          Digest::SHA256.hexdigest(JSON.generate(old_data.sort.to_h)), '기본 runtime checksum 변경')
    check(old_data.all? { |key, value| data[key] == value }, '기존 업무 설정 변경')
    check(data['SPRING_PROFILES_INCLUDE'] == 'monitoring' && data['MONITORING_HEALTH_BRIDGE_ENABLED'] == 'true',
          '프로필·Health 호환 조건 누락')
    check(data['MANAGEMENT_SERVER_ADDRESS'] == '0.0.0.0' && data['MANAGEMENT_SERVER_PORT'] == '8081',
          '관리 주소/포트 오류')
    check(data['HIKARI_MAX_POOL_SIZE'] == '5' && data['HIKARI_MIN_IDLE'] == '1', 'Hikari 연결 수 확대')
    policy_object = after.find { |o| o['kind'] == 'NetworkPolicy' }
    check(policy_object.dig('metadata', 'annotations', 'argocd.argoproj.io/sync-wave') == '-1', '접근 정책 선적용 누락')
    policy = policy_object.fetch('spec')
    check(policy['podSelector']['matchLabels'] == dep.dig('spec', 'selector', 'matchLabels'), '정책 대상 확대')
    check(policy['policyTypes'] == ['Ingress'] && !policy.key?('egress'), '업무 egress 변경')
    check(policy['ingress'] == [
      {'ports' => [{'protocol' => 'TCP', 'port' => 8080}]},
      {'from' => [{'podSelector' => {'matchLabels' => {'app.kubernetes.io/name' => 'v3-metrics-agent'}}}],
       'ports' => [{'protocol' => 'TCP', 'port' => 8081}]}
    ], '업무 접근 보존·동일 namespace 수집기 제한 오류')
    check(!render(name, '--set', 'monitoring.enabled=true', '--set', 'monitoring.healthBridgeImageReviewed=false')[2].success?, '미검토 이미지 활성화 가드 우회')
    check(!render(name, *enabled, '--set', 'monitoring.healthBridgeImageReviewed=false')[2].success?,
          '미검토 이미지 활성화 가드 우회')
  end
  check(!render(MAIN.first, '--set', 'monitoring.enabled=true', '--set',
        'monitoring.healthBridgeImageReviewed=true', '--set',
        'image.digest=sha256:4bff8ce0b6e372076f59a48280d7981ac95a7c01710ebbda9a7642e584832e11')[2].success?, '기존 Digest를 검토 플래그로 우회')
  check(!render('payment-server', *enabled, payment: true)[2].success?, 'Payment 모니터링 허용')
  check(!render(MAIN.first, *enabled, '--set', 'name=unknown-service')[2].success?, 'Main 범위 확대')
  check(!render(MAIN.first, *enabled, '--set', 'env.SPRING_PROFILES_INCLUDE=unknown')[2].success?,
        '기존 프로필 덮어쓰기')
  secret = YAML.safe_load(File.read(File.join(ROOT, 'operations/monitoring/main/credentials.yaml')))
  check(secret['spec']['data'] == [{'secretKey' => 'password', 'remoteRef' => {
    'key' => 'seonjalal/v3/production/backend-runtime', 'property' => 'MONITORING_PASSWORD'}}],
    '단일 인증 키 투영 범위 변경')
  check(YAML.load_file(File.join(ROOT, 'helm/charts/backend/values.yaml')).dig('monitoring', 'enabled') == false, 'Chart 기본 활성화 금지')
  MAIN.each do |name|
    check(objects(name).length == 6, '운영 Main 활성화 누락')
  end
  puts 'MAIN_MONITORING_CHECK: Main=5 / chart default disabled / production enabled / old image and Payment rejected'
  puts 'CONTRACT_CHECK: app probes and Service preserved / Hikari 5:1 / Agent-only management ingress'
  puts 'STATIC_ONLY: 운영 비밀번호·CNI 차단·새 이미지·실제 수집 미검증'
rescue StandardError => e
  warn e.message
  exit 1
end
