#Requires -Version 5.1

[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8101,
    [switch]$Test,
    [switch]$SetupOnly,
    [ValidateSet("inventory", "outbound")]
    [string]$Service = "inventory"
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot
$servicePortVariable = $Service.ToUpperInvariant() + "_SERVICE_PORT"
$previousServicePort = [Environment]::GetEnvironmentVariable($servicePortVariable, "Process")

# PowerShell 5.1 nie przerywa skryptu po bledzie programu zewnetrznego.
function Invoke-Python {
    param(
        [string]$Executable,
        [string[]]$PythonArguments
    )
    & $Executable @PythonArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Polecenie Python zakonczylo sie bledem (kod $LASTEXITCODE)."
    }
}

Push-Location $projectDir
try {
    if ($Service -eq "outbound" -and -not $PSBoundParameters.ContainsKey("Port")) {
        $Port = 8102
    }
    $appModule = "app.main:app"
    if ($Service -eq "outbound") {
        $appModule = "app.outbound:app"
    }

    if (-not (Test-Path ".venv")) {
        $pythonCommand = $null
        $pythonArguments = @()
        if (Get-Command py -ErrorAction SilentlyContinue) {
            $pythonCommand = "py"
            $pythonArguments = @("-3")
        }
        elseif (Get-Command python -ErrorAction SilentlyContinue) {
            $pythonCommand = "python"
        }
        else {
            throw "Zainstaluj Python 3.12 lub nowszy z python.org (z pip i launcherem py), a potem otworz nowy terminal."
        }

        $versionCheck = 'import sys; sys.exit(sys.version_info < (3, 12))'
        Invoke-Python -Executable $pythonCommand -PythonArguments ($pythonArguments + @("-c", $versionCheck))

        Invoke-Python -Executable $pythonCommand -PythonArguments ($pythonArguments + @("-m", "venv", ".venv"))
    }

    $venvPython = Join-Path $projectDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        throw "Nieprawidlowe .venv. Nie kopiuj go z innego komputera; usun je i uruchom skrypt ponownie."
    }
    $venvCheck = 'import sys; sys.exit(sys.version_info < (3, 12) or sys.prefix == sys.base_prefix)'
    Invoke-Python -Executable $venvPython -PythonArguments @("-c", $venvCheck)
    Invoke-Python -Executable $venvPython -PythonArguments @("-m", "pip", "--disable-pip-version-check", "install", "-r", "requirements.txt")

    if ($Test) {
        Invoke-Python -Executable $venvPython -PythonArguments @("-m", "pytest", "-q")
    }

    if ($SetupOnly) {
        Write-Host "Instalacja zakonczona. Aby uruchomic serwer: .\scripts\setup.ps1 -Service $Service"
    }
    else {
        [Environment]::SetEnvironmentVariable($servicePortVariable, "$Port", "Process")
        $serverArguments = @("-m", "uvicorn", $appModule, "--host", "127.0.0.1", "--port", "$Port")
        if (Test-Path ".env") {
            $serverArguments += @("--env-file", ".env")
        }
        Write-Host "$Service | Dokumentacja: http://127.0.0.1:$Port/docs | Zatrzymanie: Ctrl+C"
        Invoke-Python -Executable $venvPython -PythonArguments $serverArguments
    }
}
catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
}
finally {
    [Environment]::SetEnvironmentVariable($servicePortVariable, $previousServicePort, "Process")
    Pop-Location
}
