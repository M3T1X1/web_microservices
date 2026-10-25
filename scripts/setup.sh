#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Uzycie: bash scripts/setup.sh [--port 8101] [--test] [--setup-only]"
}

port=8101
run_tests=false
setup_only=false

while (($#)); do
    case "$1" in
        --port)
            if (($# < 2)); then
                usage >&2
                exit 1
            fi
            port="$2"
            shift 2
            ;;
        --test) run_tests=true; shift ;;
        --setup-only) setup_only=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Nieznany argument: $1" >&2; usage >&2; exit 1 ;;
    esac
done

if [[ ! "$port" =~ ^[1-9][0-9]{0,4}$ ]] || ((port > 65535)); then
    echo "Port musi byc liczba od 1 do 65535." >&2
    exit 1
fi

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$project_dir"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Zainstaluj Pythona: sudo apt update && sudo apt install python3 python3-venv" >&2
    exit 1
fi

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    echo "Wymagany Python 3.12 lub nowszy." >&2
    exit 1
fi

if [[ ! -d .venv ]]; then
    if ! python3 -m venv .venv; then
        echo "Nie udalo sie utworzyc .venv. W Debianie zainstaluj: sudo apt install python3-venv" >&2
        echo "Po naprawie usun niepelne .venv i uruchom skrypt ponownie." >&2
        exit 1
    fi
fi

venv_python="$project_dir/.venv/bin/python"
if [[ ! -x "$venv_python" ]] || ! "$venv_python" -c 'import sys; sys.exit(sys.version_info < (3, 12) or sys.prefix == sys.base_prefix)'; then
    echo "Nieprawidlowe .venv. Nie kopiuj go z innego komputera; usun je i uruchom skrypt ponownie." >&2
    exit 1
fi

"$venv_python" -m pip --disable-pip-version-check install -r requirements.txt

if "$run_tests"; then
    "$venv_python" -m pytest -q
fi

if "$setup_only"; then
    echo "Instalacja zakonczona. Aby uruchomic serwer: bash scripts/setup.sh"
    exit 0
fi

echo "Dokumentacja: http://127.0.0.1:$port/docs | Zatrzymanie: Ctrl+C"
exec "$venv_python" -m uvicorn app.main:app --host 127.0.0.1 --port "$port"
