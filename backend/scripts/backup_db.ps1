# 数据库逻辑备份：通过 docker exec 在 postgres 容器内执行 pg_dump，
# 宿主机用 .NET GZipStream 压缩，避免 PowerShell 文本重定向破坏 SQL 编码。
#
# 用法（在仓库根目录）：
#   .\backend\scripts\backup_db.ps1
#   .\backend\scripts\backup_db.ps1 -KeepDays 14 -OutputDir D:\backups
param(
    [string]$OutputDir = (Join-Path $PSScriptRoot "..\..\backups"),
    [int]$KeepDays = 7,
    [string]$Container = "aichat-postgres",
    [string]$DbUser = "postgres",
    [string]$Database = "aichat"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "未找到 docker 命令，请在部署 docker-compose 的机器上执行"
}

$running = docker inspect -f '{{.State.Running}}' $Container 2>$null
if ($LASTEXITCODE -ne 0 -or $running -ne "true") {
    throw "容器 $Container 不存在或未运行"
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$sqlPath = Join-Path $OutputDir "aichat_$stamp.sql"
$gzPath = "$sqlPath.gz"

Write-Host "开始备份 $Container/$Database -> $gzPath"
# cmd 重定向保留 pg_dump 的原始字节；PowerShell 的 > 会改变编码
cmd /c "docker exec $Container pg_dump -U $DbUser -d $Database --clean --if-exists > `"$sqlPath`""
if ($LASTEXITCODE -ne 0) {
    if (Test-Path $sqlPath) { Remove-Item $sqlPath -Force }
    throw "pg_dump 失败（退出码 $LASTEXITCODE）"
}

$input = [System.IO.File]::OpenRead($sqlPath)
try {
    $output = [System.IO.File]::Create($gzPath)
    try {
        $gzip = New-Object System.IO.Compression.GZipStream(
            $output,
            [System.IO.Compression.CompressionLevel]::Optimal
        )
        try { $input.CopyTo($gzip) } finally { $gzip.Dispose() }
    } finally { $output.Dispose() }
} finally { $input.Dispose() }

Remove-Item $sqlPath -Force

# 保留策略：只清理本脚本命名的备份文件，不碰目录里的其他内容
$cutoff = (Get-Date).AddDays(-$KeepDays)
Get-ChildItem -Path $OutputDir -Filter "aichat_*.sql.gz" |
    Where-Object { $_.LastWriteTime -lt $cutoff } |
    ForEach-Object {
        Write-Host "清理过期备份 $($_.Name)"
        Remove-Item $_.FullName -Force
    }

$sizeMb = [math]::Round((Get-Item $gzPath).Length / 1MB, 2)
Write-Host "备份完成: $gzPath ($sizeMb MB)"
