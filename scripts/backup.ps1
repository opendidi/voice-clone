# backup.ps1 — 一键备份：拉取最新 → 提交改动 → 推送当前分支
# 用法：
#   .\scripts\backup.ps1                 # 自动生成时间戳提交信息
#   .\scripts\backup.ps1 -Message "feat: xxx"   # 指定提交信息
param(
    [string]$Message = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path .git)) {
    Write-Error "当前目录不是 git 仓库，请先 cd 到项目根目录再运行。"
    exit 1
}

$branch = git rev-parse --abbrev-ref HEAD
Write-Host ">> 当前分支：$branch" -ForegroundColor Cyan

# 尝试用 rebase 拉取远端最新，避免产生多余的 merge commit；失败不阻断本地提交
$upstream = git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>$null
if ($upstream) {
    Write-Host ">> 拉取 $upstream (rebase) ..." -ForegroundColor Cyan
    git pull --rebase $upstream 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "pull --rebase 失败（可能本地有冲突）。仍会继续本地提交，稍后你可能需要手动解决。"
    }
}

# 是否有改动
$status = git status --porcelain
if (-not $status) {
    Write-Host "✅ 没有需要备份的改动。" -ForegroundColor Green
    exit 0
}

git add -A
if (-not $Message) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $Message = "chore: 自动备份 $ts"
}

git commit -m $Message
if ($LASTEXITCODE -ne 0) {
    Write-Error "commit 失败，请检查。"
    exit 1
}

# 推送（无 upstream 则建立跟踪）
if (-not $upstream) {
    git push -u origin $branch
} else {
    git push
}

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ 已备份并推送到 $branch" -ForegroundColor Green
} else {
    Write-Error "push 失败，请检查网络或 GitHub 凭据（HTTPS 需用 Personal Access Token）。"
    exit 1
}
