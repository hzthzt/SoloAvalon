param(
    [switch]$SkipInstall,
    [switch]$NoOpen,
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$FrontendRoot = Join-Path $ProjectRoot "frontend"
$LogDir = Join-Path $ProjectRoot "logs"
$BackendBaseUrl = "http://127.0.0.1:$BackendPort"
$BackendUrl = "http://127.0.0.1:$BackendPort/api/games"
$BackendProfilesUrl = "http://127.0.0.1:$BackendPort/api/llm-profiles"
$FrontendUrl = "http://127.0.0.1:$FrontendPort"
$backendOut = Join-Path $LogDir "backend-dev.out.log"
$backendErr = Join-Path $LogDir "backend-dev.err.log"
$frontendOut = Join-Path $LogDir "frontend-dev.out.log"
$frontendErr = Join-Path $LogDir "frontend-dev.err.log"
$backendInstallOut = Join-Path $LogDir "backend-install.out.log"
$backendInstallErr = Join-Path $LogDir "backend-install.err.log"
$backendFallbackInstallOut = Join-Path $LogDir "backend-install-fallback.out.log"
$backendFallbackInstallErr = Join-Path $LogDir "backend-install-fallback.err.log"
$frontendInstallOut = Join-Path $LogDir "frontend-install.out.log"
$frontendInstallErr = Join-Path $LogDir "frontend-install.err.log"
# Default URLs: http://127.0.0.1:8000 and http://127.0.0.1:5173

function Write-Step {
    param([string]$Message)

    Write-Host ""
    Write-Host "==> $Message"
}

function Set-DevUrls {
    $script:BackendBaseUrl = "http://127.0.0.1:$BackendPort"
    $script:BackendUrl = "http://127.0.0.1:$BackendPort/api/games"
    $script:BackendProfilesUrl = "http://127.0.0.1:$BackendPort/api/llm-profiles"
    $script:FrontendUrl = "http://127.0.0.1:$FrontendPort"
}

function Get-RequiredCommand {
    param(
        [string]$Name,
        [string]$InstallHint
    )

    $command = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $command) {
        throw "Command '$Name' was not found. $InstallHint"
    }

    return $command.Source
}

function Get-NpmCommand {
    $command = Get-Command "npm.cmd" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) {
        return $command.Source
    }

    return Get-RequiredCommand "npm" "Install Node.js and make sure npm is in PATH."
}

