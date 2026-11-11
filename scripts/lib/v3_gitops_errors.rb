# 외부 CLI stderr를 출력하지 않고 고정된 진단 메시지로 변환합니다.
module V3GitopsErrors
  def self.describe(error)
    case error.to_s
    when /SSO.*(?:expired|invalid)|(?:expired|invalid).*SSO|Error loading SSO Token|Token.*does not exist/i
      '원인 분류=AWS_SSO_SESSION / AWS SSO 로그인 갱신 필요'
    when /ExpiredToken|InvalidClientTokenId|security token.*(?:expired|invalid)|Unable to locate credentials|NoCredentialProviders|failed to refresh cached credentials/i
      '원인 분류=AWS_CREDENTIALS / AWS 인증 만료·누락 또는 자격증명 갱신 실패'
    when /config profile.*could not be found|ProfileNotFound|AWS_PROFILE.*not found/i
      '원인 분류=AWS_PROFILE / kubeconfig 실행 환경의 AWS 프로필 설정 확인 필요'
    when /context.*does not exist|context.*not found|no context exists/i
      '원인 분류=KUBE_CONTEXT / seonjalal-v3 context 등록 여부 확인 필요'
    when /Forbidden|forbidden|AccessDenied|not authorized to perform/i
      '원인 분류=ACCESS_DENIED / AWS 또는 Kubernetes 조회 권한 확인 필요'
    when /Unauthorized|provide credentials|must be logged in|getting credentials|exec.*(?:failed|exit code)/i
      '원인 분류=KUBE_AUTH / kubeconfig의 AWS 인증 실행 또는 Kubernetes 인증 확인 필요'
    when /release.*not found|Release not loaded/i
      '원인 분류=HELM_RELEASE_NOT_FOUND / production의 저장 Release 존재·Helm 저장 방식 확인 필요'
    when /unknown flag|unknown command|flag provided but not defined/i
      '원인 분류=CLI_OPTIONS / 설치된 Helm·kubectl의 명령 옵션 호환성 확인 필요'
    when /i\/o timeout|timed out|deadline exceeded|no such host|network is unreachable|connection refused|TLS handshake timeout|dial tcp/i
      '원인 분류=API_NETWORK / VPN·Private API DNS·TCP 443 연결 확인 필요'
    when /certificate signed by unknown authority|x509:|certificate.*expired/i
      '원인 분류=API_TLS / kubeconfig의 API 인증서와 접속 경로 확인 필요'
    else
      '원인 분류=UNKNOWN / 알려진 오류 패턴 아님. 실패 원문은 로컬에서 확인 후 민감정보를 가린 요약만 공유 필요'
    end
  end
end
