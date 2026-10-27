variable "monitor_instance_type" {
  description = "모니터링 서버(Prometheus·Grafana·CloudWatch 수집기) 인스턴스 유형. 평시 정지 상태에서 변경하며 데이터(루트 볼륨)는 유지된다."
  type        = string
  default     = "t3.medium"
  validation {
    condition     = contains(["t3.medium", "t3.large"], var.monitor_instance_type)
    error_message = "모니터링 서버는 t3.medium 이상(t3.medium, t3.large)만 허용한다. 수집 대상과 팀 접속이 늘어 t3.small은 메모리(2GiB)가 부족하다."
  }
}
