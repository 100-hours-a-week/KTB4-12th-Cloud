data "terraform_remote_state" "foundation" {
  backend = "s3"

  config = merge(
    {
      bucket = var.foundation_state_bucket
      key    = var.foundation_state_key
      region = var.foundation_state_region
    },
    var.foundation_state_profile == null ? {} : {
      profile = var.foundation_state_profile
    },
  )
}

data "aws_ssm_parameter" "ecs_optimized_ami" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id"
}
