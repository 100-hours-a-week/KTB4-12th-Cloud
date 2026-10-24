variable "aws_account_id" {
  type        = string
  description = "스테이징 대상 계정. Provider allowed_account_ids로 다른 계정 적용 차단"
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "12자리 AWS 계정 ID 필요."
  }
}
variable "aws_region" {
  type    = string
  default = "ap-northeast-2"
  validation {
    condition     = var.aws_region == "ap-northeast-2"
    error_message = "서울 리전 전용 시험."
  }
}
variable "aws_profile" {
  type    = string
  default = null
}
variable "ecs_optimized_ami_id" {
  type        = string
  default     = null
  description = "검증한 ECS 최적화 AMI 고정값. null은 초기 탐색용 권장 SSM 값 사용, 시험 중 업데이트는 별도 Plan으로 분리"
  validation {
    condition     = var.ecs_optimized_ami_id == null ? true : can(regex("^ami-[0-9a-f]{17}$", var.ecs_optimized_ami_id))
    error_message = "확인한 ECS AMI ID(ami- 및17자리16진수) 필요."
  }
}
variable "vpc_id" {
  type        = string
  description = "실제 조회로 선택할 VPC. 기존 VPC/Route/개발 State 변경 없음"
}
variable "secrets_endpoint_security_group_id" {
  type        = string
  default     = null
  description = "기존PrivateDNS Secrets Endpoint SG. 새스테이징SG로부터443만추가허용"
}
variable "database_security_group_id" {
  type        = string
  default     = null
  description = "기존리허설RDS SG. 새스테이징Task SG로부터3306만추가허용"
}
variable "app_subnet_ids" {
  type        = list(string)
  description = "Egress가 준비된 서로 다른 두 AZ의 Private App Subnet"
  validation {
    condition     = length(distinct(var.app_subnet_ids)) == 2
    error_message = "서로 다른 App Subnet 두 개 필요."
  }
}
variable "public_subnet_ids" {
  type        = list(string)
  description = "인터넷 ALB용 서로 다른 두 AZ의 Public Subnet"
  validation {
    condition     = length(distinct(var.public_subnet_ids)) == 2
    error_message = "서로 다른 Public Subnet 두 개 필요."
  }
}
variable "egress_probe_mode" {
  type        = bool
  default     = false
  description = "미검증 Egress 점검 전용: 앱/ALB/자동증설 없이 EC2 1대만 시험 기동. 통신 검증 완료를 의미하지 않음"
}
variable "enable_runtime_probe" {
  type        = bool
  default     = false
  description = "앱을 실행하지 않는 Secret/로그 점검 Task 정의 등록. 실행은 별도 사용자 명령/승인"
}
variable "enable_staging_certificate" {
  type        = bool
  default     = false
  description = "승인된 v2-staging 도메인의 ACM/DNS 검증만 준비. ALB/앱/트래픽 연결 없음"
}
variable "staging_public_zone_id" {
  type        = string
  default     = null
  description = "승인된 기존 Public Hosted Zone ID. 새 Zone 생성 없음"
  validation {
    condition     = !var.enable_staging_certificate || can(regex("^Z[A-Z0-9]+$", var.staging_public_zone_id))
    error_message = "인증서 DNS 검증에는 확인한 기존 Hosted Zone ID 필요."
  }
}
variable "private_egress_verified" {
  type        = bool
  default     = false
  description = "EC2와 Task의 ECS/ECR/Logs/SSM/Secrets 및 앱 의존성 통신 검증 후만 true"
}
variable "container_instance_types" {
  type    = list(string)
  default = ["t3.large"]
  validation {
    condition     = length(var.container_instance_types) > 0 && alltrue([for t in var.container_instance_types : startswith(t, "t3.")])
    error_message = "현재 AMI/Task 계약은 x86 t3 계열만 허용. ARM은 별도 변경 필요."
  }
}
variable "asg_min_size" {
  type    = number
  default = 0
}
variable "asg_desired_capacity" {
  type    = number
  default = 0
}
variable "asg_max_size" {
  type    = number
  default = 4
}
variable "root_volume_size_gib" {
  type    = number
  default = 30
}
variable "managed_scaling_enabled" {
  type    = bool
  default = false
}
variable "additional_tags" {
  type    = map(string)
  default = {}
}
variable "enable_alb" {
  type    = bool
  default = false
}
variable "alb_certificate_arn" {
  type        = string
  default     = null
  description = "시험 도메인에 유효한 서울 ACM 인증서 ARN"
}
variable "tailscale_router_instance_id" {
  type        = string
  default     = null
  description = "동일 VPC의 기존 Tailscale 진입 EC2. SNAT 기본값 유지 및 팀 전용 접근은 별도 검증"
  validation {
    condition     = var.tailscale_router_instance_id == null ? true : can(regex("^i-[0-9a-f]{17}$", var.tailscale_router_instance_id))
    error_message = "기존 Tailscale EC2의 정확한 Instance ID 필요."
  }
}
variable "allowed_client_ipv4_cidrs" {
  type        = set(string)
  default     = []
  description = "이전 공개 ALB 입력의 오사용 방지용. 팀 전용 구성에서는 비워둘 것"
  validation {
    condition     = length(var.allowed_client_ipv4_cidrs) == 0
    error_message = "공개 Client CIDR 방식 사용 금지. Tailscale 진입 EC2만 허용."
  }
}
variable "enable_service" {
  type    = bool
  default = false
}
variable "inject_startup_failure" {
  type        = bool
  default     = false
  description = "승인된 독립 Fixture 롤백 시험 전용. 신규 후보는 JVM/DB 접속 전에 exit78. tfvars 영구 저장 금지"
  validation {
    condition     = !var.inject_startup_failure || (var.database_fixture_verified && !var.database_cutover_verified)
    error_message = "실패 주입은 독립 Fixture에서만 허용. DB Cutover 환경에서는 금지."
  }
}
variable "backend_image" {
  type        = string
  default     = ""
  description = "같은 계정/리전의 이 Root ECR에 확보한 고정 @sha256 이미지"
}
variable "artifact_platform_verified" {
  type    = bool
  default = false
}
variable "database_cutover_verified" {
  type        = bool
  default     = false
  description = "최신 MySQL 데이터를 Target에 동기화/검증한 후만 true. 현재 오래된 RDS 직결 금지"
}
variable "database_fixture_verified" {
  type        = bool
  default     = false
  description = "독립 RDS 시험 데이터/TLS/스키마 기준을 확인한 경우만 true. 최신 Source Cutover 완료와 구분"
}
variable "rds_tls_bootstrap_verified" {
  type        = bool
  default     = false
  description = "고정 이미지에서 오프라인 CA 신뢰저장소 구성 검사를 통과한 경우 true. 실제 JDBC 검증과 구분"
}
variable "desired_task_count" {
  type    = number
  default = 0
  validation {
    condition = (
      var.desired_task_count == 0 ||
      (var.desired_task_count >= 2 && floor(var.desired_task_count) == var.desired_task_count)
    )
    error_message = "휴면 상태는 태스크0개, 활성 시험은 두 AZ 분산을 위해 최소 태스크2개 필요."
  }
}
variable "split_runtime_hosts_enabled" {
  type        = bool
  default     = false
  description = "true이면 옛 단일 백엔드 Task가 0이어도 ASG를 2대 이상 켤 수 있다. Main/Payment 분리 런타임(staging/be-split-runtime)이 이 Root의 호스트와 용량 공급자를 쓸 때만 사용"
}
variable "task_cpu" {
  type    = number
  default = 512
}
variable "task_memory_mib" {
  type    = number
  default = 1024
}
variable "container_environment" {
  type        = map(string)
  default     = {}
  description = "비밀이 아닌 DB_URL/DB_USERNAME/버킷명 등. 암호/토큰은 container_secrets 사용"
  validation {
    condition     = alltrue([for key in keys(var.container_environment) : !can(regex("(?i)(password|secret|token|credential|access_key)", key))])
    error_message = "비밀 값은 평문 환경변수가 아닌 Secrets Manager 참조 사용."
  }
}
variable "container_secrets" {
  type        = map(object({ secret_arn = string, json_key = string }))
  default     = {}
  description = "ENV명 → Secrets Manager 기본 ARN/JSON key. 실제 비밀 값 입력 금지"
  validation {
    condition     = alltrue([for r in values(var.container_secrets) : can(regex("^arn:aws:secretsmanager:ap-northeast-2:[0-9]{12}:secret:[^:]+$", r.secret_arn)) && can(regex("^[A-Za-z0-9_-]+$", r.json_key))])
    error_message = "서울 Secrets Manager 기본 ARN과 단순 JSON key 필요."
  }
}
variable "secret_kms_key_arns" {
  type        = set(string)
  default     = []
  description = "사용자 관리 KMS로 암호화한 Secret에 한해 지정"
}
variable "product_image_bucket_arn" {
  type    = string
  default = null
}
