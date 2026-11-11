{{- define "ai.labels" -}}
app.kubernetes.io/name: {{ .Values.name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "ai.validate" -}}
{{- if empty .Values.assets.bucket -}}{{- fail "assets.bucket is required" -}}{{- end -}}
{{- if not (regexMatch "^ai/production/[0-9a-f]{40}/$" .Values.assets.prefix) -}}{{- fail "assets.prefix requires a pinned production asset bundle" -}}{{- end -}}
{{- if not (regexMatch "^[0-9a-f]{64}$" .Values.assets.manifestSha256) -}}{{- fail "assets.manifestSha256 is required" -}}{{- end -}}
{{- if or (lt (int .Values.replicas) 0) (gt (int .Values.replicas) 1) -}}{{- fail "Initial AI rollout supports replicas=0 or 1 only; review total MQ concurrency before scaling" -}}{{- end -}}
{{- if or (ne (int .Values.strategy.rollingUpdate.maxSurge) 0) (ne (int .Values.strategy.rollingUpdate.maxUnavailable) 1) -}}{{- fail "Initial rollout requires maxSurge=0/maxUnavailable=1 to bound MQ concurrency" -}}{{- end -}}
{{- range $role, $digest := .Values.images -}}
{{- if not (regexMatch "^sha256:[0-9a-f]{64}$" $digest) -}}
{{- fail (printf "images.%s requires a pinned SHA256 digest" $role) -}}
{{- end -}}
{{- end -}}
{{- if gt (int .Values.replicas) 0 -}}
{{- if not .Values.launchApproved -}}{{- fail "launchApproved=true requires operator review before starting AI" -}}{{- end -}}
{{- if not (has .Values.profiler.tagEngine (list "v2" "v3")) -}}{{- fail "profiler.tagEngine must explicitly select v2 or v3" -}}{{- end -}}
{{- if and (eq .Values.profiler.tagEngine "v3") (empty .Values.profiler.modalSecretName) -}}{{- fail "Modal v3 requires profiler.modalSecretName" -}}{{- end -}}
{{- if ne (toString .Values.profiler.env.PROFILING_MQ_ENABLED) "true" -}}{{- fail "This AI release requires explicit MQ activation" -}}{{- end -}}
{{- $slots := int .Values.profiler.env.PROFILING_MQ_SLOTS -}}
{{- if or (lt $slots 1) (gt $slots 8) -}}{{- fail "Review the initial MQ slot budget: allowed range is 1..8" -}}{{- end -}}
{{- if lt (int .Values.profiler.env.PROFILING_MQ_PREFETCH) $slots -}}{{- fail "MQ prefetch must be at least the slot count" -}}{{- end -}}
{{- end -}}
{{- range $role := list "search" "profiler" "chat" -}}
{{- $settings := index $.Values $role -}}
{{- if or (hasKey $.Values.env "PORT") (hasKey $settings.env "PORT") -}}{{- fail "PORT is owned by the chart" -}}{{- end -}}
{{- if or (hasKey $.Values.env "PROFILING_TAG_ENGINE") (hasKey $settings.env "PROFILING_TAG_ENGINE") -}}{{- fail "Use profiler.tagEngine instead of an env override" -}}{{- end -}}
{{- end -}}
{{- end -}}
