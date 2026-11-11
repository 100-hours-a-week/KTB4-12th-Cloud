#!/usr/bin/env ruby
# 로컬 Helm 렌더·배포 경계 검사. --live 지정 때만 사용자가 읽기 전용 클러스터 조회 수행.
require 'yaml'
require 'json'
require 'open3'
require 'pathname'
require_relative 'lib/v3_gitops_errors'

ROOT = File.expand_path('..', __dir__)
MAIN = %w[member-service catalog-service gifting-service recommendation-service notification-service].freeze
EXPECTED = (['payment-server'] + MAIN + ['ai-runtime']).freeze
ALLOWED = %w[Deployment ConfigMap Service PodDisruptionBudget].freeze
CONTEXT = 'seonjalal-v3'.freeze
NAMESPACE = 'production'.freeze

def run(*args)
  output, error, status = Open3.capture3(*args, chdir: ROOT)
  # 원문 대신 허용된 분류·고정 대상만 표시해 인증 오류도 비밀값 없이 구분합니다.
  unless status.success?
    target = args.first == 'helm' && args[1, 2] == ['get', 'manifest'] ? args[3] : nil
    target = nil unless EXPECTED.include?(target)
    diagnosis = V3GitopsErrors.describe(error)
    raise "#{args.first} #{args[1]} 실패(exit=#{status.exitstatus})#{target ? " / 대상=#{target}" : ''}\n#{diagnosis}\n오류 원문·비밀값 미출력. 서버 변경 없음."
  end
  output
end

def docs(raw)
  YAML.load_stream(raw).compact
end

def require_state(condition, message)
  raise message unless condition
end

def identity(object)
  [object.fetch('apiVersion'), object.fetch('kind'),
   object.fetch('metadata').fetch('namespace', NAMESPACE), object.fetch('metadata').fetch('name')]
end

# 원문 값은 출력하지 않고 차이의 필드 경로만 남깁니다.
def differences(wanted, actual, path = '$')
  if wanted.is_a?(Hash) && actual.is_a?(Hash)
    (wanted.keys | actual.keys).flat_map { |key| differences(wanted[key], actual[key], "#{path}.#{key}") }
  elsif wanted.is_a?(Array) && actual.is_a?(Array)
    (0...[wanted.length, actual.length].max).flat_map { |i| differences(wanted[i], actual[i], "#{path}[#{i}]") }
  else
    wanted == actual ? [] : [path]
  end
end

