<#
.SYNOPSIS
  One-time setup of Sound Safari (chopshop) on a kid's Windows 11 Pro PC.

.DESCRIPTION
  Run this ONCE per machine, as an administrator, while signed in (or able to
  target) the kid's user profile. It:
    1. Installs Git and Python if missing (via winget).
    2. Clones the chopshop repo to C:\SoundSafari\chopshop.
    3. Creates a virtual environment and installs all dependencies, including
       torch + LAION-CLAP (the full on-device AI install you chose).
    4. Pre-downloads the CLAP model checkpoint (~2 GB).
    5. Creates a double-click "Sound Safari" shortcut on the kid's desktop.
    6. Enables the OpenSSH Server so you can remotely redeploy from your PC.
    7. Installs your SSH public key for passwordless redeploys (optional).

  After this runs once, you never touch this machine directly again - you push
  code from your PC with deploy\redeploy.ps1, and the kid launches from the
  desktop icon.

.PARAMETER KidUser
  The Windows username of the child account on this PC (for the desktop shortcut
  and to scope the install). Defaults to the currently logged-in user.

.PARAMETER AdminPublicKey
  Path to (or literal text of) YOUR SSH public key, so redeploys need no
  password. Optional but strongly recommended. e.g. C:\keys\joe.pub

.PARAMETER Repo
  Git URL to clone. Defaults to the public chopshop repo.

.PARAMETER InstallRoot
  Where the app lives. Defaults to C:\SoundSafari (a shared, admin-owned path so
  the kid can run but not break it).

.EXAMPLE
  # From an elevated PowerShell on the kid's PC:
  .\setup-kid-pc.ps1 -KidUser "lucas" -AdminPublicKey "C:\Users\Joe\.ssh\id_ed25519.pub"
#>

[CmdletBinding()]
param(
  [string]$KidUser = $env:USERNAME,
  [string]$AdminPublicKey = "",
  [string]$Repo = "https://github.com/JosephSchafer/chopshop.git",
  [string]$InstallRoot = "C:\SoundSafari"
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Test-Admin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
    [Security.Principal.WindowsBuiltinRole]::Administrator)
}

if (-not (Test-Admin)) {
  Write-Error "Run this script from an ELEVATED PowerShell (Run as administrator)."
}

$AppDir = Join-Path $InstallRoot "chopshop"

# --- 1. Git + Python via winget ------------------------------------------
Write-Step "Checking Git and Python"
function Ensure-Tool($exe, $wingetId, $name) {
  if (Get-Command $exe -ErrorAction SilentlyContinue) {
    Write-Host "  $name already installed."
  } else {
    Write-Host "  Installing $name via winget..."
    winget install --id $wingetId --silent --accept-source-agreements --accept-package-agreements
    # refresh PATH for this session
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")
  }
}
Ensure-Tool "git"    "Git.Git"            "Git"
Ensure-Tool "python" "Python.Python.3.12" "Python 3.12"

# --- 2. Clone or update the repo -----------------------------------------
Write-Step "Cloning chopshop to $AppDir"
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
if (Test-Path (Join-Path $AppDir ".git")) {
  Write-Host "  Repo already present; pulling latest."
  git -C $AppDir pull --ff-only
} else {
  git clone $Repo $AppDir
}

# --- 3. Virtual environment + dependencies -------------------------------
Write-Step "Creating virtual environment and installing dependencies"
$VenvDir = Join-Path $AppDir ".venv"
$VenvPy  = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
  python -m venv $VenvDir
}
& $VenvPy -m pip install --upgrade pip
# core slicing + builder deps
& $VenvPy -m pip install numpy soundfile librosa ableton-device-creator
# full on-device AI (torch + CLAP). This is the heavy part (~GBs).
Write-Host "  Installing torch + laion-clap (this can take a while)..."
& $VenvPy -m pip install laion-clap torch

# --- 4. Run the doctor (writes chopshop.json) + fetch the model ----------
Write-Step "Detecting hardware and pre-downloading the CLAP model (~2 GB)"
Push-Location $AppDir
try {
  & $VenvPy chopshop_doctor.py --config (Join-Path $AppDir "chopshop.json")
  & $VenvPy chopshop_doctor.py --fetch-model
} finally {
  Pop-Location
}

# --- 5. Kid desktop shortcut ---------------------------------------------
Write-Step "Creating the 'Sound Safari' desktop shortcut for $KidUser"
$LaunchBat = Join-Path $AppDir "deploy\launch.bat"
$KidDesktop = "C:\Users\$KidUser\Desktop"
if (-not (Test-Path $KidDesktop)) {
  Write-Warning "  Desktop not found at $KidDesktop - shortcut skipped. Pass -KidUser correctly."
} else {
  $lnkPath = Join-Path $KidDesktop "Sound Safari.lnk"
  $ws = New-Object -ComObject WScript.Shell
  $sc = $ws.CreateShortcut($lnkPath)
  $sc.TargetPath = $LaunchBat
  $sc.WorkingDirectory = $AppDir
  $sc.IconLocation = "$env:SystemRoot\System32\imageres.dll, 187"  # a generic media icon
  $sc.Description = "Open Sound Safari to sort your sounds"
  $sc.Save()
  Write-Host "  Shortcut created: $lnkPath"
}

# --- 6. OpenSSH Server (for remote redeploy from your PC) ----------------
Write-Step "Enabling OpenSSH Server for remote redeploys"
$ssh = Get-WindowsCapability -Online -Name "OpenSSH.Server*"
if ($ssh.State -ne "Installed") {
  Add-WindowsCapability -Online -Name "OpenSSH.Server~~~~0.0.1.0" | Out-Null
}
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
# allow inbound SSH on the LAN
if (-not (Get-NetFirewallRule -Name "sshd-soundsafari" -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -Name "sshd-soundsafari" -DisplayName "OpenSSH Server (Sound Safari)" `
    -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
}

# --- 7. Install your public key for passwordless admin redeploys ---------
if ($AdminPublicKey) {
  Write-Step "Installing admin SSH public key (passwordless redeploy)"
  # If the current account is an admin, Windows uses the special
  # administrators_authorized_keys file (not the per-user one).
  $keyText = if (Test-Path $AdminPublicKey) { Get-Content $AdminPublicKey -Raw } else { $AdminPublicKey }
  $adminKeys = "$env:ProgramData\ssh\administrators_authorized_keys"
  Add-Content -Path $adminKeys -Value $keyText
  # lock down ACLs as OpenSSH requires (Administrators + SYSTEM only)
  icacls $adminKeys /inheritance:r | Out-Null
  icacls $adminKeys /grant "Administrators:F" "SYSTEM:F" | Out-Null
  Write-Host "  Key installed. Redeploys from your PC will not prompt for a password."
} else {
  Write-Warning "No -AdminPublicKey given. Redeploys will prompt for this PC's admin password."
}

Write-Step "Done"
Write-Host @"
Sound Safari is installed on this PC.

  App folder : $AppDir
  Launcher   : the kid double-clicks 'Sound Safari' on the desktop
  Remote     : redeploy from your PC with deploy\redeploy.ps1 over SSH

Next: note this machine's name/IP for your deploy\hosts.txt:
  Name : $env:COMPUTERNAME
  IPs  : $((Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '169.*' -and $_.IPAddress -ne '127.0.0.1'}).IPAddress -join ', ')
"@ -ForegroundColor Green
