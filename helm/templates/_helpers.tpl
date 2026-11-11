{{/*
Expand the name of the chart.
*/}}
{{- define "duty-scheduling.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "duty-scheduling.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart name and version for the chart label.
*/}}
{{- define "duty-scheduling.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "duty-scheduling.labels" -}}
helm.sh/chart: {{ include "duty-scheduling.chart" . }}
{{ include "duty-scheduling.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "duty-scheduling.selectorLabels" -}}
app.kubernetes.io/name: {{ include "duty-scheduling.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Service account name.
*/}}
{{- define "duty-scheduling.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "duty-scheduling.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Secret name: created by the chart, or an existing one supplied by the operator.
*/}}
{{- define "duty-scheduling.secretName" -}}
{{- if .Values.secrets.create }}
{{- default (printf "%s-secrets" (include "duty-scheduling.fullname" .)) .Values.secrets.name }}
{{- else }}
{{- required "secrets.name is required when secrets.create is false" .Values.secrets.name }}
{{- end }}
{{- end }}

{{/*
In-cluster MySQL Service name.
*/}}
{{- define "duty-scheduling.mysqlFullname" -}}
{{- printf "%s-mysql" (include "duty-scheduling.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Persistent claim name for /app/data.
*/}}
{{- define "duty-scheduling.dataClaimName" -}}
{{- default (printf "%s-data" (include "duty-scheduling.fullname" .)) .Values.persistence.existingClaim }}
{{- end }}

{{/*
MySQL persistent claim name.
*/}}
{{- define "duty-scheduling.mysqlClaimName" -}}
{{- default (printf "%s-mysql-data" (include "duty-scheduling.fullname" .)) .Values.mysql.persistence.existingClaim }}
{{- end }}
