$ErrorActionPreference = "Stop"

$port = 8091
$appDir = "C:\Users\Vaio\Documents\1Clik"
$python = "C:\Users\Vaio\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$logOut = Join-Path $appDir "1clik_stdout.current.log"
$logErr = Join-Path $appDir "1clik_stderr.current.log"

$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    exit 0
}

Start-Process `
    -FilePath $python `
    -ArgumentList @("app.py", "--no-browser") `
    -WorkingDirectory $appDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $logOut `
    -RedirectStandardError $logErr