function Invoke-Checked {
    param(
        [string]$Description,
        [scriptblock]$Command,
        [string]$WorkingDirectory = $ProjectRoot
    )

    Write-Step $Description
    Push-Location $WorkingDirectory
    try {
        & $Command
        if ($LASTEXITCODE -ne 0) {
            throw "$Description failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

function Invoke-LoggedChecked {
    param(
        [string]$Description,
        [scriptblock]$Command,
        [string]$WorkingDirectory = $ProjectRoot,
        [string]$StdoutPath,
        [string]$StderrPath
    )

    Write-Step $Description
    Write-Host "stdout log: $StdoutPath"
    Write-Host "stderr log: $StderrPath"
    Push-Location $WorkingDirectory
    try {
        & $Command > $StdoutPath 2> $StderrPath
        if ($LASTEXITCODE -ne 0) {
            throw "$Description failed with exit code $LASTEXITCODE. See $StderrPath"
        }
    }
    finally {
        Pop-Location
    }
}

function Test-PythonDependencies {
    param([string]$PythonPath)

    try {
        & $PythonPath -c "import fastapi, uvicorn" *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Test-HttpReady {
    param([string]$Url)

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    }
    catch {
        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
            return $statusCode -ge 200 -and $statusCode -lt 500
        }

        return $false
    }
}

function Test-PortInUse {
    param([int]$Port)

    $listener = $null
    try {
        $address = [System.Net.IPAddress]::Parse("127.0.0.1")
        $listener = [System.Net.Sockets.TcpListener]::new($address, $Port)
        $listener.Start()
        return $false
    }
    catch {
        return $true
    }
    finally {
        if ($listener) {
            $listener.Stop()
        }
    }
}

function Get-ListeningProcessIds {
    param([int]$Port)

    try {
        return @(
            Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty OwningProcess -Unique
        )
    }
    catch {
        return @()
    }
}

function Get-ProcessCommandLine {
    param([int]$ProcessId)

    try {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        if ($process -and $process.CommandLine) {
            return [string]$process.CommandLine
        }
    }
    catch {
        return ""
    }

    return ""
}

function Test-BackendServiceProcess {
    param([string]$CommandLine)

    return $CommandLine -match "uvicorn" -and $CommandLine -match "backend\.app\.main:app"
}

function Test-FrontendServiceProcess {
    param([string]$CommandLine)

    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return $false
    }

    $normalizedCommandLine = $CommandLine.ToLowerInvariant().Replace("/", "\")
    $normalizedFrontendRoot = ([string]$FrontendRoot).ToLowerInvariant().Replace("/", "\")
    return $normalizedCommandLine.Contains("vite") -and $normalizedCommandLine.Contains($normalizedFrontendRoot)
}

function Wait-ForPortRelease {
    param(
        [int]$Port,
        [int]$Attempts = 20
    )

    for ($i = 1; $i -le $Attempts; $i++) {
        if (-not (Test-PortInUse $Port)) {
            return $true
        }

        Start-Sleep -Milliseconds 250
    }

    return $false
}

function Stop-ProjectServiceOnPort {
    param(
        [string]$Name,
        [int]$Port,
        [scriptblock]$IsProjectProcess
    )

    $stoppedAny = $false
    $processIds = @(Get-ListeningProcessIds -Port $Port)
    foreach ($processId in $processIds) {
        $commandLine = Get-ProcessCommandLine -ProcessId $processId
        if (& $IsProjectProcess $commandLine) {
            Write-Step "Stopping existing $Name on port $Port"
            Write-Host "$Name PID: $processId"
            Stop-Process -Id $processId -Force -ErrorAction Stop
            $stoppedAny = $true
        }
    }

    return $stoppedAny
}

function Stop-ProjectServiceProcesses {
    param(
        [string]$Name,
        [scriptblock]$IsProjectProcess
    )

    $stoppedCount = 0
    try {
        $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    }
    catch {
        return $stoppedCount
    }

    foreach ($process in $processes) {
        if (-not $process.CommandLine -or [int]$process.ProcessId -eq $PID) {
            continue
        }

        if (& $IsProjectProcess ([string]$process.CommandLine)) {
            Write-Step "Stopping existing $Name process"
            Write-Host "$Name PID: $($process.ProcessId)"
            Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
            $stoppedCount += 1
        }
    }

    return $stoppedCount
}

function Find-AvailablePort {
    param(
        [string]$Name,
        [int]$PreferredPort,
        [scriptblock]$IsProjectProcess,
        [int]$SearchLimit = 100
    )

    $lastPort = $PreferredPort + $SearchLimit - 1
    for ($port = $PreferredPort; $port -le $lastPort; $port++) {
        if (-not (Test-PortInUse $port)) {
            if ($port -ne $PreferredPort) {
                Write-Host "$Name preferred port $PreferredPort is unavailable; using $port."
            }
            return $port
        }

        $stoppedProjectService = Stop-ProjectServiceOnPort `
            -Name $Name `
            -Port $port `
            -IsProjectProcess $IsProjectProcess

        if ($stoppedProjectService -and (Wait-ForPortRelease -Port $port)) {
            if ($port -ne $PreferredPort) {
                Write-Host "$Name preferred port $PreferredPort is unavailable; using $port."
            }
            return $port
        }

        if (-not $stoppedProjectService) {
            Write-Host "$Name port $port is used by another process; trying next port."
        }
    }

    throw "No available port found for $Name in range $PreferredPort-$lastPort."
}

function Wait-ForHttp {
    param(
        [string]$Name,
        [string]$Url,
        [System.Diagnostics.Process]$Process,
        [string]$ErrorLog,
        [int]$Attempts = 45
    )

    Write-Step "Waiting for ${Name}: $Url"
    for ($i = 1; $i -le $Attempts; $i++) {
        if ($Process) {
            $Process.Refresh()
            if ($Process.HasExited) {
                throw "$Name exited before it became ready. Check $ErrorLog"
            }
        }

        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return $true
            }
        }
        catch {
            if ($_.Exception.Response) {
                $statusCode = [int]$_.Exception.Response.StatusCode
                if ($statusCode -ge 200 -and $statusCode -lt 500) {
                    return $true
                }
            }
        }

        Start-Sleep -Seconds 1
    }

    return $false
}

function Start-LoggedProcess {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory,
        [string]$StdoutPath,
        [string]$StderrPath
    )

    Write-Step "Starting $Name"
    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $StdoutPath `
        -RedirectStandardError $StderrPath `
        -PassThru `
        -WindowStyle Hidden

    Write-Host "$Name PID: $($process.Id)"
    Write-Host "$Name stdout: $StdoutPath"
    Write-Host "$Name stderr: $StderrPath"
    return $process
}

Set-Location $ProjectRoot
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Write-Host "SoloAvalon local development launcher"
Write-Host "Project root: $ProjectRoot"

$stoppedProjectServices = 0
$stoppedProjectServices += Stop-ProjectServiceProcesses `
    -Name "Backend API" `
    -IsProjectProcess ${function:Test-BackendServiceProcess}
$stoppedProjectServices += Stop-ProjectServiceProcesses `
    -Name "Frontend Vite" `
    -IsProjectProcess ${function:Test-FrontendServiceProcess}
if ($stoppedProjectServices -gt 0) {
    Start-Sleep -Milliseconds 500
}

$BackendPort = Find-AvailablePort `
    -Name "Backend API" `
    -PreferredPort $BackendPort `
    -IsProjectProcess ${function:Test-BackendServiceProcess}
$FrontendPort = Find-AvailablePort `
    -Name "Frontend Vite" `
    -PreferredPort $FrontendPort `
    -IsProjectProcess ${function:Test-FrontendServiceProcess}
Set-DevUrls

$env:SOLOAVALON_BACKEND_URL = $BackendBaseUrl
$env:SOLOAVALON_FRONTEND_ORIGIN = $FrontendUrl

Write-Host "Frontend URL: $FrontendUrl"
Write-Host "Backend URL: $BackendBaseUrl"

$python = Get-RequiredCommand "python" "Install Python 3.10 or newer and make sure python is in PATH."
$npm = Get-NpmCommand

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Invoke-Checked "Create Python virtual environment" { & $python -m venv .venv }
}

$BackendPython = $VenvPython
if (-not $SkipInstall) {
    if (-not (Test-PythonDependencies $VenvPython)) {
        try {
            Invoke-LoggedChecked `
                -Description "Install backend dependencies" `
                -Command { & $VenvPython -m pip install -e ".[dev]" } `
                -StdoutPath $backendInstallOut `
                -StderrPath $backendInstallErr
        }
        catch {
            Write-Warning "Standard backend dependency install failed. See $backendInstallErr"
            try {
                Invoke-LoggedChecked `
                    -Description "Install local backend package without PEP517" `
                    -Command { & $VenvPython -m pip install -e . --no-use-pep517 --no-deps } `
                    -StdoutPath $backendFallbackInstallOut `
                    -StderrPath $backendFallbackInstallErr
            }
            catch {
                Write-Warning "Fallback editable install failed. See $backendFallbackInstallErr"
            }
        }
    }
    else {
        Write-Step "Backend dependencies are available; skipping install"
    }

    if (-not (Test-PythonDependencies $VenvPython)) {
        if (Test-PythonDependencies $python) {
            Write-Step ".venv backend dependencies are unavailable; using system Python"
            $BackendPython = $python
        }
        else {
            throw "Backend dependencies are unavailable in .venv and system Python. Install fastapi and uvicorn, then retry."
        }
    }

    $nodeModules = Join-Path $FrontendRoot "node_modules"
    if (-not (Test-Path $nodeModules)) {
        Invoke-LoggedChecked `
            -Description "Install frontend dependencies" `
            -Command { & $npm install } `
            -WorkingDirectory $FrontendRoot `
            -StdoutPath $frontendInstallOut `
            -StderrPath $frontendInstallErr
    }
    else {
        Write-Step "Frontend dependencies are available; skipping install"
    }
}
else {
    Write-Step "-SkipInstall specified; skipping dependency install checks"
    if (-not (Test-PythonDependencies $VenvPython)) {
        if (Test-PythonDependencies $python) {
            Write-Step ".venv backend dependencies are unavailable; using system Python"
            $BackendPython = $python
        }
        else {
            throw "-SkipInstall was used, but backend dependencies are unavailable in .venv and system Python."
        }
    }
}

$startedProcesses = @()
try {
    $backendProcess = Start-LoggedProcess `
        -Name "Backend API" `
        -FilePath $BackendPython `
        -ArgumentList @("-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "$BackendPort") `
        -WorkingDirectory $ProjectRoot `
        -StdoutPath $backendOut `
        -StderrPath $backendErr
    $startedProcesses += $backendProcess

    $frontendProcess = Start-LoggedProcess `
        -Name "Frontend Vite" `
        -FilePath $npm `
        -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", "$FrontendPort") `
        -WorkingDirectory $FrontendRoot `
        -StdoutPath $frontendOut `
        -StderrPath $frontendErr
    $startedProcesses += $frontendProcess

    if (-not (Wait-ForHttp "Backend API" $BackendUrl $backendProcess $backendErr)) {
        throw "Backend API did not become ready in time. Check $backendErr"
    }

    if (-not (Wait-ForHttp "Backend model profile API" $BackendProfilesUrl $backendProcess $backendErr)) {
        throw "Backend model profile API did not become ready in time. Check $backendErr"
    }

    if (-not (Wait-ForHttp "Frontend page" $FrontendUrl $frontendProcess $frontendErr)) {
        throw "Frontend page did not become ready in time. Check $frontendErr"
    }

    if (-not $NoOpen) {
        Write-Step "Opening browser: $FrontendUrl"
        Start-Process $FrontendUrl | Out-Null
    }
}
catch {
    foreach ($process in $startedProcesses) {
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }

    throw
}

Write-Host ""
Write-Host "SoloAvalon is running"
Write-Host "Frontend: $FrontendUrl"
Write-Host "Backend: $BackendBaseUrl"
Write-Host "Logs: $LogDir"
Write-Host "Stop services: Get-Process -Id $($backendProcess.Id),$($frontendProcess.Id) | Stop-Process"
