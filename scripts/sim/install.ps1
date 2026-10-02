[CmdletBinding()]
param(
    [Alias('Home')][string]$InstallHome = 'D:\quant6',
    [string]$Repo = 'D:\quant6\repo',
    [string]$Config = (Join-Path $Repo 'config\sim\default.toml'),
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
$TaskName = 'Quant6Supervisor'
$TaskPath = '\'

function Get-TaskNames {
    $lines = & schtasks.exe /query /fo csv /nh
    if ($LASTEXITCODE -ne 0) { throw "schtasks query failed with exit code $LASTEXITCODE" }
    @($lines | ConvertFrom-Csv -Header TaskName, NextRunTime, Status |
        ForEach-Object { ([string]$_.TaskName).Trim().Trim('"') } |
        Where-Object { $_ } | Sort-Object -Unique)
}

function Save-TaskSnapshot([string]$Path) {
    Get-TaskNames | Set-Content -LiteralPath $Path -Encoding UTF8
}

# 老板的账户本身是管理员组成员，OpenSSH 会话拿到的是提权令牌，所以这里不拒绝管理员会话；
# 要保证的是任务本身以非提权方式运行：RunLevel=Limited（UAC 过滤后的普通令牌）、LogonType=Interactive（不存密码）。
try {
    $pythonw = Join-Path $Repo '.venv\Scripts\pythonw.exe'
    if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) { throw "Missing pythonw: $pythonw" }
    if (-not (Test-Path -LiteralPath $Config -PathType Leaf)) { throw "Config file not found: $Config" }
    if (Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue) {
        throw "Task $TaskName already exists; run uninstall.ps1 first."
    }

    New-Item -ItemType Directory -Path $InstallHome -Force | Out-Null
    $before = Join-Path $InstallHome 'install-before.txt'
    $after = Join-Path $InstallHome 'install-after.txt'
    Save-TaskSnapshot $before

    $action = New-ScheduledTaskAction -Execute $pythonw `
        -Argument "-m q6.sim.supervisor --home `"$InstallHome`" --config `"$Config`"" `
        -WorkingDirectory $Repo
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType Interactive -RunLevel Limited
    $atLogon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes 5) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Action $action `
        -Principal $principal -Trigger @($atLogon, $repeat) -Settings $settings | Out-Null

    if (-not $NoStart) {
        Start-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
        Start-Sleep -Seconds 15
        $statusPath = Join-Path $InstallHome 'saves\supervisor.json'
        $status = $null
        if (Test-Path -LiteralPath $statusPath) {
            try { $status = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json } catch {}
        }
        $updated = $null
        if ($status -and $status.updated_at) {
            try { $updated = [DateTime]::Parse($status.updated_at) } catch {}
        }
        if (-not $updated -or ((Get-Date) - $updated).TotalSeconds -gt 30) {
            Write-Output 'FAIL: supervisor.json updated_at was not refreshed within 30 seconds.'
            $log = Join-Path $InstallHome 'saves\logs\supervisor.log'
            if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 40 }
            throw 'Supervisor startup health check failed; task is retained for diagnosis.'
        }
    }

    Save-TaskSnapshot $after
    $changes = @(Compare-Object (Get-Content -LiteralPath $before) (Get-Content -LiteralPath $after))
    $added = @($changes | Where-Object { $_.SideIndicator -eq '=>' } | ForEach-Object { $_.InputObject })
    $removed = @($changes | Where-Object { $_.SideIndicator -eq '<=' } | ForEach-Object { $_.InputObject })
    Write-Output "Added tasks: $($added -join ', ')"
    Write-Output "Removed tasks: $($removed -join ', ')"
    if ($added.Count -ne 1 -or $added[0] -ne '\Quant6Supervisor' -or $removed.Count -ne 0) {
        Write-Output 'FAIL: task snapshot difference must be exactly \Quant6Supervisor.'
        exit 1
    }
    Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath | Select-Object TaskName, State
    $p = (Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath).Principal
    $p | Select-Object UserId, LogonType, RunLevel
    if ([string]$p.LogonType -ne 'Interactive' -or [string]$p.RunLevel -ne 'Limited') {
        Write-Output 'FAIL: task must be LogonType=Interactive (no stored password) and RunLevel=Limited.'
        exit 1
    }
    Write-Output 'INSTALL: OK'
} catch {
    Write-Error $_
    exit 1
}
