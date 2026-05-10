$start = Get-Date
$end = $start.AddHours(10)
$logDir = "G:\pure-ai-orchestrator\lingshu_full"

Write-Host "[WATCHDOG] Starting engine at $start, will run until $end"

while ((Get-Date) -lt $end) {
    $proc = Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'run_engine' }
    if (-not $proc) {
        Write-Host "[WATCHDOG] Engine not running, starting... $(Get-Date)"
        $env:PYTHONIOENCODING='utf-8'
        $p = Start-Process -NoNewWindow -FilePath py -ArgumentList @('-3.12', 'run_engine.py') -RedirectStandardOutput "$logDir\engine_stdout.log" -RedirectStandardError "$logDir\engine_stderr.log" -PassThru
        Write-Host "[WATCHDOG] Started PID: $($p.Id)"
    } else {
        Write-Host "[WATCHDOG] Engine running (PID $($proc.Id)), checking DB..."
    }
    
    if (Test-Path "$logDir\lingshu.db") {
        try {
            $r = py -3.12 -c "import sqlite3; c=sqlite3.connect('$logDir\lingshu.db'.replace('\\','/')); rows=c.execute('SELECT task_id, status FROM tasks').fetchall(); print('Tasks:', rows); chk=c.execute('SELECT COUNT(*) FROM checkpoints').fetchone(); print('Checkpoints:', chk[0]); c.close()"
            Write-Host $r
        } catch { }
    }
    
    Start-Sleep -Seconds 30
}

Write-Host "[WATCHDOG] 10 hours elapsed, stopping..."
Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'run_engine' } | Stop-Process -Force