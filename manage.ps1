# manage.ps1
[CmdletBinding()]
param(
    [ValidateSet("start", "stop", "restart", "status", "build")]
    [string]$Action = "start",
    [ValidateSet("all", "backend", "frontend")]
    [string]$Target = "all"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeRoot = Join-Path $ProjectRoot ".runtime"
$PidRoot = Join-Path $RuntimeRoot "pids"
$LogRoot = Join-Path $RuntimeRoot "logs"
$BackendRoot = Join-Path $ProjectRoot "backend"
$FrontendRoot = Join-Path $ProjectRoot "frontend"

New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
New-Item -ItemType Directory -Force -Path $PidRoot | Out-Null
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null

$BackendPidFile = Join-Path $PidRoot "backend.pid"
$FrontendPidFile = Join-Path $PidRoot "frontend.pid"
$BackendOutLog = Join-Path $LogRoot "backend.out.log"
$BackendErrLog = Join-Path $LogRoot "backend.err.log"
$FrontendOutLog = Join-Path $LogRoot "frontend.out.log"
$FrontendErrLog = Join-Path $LogRoot "frontend.err.log"

function Get-PidValue {
    param([string]$PidFile)
    if (-not (Test-Path $PidFile)) {
        return $null
    }
    $value = Get-Content $PidFile -Raw
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $null
    }
    return [int]$value.Trim()
}

function Test-Running {
    param([string]$PidFile)
    $processId = Get-PidValue -PidFile $PidFile
    if (-not $processId) {
        return $false
    }
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    return $null -ne $process
}

function Start-Backend {
    if (Test-Running -PidFile $BackendPidFile) {
        Write-Host "backend 已在运行"
        return
    }
    $process = Start-Process -FilePath "python" -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8100", "--reload") -WorkingDirectory $BackendRoot -RedirectStandardOutput $BackendOutLog -RedirectStandardError $BackendErrLog -PassThru
    Set-Content -Path $BackendPidFile -Value $process.Id
    Write-Host "backend 已启动，PID=$($process.Id)"
}

function Start-Frontend {
    if (Test-Running -PidFile $FrontendPidFile) {
        Write-Host "frontend 已在运行"
        return
    }
    $process = Start-Process -FilePath "npm.cmd" -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", "5178") -WorkingDirectory $FrontendRoot -RedirectStandardOutput $FrontendOutLog -RedirectStandardError $FrontendErrLog -PassThru
    Set-Content -Path $FrontendPidFile -Value $process.Id
    Write-Host "frontend 已启动，PID=$($process.Id)"
}

function Stop-ServiceProcess {
    param(
        [string]$Name,
        [string]$PidFile
    )
    $processId = Get-PidValue -PidFile $PidFile
    if (-not $processId) {
        Write-Host "$Name 未运行"
        return
    }
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $processId -Force
        Write-Host "$Name 已停止"
    } else {
        Write-Host "$Name 进程不存在，已清理 PID"
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Show-Status {
    $backendRunning = Test-Running -PidFile $BackendPidFile
    $frontendRunning = Test-Running -PidFile $FrontendPidFile
    if ($backendRunning) {
        Write-Host "backend: running"
    } else {
        Write-Host "backend: stopped"
    }
    if ($frontendRunning) {
        Write-Host "frontend: running"
    } else {
        Write-Host "frontend: stopped"
    }
    Write-Host "backend stdout: $BackendOutLog"
    Write-Host "backend stderr: $BackendErrLog"
    Write-Host "frontend stdout: $FrontendOutLog"
    Write-Host "frontend stderr: $FrontendErrLog"
}

function Build-Frontend {
    Push-Location $FrontendRoot
    try {
        npm run build
    } finally {
        Pop-Location
    }
}

switch ($Action) {
    "start" {
        if ($Target -in @("all", "backend")) { Start-Backend }
        if ($Target -in @("all", "frontend")) { Start-Frontend }
    }
    "stop" {
        if ($Target -in @("all", "frontend")) { Stop-ServiceProcess -Name "frontend" -PidFile $FrontendPidFile }
        if ($Target -in @("all", "backend")) { Stop-ServiceProcess -Name "backend" -PidFile $BackendPidFile }
    }
    "restart" {
        if ($Target -in @("all", "frontend")) { Stop-ServiceProcess -Name "frontend" -PidFile $FrontendPidFile }
        if ($Target -in @("all", "backend")) { Stop-ServiceProcess -Name "backend" -PidFile $BackendPidFile }
        if ($Target -in @("all", "backend")) { Start-Backend }
        if ($Target -in @("all", "frontend")) { Start-Frontend }
    }
    "status" {
        Show-Status
    }
    "build" {
        if ($Target -in @("all", "frontend")) { Build-Frontend }
    }
}
