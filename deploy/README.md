# Deploying Sound Safari to the kids' PCs

This folder turns chopshop into something you can install on your kids' computers
once, then update from your own machine whenever you change the code. It's built
for a specific (common) setup:

- **Two kid PCs, Windows 11 Pro, standalone (workgroup), on your home LAN.**
- **You're an admin on all of them; the kids are not.**
- **Full install on each PC** (Python + torch + CLAP + the model), so each
  machine works on its own and offline.
- **Remote redeploy from your PC** over SSH (no copying files by hand).
- **A double-click desktop icon** the kids use; they never touch a terminal.

```
  YOUR PC (dev + admin)                    EACH KID PC
  ─────────────────────                    ───────────────────────────────
  edit code → git push  ──── GitHub ────►  C:\SoundSafari\chopshop (git)
                                           .venv (Python+torch+CLAP+model)
  redeploy.ps1 ──── SSH over LAN ───────►  git pull (+pip if deps changed)
                                           Desktop "Sound Safari" icon
                                              → deploy\launch.bat (kid clicks)
```

---

## What's in here

| File | Runs on | Purpose |
|---|---|---|
| `setup-kid-pc.ps1` | each kid PC, once | Installs everything + the desktop icon + enables SSH. |
| `launch.bat` | each kid PC | What the desktop icon runs. The kid's launcher. |
| `redeploy.ps1` | **your** PC | Pushes the latest code to all kid PCs over SSH. |
| `hosts.example.txt` | your PC | Template; copy to `hosts.txt` and list your machines. |

---

## One-time setup (do this once per kid PC)

You'll need ~15 minutes per machine, mostly waiting on the torch/CLAP download.

### 0. (Once, on YOUR PC) make an SSH key for passwordless redeploys
```powershell
ssh-keygen -t ed25519 -f $HOME\.ssh\id_ed25519   # press Enter through the prompts
```
Your public key is now `C:\Users\<you>\.ssh\id_ed25519.pub`. You'll point the
setup script at it so future redeploys never ask for a password.

### 1. On the kid's PC, open an ELEVATED PowerShell
Sign in to the kid's machine with your admin account (or any admin account),
then **Start → type "PowerShell" → right-click → Run as administrator**.

### 2. Get this repo and run the setup script
```powershell
# allow running the local script for this session
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

# clone just to run setup (the script also clones the app to C:\SoundSafari)
git clone https://github.com/JosephSchafer/chopshop.git $env:TEMP\chopshop-setup
cd $env:TEMP\chopshop-setup\deploy

.\setup-kid-pc.ps1 -KidUser "lucas" -AdminPublicKey "C:\Users\<you>\.ssh\id_ed25519.pub"
```
Replace `lucas` with the child's Windows username on that PC, and the key path
with your real one. (No Git yet? The script installs Git and Python for you via
winget; you can also just download Git first and re-run.)

The script will:
1. Install Git + Python if missing.
2. Clone the app to `C:\SoundSafari\chopshop`.
3. Build the venv and install all deps incl. torch + CLAP.
4. Detect hardware and pre-download the model (~2 GB).
5. Put a **"Sound Safari"** icon on the kid's desktop.
6. Turn on the OpenSSH server and your firewall rule.
7. Install your SSH key so redeploys are passwordless.

When it finishes it prints the PC's **name and IP** - write those down.

### 3. Record the machine in your hosts list (on YOUR PC)
```powershell
cd <your local chopshop>\deploy
Copy-Item hosts.example.txt hosts.txt
notepad hosts.txt
```
Add a line per PC, e.g.:
```
lucas-pc   joe@192.168.1.42
emma-pc    joe@192.168.1.43
```
`hosts.txt` is gitignored, so your LAN details never get committed.

### 4. Test the connection
```powershell
ssh joe@192.168.1.42 "hostname"
```
If that prints the kid PC's name without asking for a password, you're set.

---

## Everyday use: how the kids run it

The kid double-clicks **Sound Safari** on their desktop. A friendly window opens,
their browser pops up with the review app, they sort their sounds, and when
they're done they press a key to finish. That's the whole experience. No admin,
no terminal, no Python.

(Recordings come in through the shared Google Drive `inbox`; finished sounds land
in the Drive `library`. See the main [README](../README.md).)

---

## Redeploying after you change the code

This is the part you asked for. From **your** PC:

```powershell
# 1. push your changes
git push

# 2. update the kids' machines
cd <your local chopshop>\deploy
.\redeploy.ps1            # code-only update on all PCs
.\redeploy.ps1 -Full      # also reinstall dependencies (after adding packages)
.\redeploy.ps1 -Only lucas-pc   # just one machine
```

`redeploy.ps1` SSHes into each PC, runs `git pull`, reinstalls Python packages
**only if** they changed (or you passed `-Full`), and refreshes the hardware
config. It prints a per-machine before/after commit and a summary.

The kid PCs must be **powered on and on the LAN** for a redeploy to reach them.
A machine that was off just picks up the latest code the next time you redeploy.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `redeploy` can't connect | PC off, asleep, or on a different network. Wake it, confirm `ssh user@host` works. |
| Redeploy asks for a password | The SSH key didn't install. Re-run setup with `-AdminPublicKey`, or add the key to `C:\ProgramData\ssh\administrators_authorized_keys` on that PC. |
| Kid icon says "not set up" | The venv is missing - re-run `setup-kid-pc.ps1` on that machine. |
| Model download interrupted | Re-run `C:\SoundSafari\chopshop\.venv\Scripts\python.exe chopshop_doctor.py --fetch-model` on the PC (or just `-Full` redeploy). |
| winget not found | On older Win 11, update "App Installer" from the Microsoft Store, or install Git + Python manually first. |

---

## Security notes (worth a glance)

- **SSH is exposed only on your LAN** via the firewall rule the setup adds. It's
  not opened to the internet. Don't port-forward 22 on your router.
- **Key-based auth** means no passwords fly across the network for redeploys.
- The app folder lives under `C:\SoundSafari` (admin-owned), so the kids can run
  it but can't accidentally delete or edit the code.
- Nothing here grants the kids admin rights; they only get a shortcut to a `.bat`.
