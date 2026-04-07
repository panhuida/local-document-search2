[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string] $Command = "build",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Arguments
)

$projectRoot = Split-Path -Path $PSScriptRoot -Parent
$architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()

switch ($architecture) {
    "x64" {
        $binaryName = "tailwindcss-windows-x64.exe"
    }
    "arm64" {
        $binaryName = "tailwindcss-windows-arm64.exe"
    }
    default {
        Write-Error "当前 Windows 架构不受支持：$architecture"
        exit 1
    }
}

$binaryPath = Join-Path $projectRoot "tools\tailwind\$binaryName"
$inputPath = Join-Path $projectRoot "src\local_document_search\static_src\tailwind.css"
$outputPath = Join-Path $projectRoot "src\local_document_search\static\css\tailwind.css"

if (-not (Test-Path -LiteralPath $binaryPath)) {
    Write-Error "未找到 Tailwind standalone CLI：$binaryPath。请先运行 scripts/install-tailwind-standalone.ps1"
    exit 1
}

New-Item -ItemType Directory -Path (Split-Path -Path $outputPath -Parent) -Force | Out-Null

switch ($Command.ToLowerInvariant()) {
    "build" {
        $cliArguments = @("-i", $inputPath, "-o", $outputPath, "--minify") + $Arguments
    }
    "watch" {
        $cliArguments = @("-i", $inputPath, "-o", $outputPath, "--watch=always") + $Arguments
    }
    default {
        $cliArguments = @($Command) + $Arguments
    }
}

Push-Location $projectRoot
try {
    & $binaryPath @cliArguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
