# Shared setup for up.ps1 and down.ps1: finds Terraform and the AWS CLI.
# Uses whatever is on PATH; falls back to portable installs under E:\tools if present.

function Resolve-Tool {
    param([string]$Name, [string[]]$Fallbacks)
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($path in $Fallbacks) {
        if (Test-Path $path) { return $path }
    }
    throw "$Name not found. Install it and make sure it is on PATH."
}

$tf  = Resolve-Tool 'terraform' @('E:\tools\terraform\terraform.exe')
$aws = Resolve-Tool 'aws' @('E:\tools\aws.cmd')

# Optional: keep Terraform's plugin cache and temp files on E: when that layout exists.
if (Test-Path 'E:\tools\tf-plugin-cache') {
    $env:TF_PLUGIN_CACHE_DIR = 'E:\tools\tf-plugin-cache'
}
if (Test-Path 'E:\tools\tmp') {
    $env:TMP  = 'E:\tools\tmp'
    $env:TEMP = 'E:\tools\tmp'
}
