[CmdletBinding()]
param(
    [Alias('Home')][string]$InstallHome = 'D:\quant6',
    [switch]$PurgeSaves,
    [switch]$DryRun   # 只列出会删的任务 / 会结束的进程和当前任务差异，什么都不动
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

try {
    $beforePath = Join-Path $InstallHome 'install-before.txt'
    if (-not (Test-Path -LiteralPath $beforePath -PathType Leaf)) {
        throw "Missing pre-install snapshot: $beforePath; task cleanup cannot be verified."
    }

    if (Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue) {
        if ($DryRun) {
            Write-Output "DRYRUN would unregister $TaskPath$TaskName"
        } else {
            Stop-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Confirm:$false
        }
    }

    # venv 的 python(w).exe 是转发器，真解释器在 uv 的 Python 目录下，所以按命令行匹配；转发器和真解释器的命令行相同，
    # 两者都会被结束。只认 --home 恰好是本 Home、或 --saves 恰好是本 Home\saves 的 q6.sim 进程：
    # 原来按"命令行里含 Home 路径"匹配，会把 D:\quant6\scratch-* 下的其它运行也杀掉（Win 实测）。
    $homeFull = [IO.Path]::GetFullPath($InstallHome).TrimEnd('\')
    $savesFull = Join-Path $homeFull 'saves'
    function Test-OwnProcess([string]$CommandLine) {
        foreach ($m in [regex]::Matches($CommandLine, '--(home|saves)\s+(?:"([^"]+)"|(\S+))')) {
            $value = if ($m.Groups[2].Success) { $m.Groups[2].Value } else { $m.Groups[3].Value }
            $full = [IO.Path]::GetFullPath($value).TrimEnd('\')
            $want = if ($m.Groups[1].Value -eq 'home') { $homeFull } else { $savesFull }
            if ([string]::Equals($full, $want, [StringComparison]::OrdinalIgnoreCase)) { return $true }
        }
        return $false
    }
    $residual = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -in @('python.exe', 'pythonw.exe') -and $_.CommandLine -and
        $_.CommandLine -like '*q6.sim*' -and (Test-OwnProcess $_.CommandLine)
    })
    foreach ($process in $residual) {
        if ($DryRun) {
            Write-Output "DRYRUN would stop PID $($process.ProcessId): $($process.CommandLine)"
        } else {
            Write-Output "Stopping PID $($process.ProcessId): $($process.CommandLine)"
            Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }

    if ($PurgeSaves -and -not $DryRun) {
        $saves = Join-Path $InstallHome 'saves'
        if (Test-Path -LiteralPath $saves) { Remove-Item -LiteralPath $saves -Recurse -Force }
    }

    $after = @(Get-TaskNames)
    $expected = @(Get-Content -LiteralPath $beforePath | Where-Object { $_ } | Sort-Object -Unique)
    $diff = @(Compare-Object $expected $after)
    # install.ps1 只建 \Quant6Supervisor：名字不是 \Quant6* 的差异不可能是我们造成的（例如 Windows 自己的
    # \SoftLanding\... 任务会换 GUID，Win 实测）。照样列出来，但只有 \Quant6* 的差异判失败。
    $ours = @($diff | Where-Object { $_.InputObject -like '\Quant6*' })
    $external = @($diff | Where-Object { $_.InputObject -notlike '\Quant6*' })
    $external | ForEach-Object { Write-Output "EXTERNAL (not created by install.ps1) $($_.SideIndicator) $($_.InputObject)" }
    if ($DryRun) {
        $ours | ForEach-Object { Write-Output "DRYRUN current $($_.SideIndicator) $($_.InputObject)" }
        Write-Output 'UNINSTALL: DRYRUN (nothing changed)'
    } elseif ($ours.Count -eq 0) {
        Write-Output 'UNINSTALL: CLEAN'
    } else {
        Write-Output 'UNINSTALL: DIFF'
        $ours | ForEach-Object { Write-Output "$($_.SideIndicator) $($_.InputObject)" }
        exit 1
    }
} catch {
    Write-Error $_
    exit 1
}
