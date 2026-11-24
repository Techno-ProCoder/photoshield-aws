# Deletes everything Terraform created (stops all charges). Terraform lists what
# it will delete and asks you to type "yes" first.
# Run:  powershell -ExecutionPolicy Bypass -File scripts\down.ps1
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'common.ps1')

Push-Location (Join-Path $root 'terraform')
try {
    & $tf destroy
    if ($LASTEXITCODE -ne 0) { throw 'terraform destroy failed or was cancelled' }
}
finally {
    Pop-Location
}
