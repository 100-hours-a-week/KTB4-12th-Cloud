{{- define "backend.name" -}}
{{- required "서비스별 values에 name 필요" .Values.name -}}
{{- end -}}

{{- define "backend.selector" -}}
app.kubernetes.io/name: {{ include "backend.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "backend.labels" -}}
{{ include "backend.selector" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: seonjalal
app.kubernetes.io/component: {{ .Values.component }}
{{- end -}}

{{- define "backend.validate" -}}
{{- if .Values.monitoring.enabled -}}
{{- if or (ne .Values.component "main") (ne .Release.Namespace "production") (ne (int .Values.service.port) 8080) -}}
{{- fail "모니터링은 production Main 8080 서비스만 검토됨" -}}
{{- end -}}
{{- if not (has .Values.name (list "member-service" "catalog-service" "gifting-service" "recommendation-service" "notification-service")) -}}
{{- fail "모니터링 대상은 검토한 Main 5개 서비스만 허용" -}}
{{- end -}}
{{- if or (not .Values.monitoring.healthBridgeImageReviewed) (eq .Values.image.digest "sha256:4bff8ce0b6e372076f59a48280d7981ac95a7c01710ebbda9a7642e584832e11") -}}
{{- fail "기존 Health 경로 호환 수정이 포함된 새 Main Digest 확인 필요" -}}
{{- end -}}
{{- if hasKey .Values.env "SPRING_PROFILES_INCLUDE" -}}
{{- fail "기존 추가 프로필과 monitoring 병합 검토 필요" -}}
{{- end -}}
{{- range .Values.secretEnv -}}
{{- if eq .name "MONITORING_PASSWORD" -}}
{{- fail "모니터링 비밀번호 Secret 중복 주입 금지" -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if not (has .Values.component (list "main" "payment")) -}}
{{- fail "component는 main 또는 payment 필요" -}}
{{- end -}}
{{- if not (regexMatch "^sha256:[a-f0-9]{64}$" .Values.image.digest) -}}
{{- fail "image.digest는 sha256 Digest 전체 값 필요" -}}
{{- end -}}
{{- if not .Values.image.repository -}}
{{- fail "image.repository 필요" -}}
{{- end -}}
{{- if gt (int .Values.replicas) 0 -}}
{{- if not .Values.database.schemaReviewed -}}
{{- fail "앱 실행 전 기존 V2·새 V3의 DB 스키마 호환성 검토 필요" -}}
{{- end -}}
{{- if and (eq .Values.component "main") (not .Values.image.mainRuntimeCompatible) -}}
{{- fail "Main 실행 전 Sentinel 지원·STS 모듈이 반영된 새 이미지 확인 필요" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* ConfigMap과 Pod checksum이 동일한 최종 환경변수를 사용해야 함. */}}
{{- define "backend.runtimeEnv" -}}
{{- $env := deepCopy .Values.env -}}
{{- if .Values.monitoring.enabled -}}
{{- $env = mergeOverwrite $env (dict
  "SPRING_PROFILES_INCLUDE" "monitoring"
  "MONITORING_HEALTH_BRIDGE_ENABLED" "true"
  "MANAGEMENT_SERVER_ADDRESS" "0.0.0.0"
  "MANAGEMENT_SERVER_PORT" "8081"
  "MANAGEMENT_ENDPOINTS_WEB_EXPOSURE_INCLUDE" "health,prometheus"
  "HIKARI_MAX_POOL_SIZE" "5"
  "HIKARI_MIN_IDLE" "1") -}}
{{- end -}}
{{- toJson $env -}}
{{- end -}}
