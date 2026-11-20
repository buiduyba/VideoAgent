# Chay bot Telegram + dashboard cua vidagents va canh chung: chet thi tu bat lai. Dung boi tac vu "vidagents" cua Task Scheduler
# (xem install-autostart.ps1). Cung chay tay duoc: powershell -File ops\start-vidagents.ps1
# Khong bat bot thu hai neu da co mot bot dang chay (Telegram chi cho MOT chuong trinh nhan tin cua cung mot bot).
param(
    [string]$Repo = "D:\vidagents\vidagents",
    [string]$Venv = "C:\vidagents-venv",
    [string]$LogDir = "C:\VideoProjects\_jobs\logs",
    [int]$MinRestartSeconds = 60
)
$ErrorActionPreference = "Continue"
# dau ra bi chuyen vao file: ep UTF-8 de in tieng Viet khong lam chuong trinh sap (cp1252 mac dinh cua Windows)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$exe = Join-Path $Venv "Scripts\vidagents.exe"
$main = Join-Path $LogDir ("supervisor-{0}.log" -f (Get-Date -Format "yyyyMMdd"))

function Write-Log([string]$msg) {
    Add-Content -Path $main -Value ("{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg)
}

# xoa nhat ky cu hon 14 ngay
Get-ChildItem $LogDir -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-14) } | Remove-Item -Force -ErrorAction SilentlyContinue

if (-not (Test-Path $exe)) { Write-Log "LOI: khong thay $exe"; exit 1 }

$services = @(
    @{ Name = "bot";       Args = "bot --config config.json";                                                Match = "vidagents.exe*bot --config" },
    @{ Name = "dashboard"; Args = "dashboard --config config.json --host 127.0.0.1 --port 8765";             Match = "vidagents.exe*dashboard --config" }
)
$lastStart = @{}

function Test-Running([string]$match) {
    $hit = Get-CimInstance Win32_Process -Filter "Name='vidagents.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$match*" }
    return [bool]$hit
}

Write-Log "supervisor bat dau (repo=$Repo)"
while ($true) {
    foreach ($s in $services) {
        if (Test-Running $s.Match) { continue }
        $since = if ($lastStart.ContainsKey($s.Name)) { ((Get-Date) - $lastStart[$s.Name]).TotalSeconds } else { 1e9 }
        if ($since -lt $MinRestartSeconds) { continue }   # chong vong lap chet-bat lien tuc
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $out = Join-Path $LogDir ("{0}-{1}.log" -f $s.Name, $stamp)
        try {
            Start-Process -FilePath $exe -ArgumentList $s.Args -WorkingDirectory $Repo -WindowStyle Hidden `
                -RedirectStandardOutput $out -RedirectStandardError ($out + ".err") | Out-Null
            $lastStart[$s.Name] = Get-Date
            Write-Log ("da bat {0} -> {1}" -f $s.Name, $out)
        } catch {
            $lastStart[$s.Name] = Get-Date
            Write-Log ("LOI khi bat {0}: {1}" -f $s.Name, $_.Exception.Message)
        }
    }
    Start-Sleep -Seconds 15
}
