# Cai tac vu Windows "vidagents": tu chay bot + dashboard (ops\start-vidagents.ps1) moi khi nguoi dung DANG NHAP vao Windows.
#   powershell -ExecutionPolicy Bypass -File ops\install-autostart.ps1            # cai
#   powershell -ExecutionPolicy Bypass -File ops\install-autostart.ps1 -Uninstall  # go
# Chay duoi tai khoan hien tai (can: dang nhap `ant auth login`, file .env, Node, FFmpeg nam trong moi truong cua nguoi nay), khong can quyen admin.
# Muon bot len ca khi chua ai dang nhap Windows: bat tu dong dang nhap cho may nay (netplwiz), hoac dung dich vu Windows (can mat khau).
param([switch]$Uninstall, [string]$TaskName = "vidagents", [int]$DelaySeconds = 30)
$ErrorActionPreference = "Stop"
if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Da go tac vu $TaskName"
    return
}
$script = Join-Path $PSScriptRoot "start-vidagents.ps1"
if (-not (Test-Path $script)) { throw "Khong thay $script" }
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory (Split-Path $PSScriptRoot -Parent) `
    -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $script)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = ("PT{0}S" -f $DelaySeconds)      # doi mang / o NAS len truoc
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "vidagents: bot Telegram + dashboard, tu bat khi dang nhap Windows (ops\start-vidagents.ps1)" -Force | Out-Null
Write-Output ("Da cai tac vu '{0}' cho {1}: chay moi khi dang nhap (tre {2} giay), tu bat lai neu chet." -f $TaskName, $user, $DelaySeconds)
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State | Format-Table -AutoSize | Out-String
