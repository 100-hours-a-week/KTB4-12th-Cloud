locals {
  source_instance_id = "i-0c6f5ea09b5e20859"
  source_role_name   = "seonjalal-v1-develop-role"
  source_role_arn    = "arn:aws:iam::721744297924:role/seonjalal-v1-develop-role"
  task_arn           = "arn:aws:dms:ap-northeast-2:721744297924:task:OU5M6NHIS5BOHGL7KSXRWHCKNY"
}

# 기존 EC2/Role/Profile은 조회만. 독립 State에는 신규 정책 하나만 관리.
data "aws_instance" "source" {
  instance_id = local.source_instance_id
}

data "aws_iam_instance_profile" "source" {
  name = data.aws_instance.source.iam_instance_profile
}

resource "aws_iam_role_policy" "cutover_control" {
  name   = "SeonjalalRehearsalCutover20261019"
  role   = local.source_role_name
  policy = file("${path.module}/dms-policy.json")

  lifecycle {
    precondition {
      condition = (
        data.aws_instance.source.id == local.source_instance_id &&
        data.aws_instance.source.private_ip == "172.31.6.212" &&
        data.aws_instance.source.instance_state == "running" &&
        data.aws_iam_instance_profile.source.role_arn == local.source_role_arn
      )
      error_message = "확인된 비운영 Source의 실행 상태·IP·현재 연결 Role이 모두 일치해야 함."
    }
  }
}

output "cutover_control" {
  value = {
    role               = aws_iam_role_policy.cutover_control.role
    policy_name        = aws_iam_role_policy.cutover_control.name
    task_arn           = local.task_arn
    task_stop_executed = false
    note               = "Permission only. No Start/Modify/Delete, DB write or traffic switch."
  }
}
