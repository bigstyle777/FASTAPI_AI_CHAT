# 数据库恢复：解压备份后用 psql 执行。备份由 backup_db.ps1 生成（--clean --if-exists），
# 恢复会先删除现有对象再重建，属于破坏性操作，默认要求确认。
#
# 用法（在仓库根目录）：
#   .\backend\scripts\restore_db.ps1 -File .\backups\aichat_20261031_120000.sql.gz
#   .\backend\scripts\restore_db.ps1 -File ... -Force   # 跳过交互确认（CI/演练）
param(
    [Parameter(Mandatory = $true)][string]$File,
    [string]$Container = "aichat-postgres",
    [string]$DbUser = "postgres",
    [string]$Database = "aichat",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$resolved = (Resolve-Path -Path $File).Path
if (-not (Test-Path $resolved)) {
    throw "备份文件不存在: $File"
}

$running = docker inspect -f '{{.State.Running}}' $Container 2>$null
if ($LASTEXITCODE -ne 0 -or $running -ne "true") {
    throw "容器 $Container 不存在或未运行"
}

if (-not $Force) {
    Write-Host "即将把 $resolved 恢复到 $Container/$Database。" -ForegroundColor Yellow
    Write-Host "现有数据会被删除并替换，此操作不可撤销。" -ForegroundColor Yellow
    $answer = Read-Host "输入 yes 继续"
    if ($answer -ne "yes") {
        Write-Host "已取消"
        exit 1
    }
}

$tempSql = Join-Path $env:TEMP "aichat_restore_$(Get-Date -Format 'yyyyMMdd_HHmmss').sql"
$input = [System.IO.File]::OpenRead($resolved)
try {
    $output = [System.IO.File]::Create($tempSql)
    try {
        $gzip = New-Object System.IO.Compression.GZipStream(
            $input,
            [System.IO.Compression.CompressionMode]::Decompress
        )
        try { $gzip.CopyTo($output) } finally { $gzip.Dispose() }
    } finally { $output.Dispose() }
} finally { $input.Dispose() }

Write-Host "开始恢复 $resolved -> $Container/$Database"
# ON_ERROR_STOP=1：任何一条语句失败立即中止，避免留下半恢复状态
cmd /c "docker exec -i $Container psql -U $DbUser -d $Database -v ON_ERROR_STOP=1 < `"$tempSql`""
$exitCode = $LASTEXITCODE
Remove-Item $tempSql -Force

if ($exitCode -ne 0) {
    throw "psql 恢复失败（退出码 $exitCode），请检查上方错误并重新执行"
}
Write-Host "恢复完成" -ForegroundColor Green
