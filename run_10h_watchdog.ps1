param(
    [int]$Hours = 10,
    [string]$TasksDir = "tasks",
    [string]$Db = "lingshu.db"
)

$Python = "C:\Users\liyuanzhi\AppData\Local\Programs\Python\Python312\python.exe"
$WorkDir = "G:\pure-ai-orchestrator\lingshu_full"
$EndTime = (Get-Date).AddHours($Hours)
$RestartDelay = 5  # seconds

Write-Host "="*60
Write-Host "LINGSHU ENGINE WATCHDOG"
Write-Host "Started at: $(Get-Date)"
Write-Host "Will run until: $EndTime (${Hours}h)"
Write-Host "Tasks: $TasksDir"
Write-Host "="*60

while ((Get-Date) -lt $EndTime) {
    # Check if engine process is running
    $proc = Get-Process -Name python* -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -match "main engine"
    }
    
    if (-not $proc) {
        Write-Host "[$(Get-Date)] Engine not running, starting..." -ForegroundColor Yellow
        
        # Clear failed checkpoint so tasks can be retried
        if (Test-Path "$WorkDir\.lingshu\engine_checkpoint.json") {
            Remove-Item "$WorkDir\.lingshu\engine_checkpoint.json" -Force -ErrorAction SilentlyContinue
            Write-Host "[$(Get-Date)] Checkpoint cleared for fresh start" -ForegroundColor Cyan
        }
        
        # Start engine
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $Python
        $psi.Arguments = "-m main engine $Hours --dir $TasksDir --db $Db"
        $psi.WorkingDirectory = $WorkDir
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        
        $p = [System.Diagnostics.Process]::Start($psi)
        
        # Wait a moment then check
        Start-Sleep -Seconds 3
        if (-not $p.HasExited) {
            Write-Host "[$(Get-Date)] Engine started, PID: $($p.Id)" -ForegroundColor Green
        } else {
            Write-Host "[$(Get-Date)] Engine failed to start, retrying in ${RestartDelay}s..." -ForegroundColor Red
        }
    } else {
        $remaining = $EndTime - (Get-Date)
        Write-Host "[$(Get-Date)] Engine running (PID: $($proc.Id)), remaining: $([math]::Round($remaining.TotalHours, 1))h" -ForegroundColor Green
    }
    
    Start-Sleep -Seconds 30
}

Write-Host "="*60
Write-Host "10 hours completed at $(Get-Date)" -ForegroundColor Cyan
Write-Host "="*60
# Stop engine if still running
Get-Process -Name python* -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -match "main engine"
} | Stop-Process -Force -ErrorAction SilentlyContinue