locals {
  prefix = "seonjalal-v2-${var.environment}"
  name   = "${local.prefix}-ai"
  roles  = toset(["app", "migrate", "catalog"])
  trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = var.aws_account_id }
        ArnLike      = { "aws:SourceArn" = "arn:aws:ecs:${var.aws_region}:${var.aws_account_id}:*" }
      }
    }]
  })
  secret_kind = { app = "runtime", migrate = "migration", catalog = "catalog" }
}

data "aws_subnet" "app" { id = var.config.subnet_id }
data "aws_security_group" "database_client" { id = var.config.database_client_security_group_id }
data "aws_security_group" "backend" { id = var.config.backend_security_group_id }
data "aws_security_group" "backend_export" { id = var.config.backend_export_security_group_id }

resource "terraform_data" "guard" {
  lifecycle {
    precondition {
      condition = (
        var.aws_region == "ap-northeast-2" &&
        data.aws_subnet.app.vpc_id == var.config.vpc_id &&
        data.aws_subnet.app.availability_zone == "ap-northeast-2a" &&
        !data.aws_subnet.app.map_public_ip_on_launch &&
        data.aws_security_group.database_client.vpc_id == var.config.vpc_id &&
        data.aws_security_group.backend.vpc_id == var.config.vpc_id &&
        data.aws_security_group.backend_export.vpc_id == var.config.vpc_id &&
        var.config.cluster_arn == "arn:aws:ecs:${var.aws_region}:${var.aws_account_id}:cluster/${local.prefix}-cluster" &&
        (var.environment == "production" ?
          var.config.capacity_provider == "${local.prefix}-ai" :
        startswith(var.config.capacity_provider, "${local.prefix}-"))
      )
      error_message = "서울 A Private Subnet·같은 VPC의 SG·해당 환경 ECS Cluster/Capacity Provider 필요. 운영 AI는 전용 AI Capacity Provider 필수."
    }
    precondition {
      condition = alltrue([for target, image in var.config.images :
        can(regex("^${var.aws_account_id}\\.dkr\\.ecr\\.${var.aws_region}\\.amazonaws\\.com/seonjalal/${var.environment}/ai-${target}@sha256:[0-9a-f]{64}$", image))
      ])
      error_message = "각 target은 해당 계정·서울·환경 AI ECR의 Digest 고정 Image만 허용."
    }
    precondition {
      condition     = var.config.desired_count == 0 || alltrue(values(var.config.activation_checks))
      error_message = "기동 전 DB Migration/Catalog/Network/Capacity/Runtime/Secret/Provider 비용 승인 확인 필요."
    }
    precondition {
      condition     = var.environment != "production" || var.config.catalog_bundle_s3 != null
      error_message = "첫 운영 Catalog 적재에는 검증된 S3 Bundle URI 필요."
    }
  }
}

resource "aws_cloudwatch_log_group" "ai" {
  name              = "/ecs/${local.name}"
  retention_in_days = var.environment == "production" ? 30 : 7
}

# 값은 별도 안전한 bootstrap으로 기록. Terraform에는 Secret Metadata만 저장한다.
resource "aws_secretsmanager_secret" "runtime" {
  for_each                = local.roles
  name                    = "seonjalal/${var.environment}/ai/${local.secret_kind[each.key]}"
  recovery_window_in_days = 30
  lifecycle { prevent_destroy = true }
}

resource "aws_iam_role" "execution" {
  for_each           = local.roles
  name               = "${local.name}-${each.key}-execution"
  assume_role_policy = local.trust
}

resource "aws_iam_role_policy_attachment" "execution" {
  for_each   = local.roles
  role       = aws_iam_role.execution[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "secret_injection" {
  for_each = local.roles
  role     = aws_iam_role.execution[each.key].name
  name     = "InjectOnlyOwnAiSecret"
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = aws_secretsmanager_secret.runtime[each.key].arn }]
  })
}

resource "aws_iam_role" "task" {
  for_each           = local.roles
  name               = "${local.name}-${each.key}-task"
  assume_role_policy = local.trust
}

# Init 컨테이너와 Catalog Job만 모델/미디어를 읽는다. DB 관리자 Secret 권한 없음.
resource "aws_iam_role_policy" "assets" {
  for_each = toset(["app", "catalog"])
  role     = aws_iam_role.task[each.key].name
  name     = "ReadOnlyVerifiedAiAssets"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Action = ["s3:GetObject"], Resource = "arn:aws:s3:::${var.config.asset_bucket}/${var.config.asset_prefix}*"
    }]
  })
}

resource "aws_security_group" "task" {
  name        = "${local.name}-runtime"
  description = "AI runtime; only backend can call Chat and Profiler"
  vpc_id      = var.config.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "from_backend" {
  for_each                     = { chat = 7860, profiler = 8001 }
  security_group_id            = aws_security_group.task.id
  referenced_security_group_id = var.config.backend_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = each.value
  to_port                      = each.value
}

resource "aws_vpc_security_group_egress_rule" "backend_to_ai" {
  for_each                     = { chat = 7860, profiler = 8001 }
  security_group_id            = var.config.backend_security_group_id
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = each.value
  to_port                      = each.value
}

resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.task.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "callback" {
  security_group_id            = aws_security_group.task.id
  referenced_security_group_id = var.config.backend_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}

resource "aws_vpc_security_group_ingress_rule" "backend_callback" {
  security_group_id            = var.config.backend_security_group_id
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}

# 일회성 Task는 Service Connect 주소 대신 검증된 Export HTTPS 진입점을 사용한다.
resource "aws_vpc_security_group_ingress_rule" "backend_export" {
  security_group_id            = var.config.backend_export_security_group_id
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

# BE Main/Payment도 같은 Namespace를 사용하도록 후속 Root에서 이 ARN을 참조한다.
resource "aws_service_discovery_http_namespace" "services" {
  name = "${local.prefix}-services"
}
