{{- define "botgraph.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "botgraph.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/* selector labels for one component: include "botgraph.selector" (list . "api") */}}
{{- define "botgraph.selector" -}}
{{- $root := index . 0 -}}
app.kubernetes.io/name: {{ $root.Chart.Name }}
app.kubernetes.io/instance: {{ $root.Release.Name }}
app.kubernetes.io/component: {{ index . 1 }}
{{- end -}}

{{- define "botgraph.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}

{{- define "botgraph.secretName" -}}
{{- .Values.existingSecret | default (printf "%s-secrets" (include "botgraph.fullname" .)) -}}
{{- end -}}

{{- define "botgraph.host" -}}
{{- if .Values.ingress.tls.enabled -}}https{{- else -}}http{{- end -}}://{{ .Values.ingress.host }}
{{- end -}}

{{/* Environment shared by every Python container. */}}
{{- define "botgraph.env" -}}
- name: BOTGRAPH_ENV
  value: production
- name: BOTGRAPH_LOG_FORMAT
  value: {{ .Values.logFormat | quote }}
- name: BOTGRAPH_LOG_LEVEL
  value: {{ .Values.logLevel | quote }}
- name: BOTGRAPH_METRICS_PORT
  value: {{ .Values.metricsPort | quote }}
- name: BOTGRAPH_DB_URL
  valueFrom:
    secretKeyRef: { name: {{ include "botgraph.secretName" . }}, key: BOTGRAPH_DB_URL }
{{- end -}}

{{- define "botgraph.kafkaEnv" -}}
- name: BOTGRAPH_KAFKA_BOOTSTRAP
  value: {{ .Values.kafka.bootstrap | quote }}
{{- with .Values.kafka.securityProtocol }}
- name: BOTGRAPH_KAFKA_SECURITY_PROTOCOL
  value: {{ . | quote }}
{{- end }}
{{- with .Values.kafka.saslMechanism }}
- name: BOTGRAPH_KAFKA_SASL_MECHANISM
  value: {{ . | quote }}
- name: BOTGRAPH_KAFKA_SASL_USERNAME
  valueFrom:
    secretKeyRef: { name: {{ include "botgraph.secretName" $ }}, key: BOTGRAPH_KAFKA_SASL_USERNAME }
- name: BOTGRAPH_KAFKA_SASL_PASSWORD
  valueFrom:
    secretKeyRef: { name: {{ include "botgraph.secretName" $ }}, key: BOTGRAPH_KAFKA_SASL_PASSWORD }
{{- end }}
{{- end -}}

{{/* Writable scratch space: the root filesystem is read-only. */}}
{{- define "botgraph.scratchVolumes" -}}
- name: tmp
  emptyDir: {}
- name: data
  emptyDir: {}
{{- end -}}
{{- define "botgraph.scratchMounts" -}}
- { name: tmp, mountPath: /tmp }
- { name: data, mountPath: /app/data }
{{- end -}}

{{- define "botgraph.probes" -}}
livenessProbe:
  httpGet: { path: /healthz, port: metrics }
  periodSeconds: 20
  failureThreshold: 3
readinessProbe:
  httpGet: { path: /readyz, port: metrics }
  periodSeconds: 10
startupProbe:
  httpGet: { path: /healthz, port: metrics }
  periodSeconds: 5
  failureThreshold: 60
{{- end -}}

{{- define "botgraph.podDefaults" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets: {{- toYaml . | nindent 2 }}
{{- end }}
securityContext: {{- toYaml .Values.podSecurityContext | nindent 2 }}
automountServiceAccountToken: false
{{- with .Values.nodeSelector }}
nodeSelector: {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations: {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.affinity }}
affinity: {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}
