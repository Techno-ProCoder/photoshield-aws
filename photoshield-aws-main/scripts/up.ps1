# Builds the whole stack with Terraform, waits until every web instance passes
# its load balancer health check, then opens the site.
# Run:  powershell -ExecutionPolicy Bypass -File scripts\up.ps1
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'common.ps1')

$start = Get-Date
Push-Location (Join-Path $root 'terraform')
try {
    & $tf init -input=false | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'terraform init failed' }
    & $tf apply -input=false -auto-approve
    if ($LASTEXITCODE -ne 0) { throw 'terraform apply failed' }
    $url = & $tf output -raw app_url
}
finally {
    Pop-Location
}

Write-Host 'Waiting for web instances to pass health checks...'
$tg = & $aws elbv2 describe-target-groups --names cloud-ha-tg --query 'TargetGroups[0].TargetGroupArn' --output text
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    $raw = & $aws elbv2 describe-target-health --target-group-arn $tg --query 'TargetHealthDescriptions[].TargetHealth.State' --output text
    $states = @(($raw -split '\s+') | Where-Object { $_ })
    $healthy = @($states | Where-Object { $_ -eq 'healthy' }).Count
    Write-Host ('  {0:HH:mm:ss}  healthy {1}/{2}' -f (Get-Date), $healthy, $states.Count)
    if ($states.Count -gt 0 -and $healthy -eq $states.Count) { $ready = $true; break }
    Start-Sleep -Seconds 15
}

$minutes = [math]::Round(((Get-Date) - $start).TotalMinutes, 1)
if ($ready) {
    Write-Host "Ready after $minutes min: $url"
    Start-Process $url
}
else {
    Write-Warning "Infrastructure is up but instances are not healthy after $minutes min. Check the target group in the EC2 console."
}
