[CmdletBinding()]
param(
    [Alias('Home')][string]$InstallHome = 'D:\quant6',
    [switch]$PurgeSaves
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
        Stop-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Confirm:$false
    }

    # venv 的 python(w).exe 是转发器，真解释器在 uv 的 Python 目录下，所以按命令行（q6.sim + 本 Home 路径）匹配；
    # 转发器和真解释器的命令行相同，两者都会被结束。
    $homeFull = [IO.Path]::GetFullPath($InstallHome).TrimEnd('\')
    $residual = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -in @('python.exe', 'pythonw.exe') -and $_.CommandLine -and
        $_.CommandLine -like '*q6.sim*' -and
        $_.CommandLine.IndexOf($homeFull, [StringComparison]::OrdinalIgnoreCase) -ge 0
    })
    foreach ($process in $residual) {
        Write-Output "Stopping PID $($process.ProcessId): $($process.CommandLine)"
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }

    if ($PurgeSaves) {
        $saves = Join-Path $InstallHome 'saves'
        if (Test-Path -LiteralPath $saves) { Remove-Item -LiteralPath $saves -Recurse -Force }
    }

    $after = @(Get-TaskNames)
    $expected = @(Get-Content -LiteralPath $beforePath | Where-Object { $_ } | Sort-Object -Unique)
    $diff = @(Compare-Object $expected $after)
    if ($diff.Count -eq 0) {
        Write-Output 'UNINSTALL: CLEAN'
    } else {
        Write-Output 'UNINSTALL: DIFF'
        $diff | ForEach-Object { Write-Output "$($_.SideIndicator) $($_.InputObject)" }
        exit 1
    }
} catch {
    Write-Error $_
    exit 1
}
