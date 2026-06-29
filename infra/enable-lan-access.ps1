# 放行 DyFlow 前端端口，供内网同事访问。需以管理员身份运行。
#   右键 → 以管理员身份运行 PowerShell，然后执行：
#   powershell -ExecutionPolicy Bypass -File infra\enable-lan-access.ps1

$ErrorActionPreference = "Stop"

$rules = @(
    @{ Name = "DyFlow-Frontend-3000"; Port = 3000 }  # 前端（nginx 反代 /api 与 /ws，内网只需开放此端口）
)

foreach ($r in $rules) {
    $existing = Get-NetFirewallRule -DisplayName $r.Name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host ("防火墙规则已存在: {0} (TCP {1})" -f $r.Name, $r.Port)
    } else {
        New-NetFirewallRule -DisplayName $r.Name -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $r.Port -Profile Any | Out-Null
        Write-Host ("已添加入站规则: {0} (TCP {1})" -f $r.Name, $r.Port)
    }
}

$ip = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -like "192.168.*" -or $_.IPAddress -like "10.*" } |
    Select-Object -First 1).IPAddress
Write-Host ""
Write-Host ("内网同事访问地址: http://{0}:3000" -f $ip) -ForegroundColor Green
