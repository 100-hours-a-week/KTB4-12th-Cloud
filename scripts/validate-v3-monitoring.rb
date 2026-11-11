#!/usr/bin/env ruby
# 로컬 렌더·수집 범위·권한 검사. 클러스터/호스트/Secret/네트워크 조회 없음.
require 'yaml'
require 'open3'
require 'fileutils'

ROOT = File.expand_path('..', __dir__)
CHART = 'helm/charts/monitoring-agent'.freeze
def render(*args)
  Open3.capture3('helm', 'template', 'v3-metrics-agent', CHART,
                '--namespace', 'production', *args, chdir: ROOT)
end
def check(ok, message)
  raise message unless ok
end
def objects(*args)
  out, _err, status = render(*args)
  check(status.success?, 'Agent 렌더 실패')
  YAML.load_stream(out).compact
end

begin
  docs = objects
  check(docs.map { |o| o['kind'] }.sort == %w[ConfigMap Deployment Role RoleBinding ServiceAccount].sort,
        '허용 범위 밖 리소스 또는 누락')
  check(docs.all? { |o| o.dig('metadata', 'namespace') == 'production' }, 'namespace 범위 변경')
  deployment = docs.find { |o| o['kind'] == 'Deployment' }
  check(deployment.dig('spec', 'replicas') == 0, '준비 단계는 실행 0개 필요')
  role = docs.find { |o| o['kind'] == 'Role' }
  check(role['rules'] == [{'apiGroups' => [''], 'resources' => ['pods'], 'verbs' => %w[get list watch]}],
        'Pod discovery 외 권한 변경')
  config = docs.find { |o| o['kind'] == 'ConfigMap' }.fetch('data').fetch('prometheus.yml')
  parsed = YAML.safe_load(config, aliases: true)
  check((parsed.keys & %w[rule_files alerting]).empty?, 'Agent에 중앙 규칙/알림 평가 혼입')
  jobs = parsed.fetch('scrape_configs')
  check(jobs.map { |j| j['job_name'] } == %w[rabbitmq rabbitmq-queues rabbitmq-dlq-age prometheus-agent],
        '수집 범위 변경')
  jobs.take(3).each do |job|
    check(job['kubernetes_sd_configs'] == [{'role' => 'pod', 'namespaces' => {'names' => ['production']}}],
          'namespace Pod discovery 범위 변경')
    port = job['relabel_configs'].find { |r| r['source_labels'] == ['__meta_kubernetes_pod_container_port_name'] }
    check(port == {'source_labels' => ['__meta_kubernetes_pod_container_port_name'], 'regex' => 'prometheus', 'action' => 'keep'},
          'RabbitMQ metrics 외 포트 수집')
  end
  work = %w[gift.receipt.email gift.roulette gift.coupon.restore gift.payment.approval.recovery gift.payment.cancellation.recovery]
  payments = work.flat_map { |n| ["#{n}.q", "#{n}.dlq"] }
  profiling = %w[profiling.request profiling.result profiling.request.failed profiling.result.failed]
  reports = %w[Report.discord Report.sheets Report.retry Report.dlq]
  broad = jobs[1].fetch('params')
  narrow = jobs[2].fetch('params')
  check(broad['vhost'] == ['gift'] && narrow['vhost'] == ['gift'], '다른 vhost 수집')
  check(broad['family'] == %w[queue_coarse_metrics queue_consumer_count queue_delivery_metrics exchange_metrics],
        '기본 상세 지표 계약 변경')
  regex = Regexp.new(broad.fetch('queue').fetch(0))
  check((payments + profiling + reports).all? { |q| regex.match?(q) }, '필수 큐 이름 누락')
  check(%w[unknown gift.receipt.email.q.extra XReport.discord profiling.request.extra].none? { |q| regex.match?(q) },
        '큐 필터 경계 오류')
  regex_dlq = Regexp.new(narrow.fetch('queue').fetch(0))
  check(narrow['family'] == ['queue_metrics'], 'DLQ 상세 family 변경')
  check(work.all? { |q| regex_dlq.match?("#{q}.dlq") } &&
        (work.map { |q| "#{q}.q" } + profiling + reports).none? { |q| regex_dlq.match?(q) },
        'queue_metrics가 결제 DLQ5개 외 대상으로 확장')
  positive = ['--set', 'replicas=1', '--set', 'receiverReviewed=true',
              '--set', "image.digest=sha256:#{'1' * 64}",
              '--set', 'remoteWrite.url=http://monitoring.test.internal:9090/api/v1/write']
  ready = objects(*positive).find { |o| o['kind'] == 'Deployment' }
  check(ready.dig('spec', 'replicas') == 1, '검토 완료 조합 렌더 실패')
  [
    ['--set', 'replicas=1'],
    ['--set', 'replicas=2'],
    positive + ['--set', 'receiverReviewed=false'],
    positive + ['--set', 'image.digest='],
    positive + ['--set', 'remoteWrite.url=http://monitoring.internal.invalid:9090/api/v1/write']
  ].each { |args| check(!render(*args)[2].success?, '실행 가드 우회 발견') }
  # Redis를 켜도 기존 RabbitMQ 수집 계약은 그대로 유지해야 함.
  redis_docs = objects('--set', 'redis.enabled=true')
  redis_config = redis_docs.find { |o| o['kind'] == 'ConfigMap' }.fetch('data').fetch('prometheus.yml')
  redis_jobs = YAML.safe_load(redis_config, aliases: true).fetch('scrape_configs')
  check(redis_jobs.take(4) == jobs, 'Redis 활성화로 기존 job 변경')
  check(redis_jobs.drop(4).map { |j| j['job_name'] } == %w[redis redis-sentinel], 'Redis job 범위 변경')
  redis_jobs.drop(4).each do |j|
    check(j['relabel_configs'].any? { |r| r['regex'] == 'v3-redis-exporter' && r['action'] == 'keep' },
          'exporter 외 Pod 수집')
  end
  exporter_chart = 'helm/charts/redis-exporter'
  exporter_render = lambda do |*args|
    Open3.capture3('helm', 'template', 'v3-redis-exporter', exporter_chart,
                  '--namespace', 'production', *args, chdir: ROOT)
  end
  exporter_out, _, exporter_status = exporter_render.call
  check(exporter_status.success?, 'Redis exporter 준비 렌더 실패')
  exporter_docs = YAML.load_stream(exporter_out).compact
  check(exporter_docs.group_by { |o| o['kind'] }.transform_values(&:length) == {'Deployment' => 7, 'NetworkPolicy' => 7},
        'Redis 업무 리소스/RBAC/Service 변경 또는 대상7개 누락')
  exporters = exporter_docs.select { |o| o['kind'] == 'Deployment' }
  check(exporters.all? { |o| o.dig('spec', 'replicas') == 0 }, 'exporter 자동 기동')
  exporters.each do |o|
    pod = o.dig('spec', 'template', 'spec')
    check(pod['automountServiceAccountToken'] == false, 'exporter Kubernetes 인증 부여')
    container = pod.fetch('containers').fetch(0)
    env = container.fetch('env')
    check(env.map { |e| e['name'] } == %w[REDIS_ADDR REDIS_PASSWORD], '불필요한 인증/환경변수 주입')
    ref = env[1].dig('valueFrom', 'secretKeyRef')
    check(%w[redis-auth redis-product redis-sentinel].include?(ref['name']) && ref['key'] == 'password',
          '검토되지 않은 Secret 참조')
    check(!env[0].fetch('value').include?('@'), 'Redis URL에 자격 포함')
    check(container.fetch('args').include?('--disable-exporting-key-values'), '키 값 노출 방지 누락')
    check(container.fetch('args').include?('--disable-scrape-endpoint'), '임의 대상 scrape endpoint 노출')
  end
  exporter_launch = ['--set', 'replicas=1', '--set', 'launchReviewed=true',
                     '--set', "image.digest=sha256:#{'1' * 64}"]
  check(exporter_render.call(*exporter_launch)[2].success?, 'exporter 승인 조합 거부')
  [ ['--set', 'replicas=1'], exporter_launch + ['--set', 'launchReviewed=false'],
    exporter_launch + ['--set', 'replicas=2'] ].each do |args|
    check(!exporter_render.call(*args)[2].success?, 'exporter 실행 가드 우회')
  end
  if ARGV.any?
    check(ARGV.length == 2 && ARGV[0] == '--write-config', '사용법: --write-config LOCAL_PATH')
    destination = File.expand_path(ARGV[1])
    FileUtils.mkdir_p(File.dirname(destination))
    File.write(destination, config)
    File.chmod(0600, destination)
  end
  puts 'MONITORING_PLAN_CHECK: resources=5 / initialReplicas=0 / Pod discovery read-only'
  puts 'QUEUE_FILTER_CHECK: payment=10 / profiling=4 / report=4 / detailed queue_metrics=5 DLQs only'
  puts 'LAUNCH_GUARD_CHECK: unreviewed / missing Digest / example URL / duplicate Agent rejected'
  puts 'REDIS_BOUNDARY_CHECK: 7 isolated exporters / no database resources or RBAC / optional jobs preserve existing scrapes'
  puts 'READ_ONLY_STATIC: 실제 수집·remote_write·Discord 성공은 미검증'
rescue StandardError => e
  warn e.message
  exit 1
end
