$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"

$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($uvCommand) {
    $uv = $uvCommand.Source
}
else {
    $uvCandidates = @(
        (Join-Path $env:USERPROFILE "uv.exe"),
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe")
    )
    $uv = $uvCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (-not $uv) {
    [Console]::Error.WriteLine("uv was not found in PATH, $env:USERPROFILE\uv.exe, or $env:USERPROFILE\.local\bin\uv.exe")
    exit 1
}

function Invoke-Step {
    param(
        [int]$Number,
        [string]$Name,
        [scriptblock]$Command
    )

    Write-Output "== [$Number/7] $Name"
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        & $Command
        if ($LASTEXITCODE -ne 0) {
            throw "Command exited with code $LASTEXITCODE"
        }
    }
    catch {
        $timer.Stop()
        Write-Output ("TIME [{0}/7] {1}: {2}s" -f $Number, $Name, [math]::Round($timer.Elapsed.TotalSeconds, 3))
        [Console]::Error.WriteLine("FAILED at step $Number")
        exit 1
    }
    $timer.Stop()
    Write-Output ("TIME [{0}/7] {1}: {2}s" -f $Number, $Name, [math]::Round($timer.Elapsed.TotalSeconds, 3))
}

$rssLimit = if ([string]::IsNullOrEmpty($env:Q6_RSS_LIMIT_MB)) { "512" } else { $env:Q6_RSS_LIMIT_MB }
function Invoke-RssGuard {
    param([string[]]$ChildCommand)
    $ChildCommand[0] = $uv
    & $uv run python scripts/rss_guard.py --limit-mb $rssLimit -- @ChildCommand
    if ($LASTEXITCODE -ne 0) { throw "RSS guarded command exited with code $LASTEXITCODE" }
}

Invoke-Step 1 "uv sync --frozen" { & $uv sync --frozen }
Invoke-Step 2 "uv run ruff check src tests scripts" { & $uv run ruff check src tests scripts }
Invoke-Step 3 "uv run python -m q6.lint.lookahead_ast src" { & $uv run python -m q6.lint.lookahead_ast src }
Invoke-Step 4 "uv run pytest tests/unit tests/property" { Invoke-RssGuard @("uv", "run", "pytest", "tests/unit", "tests/property") }
# 每个测试文件单独一个进程、单独受 RSS 上限约束（同进程累加会超 512MB，见 verify.sh 注释）
Invoke-Step 5 "uv run pytest tests/lookahead (one process per file)" {
    foreach ($f in (Get-ChildItem -Path tests\lookahead -Filter "test_*.py" | Sort-Object Name)) {
        Write-Output "-- tests/lookahead/$($f.Name)"
        Invoke-RssGuard @("uv", "run", "pytest", "tests/lookahead/$($f.Name)")
    }
}
Invoke-Step 6 "uv run pytest tests/consistency (one process per file)" {
    foreach ($f in (Get-ChildItem -Path tests\consistency -Filter "test_*.py" | Sort-Object Name)) {
        Write-Output "-- tests/consistency/$($f.Name)"
        Invoke-RssGuard @("uv", "run", "pytest", "tests/consistency/$($f.Name)")
    }
}
Invoke-Step 7 "snapshot validation" {
    Invoke-RssGuard @("uv", "run", "python", "scripts/check_snapshot.py")
}

if ($env:Q6_REQUIRE_SNAPSHOT -eq "1") {
    Write-Output "ALL CHECKS PASSED (acceptance mode: real snapshot required)"
}
else {
    Write-Output "ALL CHECKS PASSED (dev mode: missing snapshot is skipped, NOT valid for acceptance; set Q6_REQUIRE_SNAPSHOT=1)"
}