begin
  require_state((ARGV - ['--live']).empty?, '사용법: ruby scripts/validate-v3-gitops.rb [--live]')
  values = YAML.load_file(File.join(ROOT, 'argocd/charts/production-apps/values.yaml'))
  source = values.fetch('applications')
  require_state(source.map { |a| a['name'] }.sort == EXPECTED.sort, '관리 대상 7개 불일치')
  wrapper = 'argocd/charts/production-apps'
  rendered = docs(run('helm', 'template', 'v3-production-apps', wrapper, '--namespace', 'argocd'))
  project = rendered.find { |d| d['kind'] == 'AppProject' }.fetch('spec')
  require_state(project['clusterResourceWhitelist'] == [], '클러스터 리소스 관리 금지 필요')
  require_state(project['namespaceResourceWhitelist'].map { |r| r['kind'] }.sort == ALLOWED.sort, 'Project 리소스 범위 초과')
  require_state(project['destinations'] == [{'server' => 'https://kubernetes.default.svc', 'namespace' => NAMESPACE}], '목적지 범위 불일치')
  applications = rendered.select { |d| d['kind'] == 'Application' }
  require_state(applications.length == 7, 'Application 수 불일치')
  all_objects = []
  source.each do |app|
    name = app.fetch('name')
    definition = applications.find { |d| d.dig('metadata', 'name') == name }
    require_state(definition.dig('metadata', 'finalizers').to_a.empty?, "#{name}: 연쇄 삭제 finalizer 금지")
    spec = definition.fetch('spec')
    require_state(spec.dig('source', 'helm', 'releaseName') == name, "#{name}: 기존 Release 이름 유지 필요")
    require_state(spec.dig('source', 'helm', 'valueFiles') == app['valueFiles'], "#{name}: values 순서 불일치")
    auto = spec.dig('syncPolicy', 'automated')
    require_state(auto['prune'] == false && auto['allowEmpty'] == false, "#{name}: 자동 삭제 금지 필요")
    require_state(auto['enabled'] == values['automatedSync'] && auto['selfHeal'] == values['automatedSync'], "#{name}: 동기화 모드 불일치")
    options = spec.dig('syncPolicy', 'syncOptions')
    require_state(options == ['FailOnSharedResource=true'], "#{name}: 파괴적 Sync 옵션 또는 소유권 경계 변경")
    chart = app.fetch('chartPath')
    files = app.fetch('valueFiles').map { |f| Pathname.new(File.join(chart, f)).cleanpath.to_s }
    args = files.flat_map { |f| ['--values', f] }
    run('helm', 'lint', chart, *args)
    objects = docs(run('helm', 'template', name, chart, '--namespace', NAMESPACE, *args))
    require_state(objects.all? { |o| ALLOWED.include?(o['kind']) }, "#{name}: 데이터·Secret·RBAC 등 범위 밖 리소스 포함")
    deployment = objects.find { |o| o['kind'] == 'Deployment' }
    require_state(deployment.dig('spec', 'replicas') == 1, "#{name}: 기존 1개 실행 수 유지 필요")
    selector = deployment.dig('spec', 'selector', 'matchLabels')
    require_state(selector['app.kubernetes.io/instance'] == name, "#{name}: 기존 Pod selector 불일치")
    services = objects.select { |o| o['kind'] == 'Service' }
    require_state(services.all? { |s| s.dig('spec', 'selector') == selector }, "#{name}: Service와 Pod selector 불일치")
    containers = deployment.dig('spec', 'template', 'spec', 'containers')
    require_state(containers.all? { |c| c['image'].match?(/@sha256:[0-9a-f]{64}\z/) }, "#{name}: Image Digest 고정 필요")
    if MAIN.include?(name)
      env = objects.find { |o| o['kind'] == 'ConfigMap' && o.dig('metadata', 'name') == "#{name}-runtime" }.fetch('data')
      %w[SCHEDULING_ENABLED AI_PROFILE_AMQP_ENABLED AI_PROFILE_SCHEDULING_ENABLED AI_PROFILE_RESULT_CONSUMER_ENABLED].each do |flag|
        expected = name == 'recommendation-service' ? 'true' : 'false'
        require_state(env[flag] == expected, "#{name}: #{flag} 실행 주체 변경")
      end
    elsif name == 'ai-runtime'
      require_state(containers.map { |c| c['name'] }.sort == %w[chat profiler search], 'AI 통합 컨테이너 구성 변경')
      strategy = deployment.dig('spec', 'strategy', 'rollingUpdate')
      require_state(strategy == {'maxSurge' => 0, 'maxUnavailable' => 1}, 'AI Search 단일 실행 교체 조건 변경')
    end
    if ARGV.include?('--live')
      # helm get은 기존 수동 Release의 저장 manifest만 조회하며 서버를 변경하지 않습니다.
      previous = docs(run('helm', 'get', 'manifest', name, '--kube-context', CONTEXT, '--namespace', NAMESPACE))
      old = previous.each_with_object({}) { |o, h| h[identity(o)] = o }
      wanted = objects.each_with_object({}) { |o, h| h[identity(o)] = o }
      changed = (old.keys | wanted.keys).flat_map { |key| differences(wanted[key], old[key], key.join('/')) }
      # 실서버는 마지막 Helm 값 이후 kubectl 등으로 변경됐을 수 있으므로 Diff에서 추가 확인 필요.
      live = JSON.parse(run('kubectl', '--context', CONTEXT, '--namespace', NAMESPACE,
                            'get', 'deployment', name, '--output', 'json', '--request-timeout=15s'))
      require_state(live.dig('spec', 'replicas') == 1 && live.dig('status', 'availableReplicas').to_i >= 1, "#{name}: 실배포 1개 Available 확인 필요")
      unless changed.empty?
        puts JSON.pretty_generate('release' => name, 'changedPaths' => changed)
        raise "#{name}: 기존 Helm manifest와 차이 발견; Sync 전 검토 필요"
      end
    end
    all_objects.concat(objects)
    puts "#{name}: 렌더·replicas=1·Digest·selector·실행 경계 정상#{ARGV.include?('--live') ? ' / 저장 Helm manifest 동일' : ''}"
  end
  require_state(all_objects.map { |o| identity(o) }.uniq.length == all_objects.length, 'Application 간 공유 리소스 중복')
  # 미래 자동 동기화 모드도 렌더하며, 작업 브랜치에서 자동화하는 잘못된 조합은 거부합니다.
  run('helm', 'template', 'v3-production-apps', wrapper, '--namespace', 'argocd', '--set', 'revision=develop', '--set', 'automatedSync=true')
  _out, _err, bad = Open3.capture3('helm', 'template', 'v3-production-apps', wrapper,
                                '--set', 'revision=feat/v3-argocd', '--set', 'automatedSync=true', chdir: ROOT)
  require_state(!bad.success?, '작업 브랜치 자동 동기화 차단 실패')
  puts "GITOPS_CHECK_FINISHED: Applications=7 / managedResources=#{all_objects.length} / liveRead=#{ARGV.include?('--live')}"
rescue StandardError => e
  # 프로그램 자체의 예외 메시지에는 파싱 중 원문이 포함될 수 있어 외부 명령 이외도 주의합니다.
  warn e.message
  exit 1
end
