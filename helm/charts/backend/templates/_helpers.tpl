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
