#Requires -Version 5.1

[CmdletBinding()]
param(
    [ValidateSet("all", "inventory", "outbound", "shipping", "auth")]
    [string]$Service = "all",
    [ValidateRange(1, 65535)]
    [int]$Port,
    [switch]$Test,
    [switch]$SetupOnly
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot
if ($Service -eq "all" -and $PSBoundParameters.ContainsKey("Port")) {
    throw "Parametr -Port wymaga wybrania jednej uslugi przez -Service."
}

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
        Write-Host "Instalacja zakonczona. Caly system: .\scripts\setup.ps1; jedna usluga: .\scripts\setup.ps1 -Service inventory"
    }
    else {
        $serverArguments = @("scripts/run_services.py", "--service", $Service)
        if ($PSBoundParameters.ContainsKey("Port")) {
            $serverArguments += @("--port", "$Port")
        }
        Invoke-Python -Executable $venvPython -PythonArguments $serverArguments
    }
}
catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
}
finally {
    Pop-Location
}
