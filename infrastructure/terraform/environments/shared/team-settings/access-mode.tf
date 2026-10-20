# 사용자 요청 시에만 설정 서비스 관리 권한으로 확장. 기본은 기존 팀별 최소권한.
# true면 운영/타팀/기존runtime/release Parameter까지 접근 가능하므로 Plan 검토 필수.
# false로 되돌리는 저장 Plan으로 기존 팀 경계 복구 가능.
variable "team_settings_admin_enabled" {
  type        = bool
  default     = false
  description = "MFA를 유지한 서울 Secrets Manager·Parameter Store 관리 권한. 전체 AWS/SSM 관리자 권한 아님."
}
