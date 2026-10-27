# Expose WSL Traefik (80/443) and keep SSH pattern on Windows.
# Run elevated in PowerShell:  powershell -ExecutionPolicy Bypass -File \\wsl$\Ubuntu\home\deepadm\uccx-portal\scripts\windows-expose-ports.ps1
# Or from WSL: /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -Command "Start-Process powershell -Verb RunAs -ArgumentList '-ExecutionPolicy Bypass -File C:\Users\deepadm\expose-uccx-ports.ps1'"

$ErrorActionPreference = "Stop"

$wslIp = (wsl -e hostname -I).Trim().Split(" ")[0]
if (-not $wslIp) { throw "Impossible de lire l'IP WSL" }

Write-Host "WSL IP: $wslIp"

foreach ($port in 80, 443, 8080) {
  netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$port 2>$null | Out-Null
  netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=$port connectaddress=$wslIp connectport=$port
  Write-Host "portproxy 0.0.0.0:$port -> ${wslIp}:$port"
}

# SSH remains on 2222 (existing pattern) — refresh target IP
netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=2222 2>$null | Out-Null
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=2222 connectaddress=$wslIp connectport=22
Write-Host "portproxy 0.0.0.0:2222 -> ${wslIp}:22"

# Firewall allow HTTP/HTTPS (Public/Domain/Private)
foreach ($p in @(
  @{ Name = "WSL Traefik HTTP 80"; Port = 80 },
  @{ Name = "WSL Traefik HTTPS 443"; Port = 443 },
  @{ Name = "WSL UCCX API HTTP 8080"; Port = 8080 }
)) {
  if (-not (Get-NetFirewallRule -DisplayName $p.Name -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $p.Name -Direction Inbound -Action Allow -Protocol TCP -LocalPort $p.Port -Profile Any | Out-Null
    Write-Host "Firewall rule created: $($p.Name)"
  } else {
    Write-Host "Firewall rule exists: $($p.Name)"
  }
}

Write-Host ""
Write-Host "Current portproxy:"
netsh interface portproxy show all
Write-Host "Done. Test HTTPS: https://collab-edge.i-hub.com/api/public/health"
Write-Host "Test HTTP UCCX: http://<IP-Windows>:8080/api/public/health"
