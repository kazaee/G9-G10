$ErrorActionPreference = 'Stop'
$ruleName = 'iLO Installer HTTP 8080 - private iLO subnet'
if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8080 -LocalAddress 172.20.224.253 -RemoteAddress 172.20.224.0/24 -Program 'C:\Users\ah.khazaee\Desktop\test\.tools\python\python.exe' -Profile Any | Out-Null
}
