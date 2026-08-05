param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Prompt,
    [string]$SessionId = "quick-$([guid]::NewGuid().ToString('N').Substring(0,8))",
    [string]$ConvId = "quick-$([guid]::NewGuid().ToString('N').Substring(0,8))"
)
$ErrorActionPreference = 'Stop'
$base = 'http://127.0.0.1:8000'

# 1. 确保 LLM 配置
$body = @{ api_url = 'https://opencode.ai/zen/go/v1'; api_key = 'sk-ss0dhMVpqZMsaFYU0Acw7HzTFJob61HSCz39db1mqWMYvN8lIkdePOhzEY0TvIuP'; model = 'deepseek-v4-flash' } | ConvertTo-Json
Invoke-RestMethod -Method Put -Uri "$base/llm/config" -ContentType 'application/json' -Body $body -TimeoutSec 60 | Out-Null
Write-Host "[1/3] LLM 已配置" -ForegroundColor Green

# 2. 启动会话（断开自动重试一次）
$session = $null
try {
    $session = Invoke-RestMethod -Method Post -Uri "$base/browser/session/start" -ContentType 'application/json' -Body (@{ browser_session_id = $SessionId; mode = 'isolated' } | ConvertTo-Json) -TimeoutSec 180
} catch {
    Start-Sleep -Seconds 5
    $session = Invoke-RestMethod -Method Post -Uri "$base/browser/session/start" -ContentType 'application/json' -Body (@{ browser_session_id = $SessionId; mode = 'isolated' } | ConvertTo-Json) -TimeoutSec 180
}
Write-Host "[2/3] 会话就绪: $SessionId" -ForegroundColor Green

# 3. 跑任务（流式显示进度）
Write-Host "[3/3] 运行任务中，请稍候..." -ForegroundColor Green
$body = @{ message = $Prompt; conversation_id = $ConvId; browser_session_id = $SessionId } | ConvertTo-Json
try {
    $r = Invoke-RestMethod -Method Post -Uri "$base/agent/run" -ContentType 'application/json' -Body $body -TimeoutSec 900
    Write-Host "`n===== 最终答案 =====" -ForegroundColor Cyan
    Write-Host $r.answer
    Write-Host "`n===== Token 用量 =====" -ForegroundColor Cyan
    $r.token_usage | ConvertTo-Json
    if ($r.success) { Write-Host "`n✅ 任务成功" -ForegroundColor Green } else { Write-Host "`n❌ 任务失败" -ForegroundColor Red }
} finally {
    try { Invoke-RestMethod -Method Delete -Uri "$base/browser/sessions/$SessionId" -TimeoutSec 30 | Out-Null } catch {}
}
