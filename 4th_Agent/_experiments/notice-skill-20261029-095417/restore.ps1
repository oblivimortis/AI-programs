# 回滚脚本：恢复到“加载 notice 技能之前”的版本。
#
# 用法（在任意目录执行均可）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\restore.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\restore.ps1 -KeepSnapshot
#
# 不带 -KeepSnapshot 时，回滚成功后会删除本快照目录自身。

param(
    [switch]$KeepSnapshot
)

$ErrorActionPreference = "Stop"

$snapshot = $PSScriptRoot
$project = (Resolve-Path (Join-Path $snapshot "..\..")).Path
$manifestPath = Join-Path $snapshot "manifest.json"

if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw "找不到 manifest.json：$manifestPath"
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json

Write-Host "项目目录: $project"
Write-Host "快照目录: $snapshot"
Write-Host ""

foreach ($rel in @($manifest.modified)) {
    $src = Join-Path $snapshot ("backup\" + $rel)
    $dst = Join-Path $project $rel
    if (-not (Test-Path -LiteralPath $src)) {
        Write-Warning "快照中缺少文件，跳过: $rel"
        continue
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
    Copy-Item -LiteralPath $src -Destination $dst -Force
    Write-Host "已恢复: $rel"
}

foreach ($rel in @($manifest.added)) {
    $target = Join-Path $project $rel
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Force -Recurse
        Write-Host "已删除新增文件: $rel"
    }
}

# 删除新增模块留下的字节码缓存，避免 Python 继续加载旧模块。
$pycache = Join-Path $project "core\__pycache__"
if (Test-Path -LiteralPath $pycache) {
    Get-ChildItem -LiteralPath $pycache -Filter "skills.*.pyc" -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

$tests_pycache = Join-Path $project "tests\__pycache__"
if (Test-Path -LiteralPath $tests_pycache) {
    Get-ChildItem -LiteralPath $tests_pycache -Filter "test_skills.*.pyc" -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "回滚完成，代码已恢复到加载技能之前的状态。"

if (-not $KeepSnapshot) {
    $parent = Split-Path -Parent $snapshot
    Set-Location $parent
    Remove-Item -LiteralPath $snapshot -Recurse -Force
    Write-Host "已删除快照目录: $snapshot"
} else {
    Write-Host "已保留快照目录: $snapshot"
}
