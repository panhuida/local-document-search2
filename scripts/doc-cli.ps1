[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Arguments
)

# 基于脚本所在目录定位仓库根目录，避免依赖当前工作目录。
$projectRoot = Split-Path -Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
    & uv run python (Join-Path $projectRoot "doc-cli.py") @Arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
