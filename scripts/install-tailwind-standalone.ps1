[CmdletBinding()]
param()

$projectRoot = Split-Path -Path $PSScriptRoot -Parent
$toolDirectory = Join-Path $projectRoot "tools\tailwind"
$tailwindVersion = "v4.1.18"
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

$targetPath = Join-Path $toolDirectory $binaryName
$downloadUrl = "https://github.com/tailwindlabs/tailwindcss/releases/download/$tailwindVersion/$binaryName"

New-Item -ItemType Directory -Path $toolDirectory -Force | Out-Null

# 使用固定版本，避免多人协作时构建结果随上游最新版本漂移。
Invoke-WebRequest -Uri $downloadUrl -OutFile $targetPath
Write-Output "已下载 Tailwind standalone CLI 到 $targetPath"
