param(
    [Alias('Home')][string]$RootHome = 'D:\quant6'
)

$ErrorActionPreference = 'Stop'
$failed = New-Object System.Collections.Generic.List[string]

function Get-TreeBytes([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return [int64]0 }
    $total = [int64]0
    Get-ChildItem -LiteralPath $Path -Recurse -Force -File -ErrorAction SilentlyContinue | ForEach-Object {
        $total += [int64]$_.Length
    }
    return $total
}

function Show-SizeCheck([string]$Label, [string]$Path, [double]$LimitGb) {
    $bytes = Get-TreeBytes $Path
    $gb = [math]::Round(($bytes / 1GB), 3)
    $limitBytes = [int64]($LimitGb * 1GB)
    $state = if ($bytes -le $limitBytes) { 'PASS' } else { 'FAIL' }
    Write-Output ("{0}: {1} GB / {2} GB {3}" -f $Label, $gb, $LimitGb, $state)
    if ($state -eq 'FAIL') { $script:failed.Add("$Label exceeds $LimitGb GB") }
}

try {
    Show-SizeCheck 'D:\quant6 total' $RootHome 4.0
    Show-SizeCheck 'D:\quant6\saves' (Join-Path $RootHome 'saves') 2.0

    # Keep this file ASCII-only: PS 5.1 reads BOM-less .ps1 as ANSI (GBK), and a non-ASCII comment can swallow the next line.
    # InJob: the 512 MB hard limit is applied by the supervisor's Job Object; children are born inside it.
    $k32 = Add-Type -PassThru -Namespace Q6 -Name K32 -MemberDefinition '[DllImport("kernel32.dll")] public static extern bool IsProcessInJob(IntPtr p, IntPtr j, out bool r);'
    Write-Output 'q6.sim Python processes:'
    $processes = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -match 'q6\.sim' })
    if ($processes.Count -eq 0) {
        Write-Output '(none)'
    }
    foreach ($item in $processes) {
        $proc = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
        if ($null -eq $proc) { continue }
        $summary = [string]$item.CommandLine
        if ($summary.Length -gt 180) { $summary = $summary.Substring(0, 177) + '...' }
        $working = [math]::Round(($proc.WorkingSet64 / 1MB), 1)
        $peak = [math]::Round(($proc.PeakWorkingSet64 / 1MB), 1)
        $private = [math]::Round(($proc.PrivateMemorySize64 / 1MB), 1)
        $inJob = $false
        $null = $k32::IsProcessInJob($proc.Handle, [IntPtr]::Zero, [ref]$inJob)
        Write-Output ("PID={0} Command={1} WorkingSet64={2}MB PeakWorkingSet64={3}MB PrivateMemorySize64={4}MB InJob={5}" -f $proc.Id, $summary, $working, $peak, $private, $inJob)
        if (-not $inJob) { $failed.Add("PID $($proc.Id) is not in a Job Object") }
        if ($peak -gt 512) { $failed.Add("PID $($proc.Id) peak working set exceeds 512 MB") }
    }

    Write-Output 'Quant6 scheduled tasks:'
    $quantTasks = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -like 'Quant6*' })
    if ($quantTasks.Count -eq 0) { Write-Output '(none)' }
    foreach ($task in $quantTasks) {
        # State lives on the Get-ScheduledTask object; Get-ScheduledTaskInfo has no State (column used to be empty).
        Write-Output ("{0}{1} State={2}" -f $task.TaskPath, $task.TaskName, $task.State)
    }

    $nonMicrosoft = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskPath -notlike '\Microsoft\*' })
    Write-Output ("Non-Microsoft scheduled task count: {0}" -f $nonMicrosoft.Count)
} catch {
    $failed.Add($_.Exception.Message)
}

if ($failed.Count -eq 0) {
    Write-Output 'CHECK_LIMITS: PASS'
    exit 0
}
$reason = $failed -join '; '
Write-Output ("CHECK_LIMITS: FAIL ({0})" -f $reason)
exit 1
