# LINGSHU Eternal Watchdog
# Auto-restarts engine every 5 min check
# Only stops when STOP_ETERNAL file exists

param(
    [int]$CheckSec = 300,
    [int]$RetrySec = 5,
    [string]$WD = "G:\pure-ai-orchestrator\lingshu_full"
)

$ErrorActionPreference = "Continue"
$SFile = "$WD\.lingshu\STOP_ETERNAL"
$LogFile = "$WD\.lingshu\eternal_watchdog.log"
$PidFile = "$WD\.lingshu\watchdog.pid"
$EpFile = "$WD\.lingshu\engine.pid"

mkdir "$WD\.lingshu" -Force -ErrorAction SilentlyContinue | Out-Null
$MyPid = [Diagnostics.Process]::GetCurrentProcess().Id
$StartTime = Get-Date
[IO.File]::WriteAllText($PidFile, "$MyPid")

function Log($m, $l = "INFO") {
    $t = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$t][$l] $m"
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch {}
    Write-Host $line
}

function StopSig { return (Test-Path $SFile) }

function GetHrs {
    return [Math]::Round(((Get-Date)-$StartTime).TotalHours, 1)
}

function StartEng {
    Log "Starting engine..." "ACTION"
    try {
        $cp = "$WD\.lingshu\engine_checkpoint.json"
        if (Test-Path $cp) { Remove-Item $cp -Force -ErrorAction SilentlyContinue }
        $args = "/c cd /d ""$WD"" && py -3.12 main.py engine 99999 --dir tasks --db lingshu.db > .lingshu\engine_out.log 2>&1"
        $p = Start-Process cmd -ArgumentList $args -WorkingDirectory $WD -NoNewWindow -PassThru -RedirectStandardOutput "$WD\.lingshu\tmp_stdout.log" -RedirectStandardError "$WD\.lingshu\tmp_stderr.log"
        Start-Sleep -Seconds 3
        if (!$p.HasExited) {
            [IO.File]::WriteAllText($EpFile, "$($p.Id)")
            Log "Engine started PID=$($p.Id) watchdog=$MyPid" "OK"
            return $true
        } else {
            Log "Engine exited quickly code=$($p.ExitCode)" "WARN"
            return $false
        }
    } catch {
        Log "StartEngine exception: $_" "ERROR"
        return $false
    }
}

function IsRunning {
    if (Test-Path $EpFile) {
        $epId = (Get-Content $EpFile -Raw).Trim()
        if ($epId -match "^\d+$") {
            if (Get-Process -Id $epId -ErrorAction SilentlyContinue) { return $true }
        }
    }
    $procs = Get-Process python* -ErrorAction SilentlyContinue | Where-Object { ($_.CommandLine -join " ") -match "main\.py.*engine" }
    if ($procs) {
        [IO.File]::WriteAllText($EpFile, "$($procs[0].Id)")
        return $true
    }
    return $false
}

Log "========================================"
Log "ETERNAL WATCHDOG v1.0"
Log "PID: $MyPid"
Log "CWD: $WD"
Log "Interval: ${CheckSec}s ($([Math]::Round($CheckSec/60))min)" 
Log "Stop signal: $SFile"
Log "========================================"
Log ""
Log "RULES: Never stop unless STOP_ETERNAL exists"
Log ""

$first = $true
$failCount = 0

while ($true) {
    if (StopSig) {
        Log "STOP signal detected, exiting." "STOP"
        try { Remove-Item $SFile -Force -ErrorAction SilentlyContinue } catch {}
        break
    }
    $hrs = GetHrs
    $run = IsRunning
    if ($run) {
        if ($first) { Log "Engine is running." "OK"; $first = $false }
        $failCount = 0
        Log "[${hrs}h] Engine alive"
    } else {
        Log "Engine DOWN. Restarting..." "WARN"
        $ok = $false
        for ($i = 1; $i -le 5; $i++) {
            if (StopSig) { break }
            Log "Restart attempt #$i" "ACTION"
            $ok = StartEng
            if ($ok) { Log "Restart OK (#$i)" "OK"; $failCount = 0; break }
            Log "Restart #$i failed, retry in ${RetrySec}s" "WARN"
            Start-Sleep -Seconds $RetrySec
        }
        if (!$ok) {
            $failCount++
            Log "${failCount} consecutive restart failures" "ERROR"
        }
    }
    $elapsed = 0
    while ($elapsed -lt $CheckSec) {
        if (StopSig) { break }
        if ($elapsed % 60 -eq 0 -and $elapsed -gt 0) {
            $s = if (IsRunning) { "alive" } else { "dead" }
            Log "[HB ${hrs}h] engine=$s" "HEARTBEAT"
        }
        Start-Sleep -Seconds 10
        $elapsed += 10
        if ($run -and !(IsRunning)) {
            Log "Engine crashed during wait!" "CRASH"
            break
        }
    }
}

Log "========================================"
Log "STOPPED at $(Get-Date)" 
Log "Ran: $(GetHrs)h"
Log "========================================"
if (Test-Path $PidFile) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue }