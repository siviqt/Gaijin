#!/usr/bin/env bash
set -euo pipefail
: "${IDF_PATH:=$HOME/.espressif/v6.0.2/esp-idf}"
export IDF_PATH
python3 tools/build_home_seed.py
# EIM installs can keep tools and their Python environment under this directory.
if [[ -z "${IDF_TOOLS_PATH:-}" && -d "$HOME/.espressif/tools/python_env" ]]; then
    export IDF_TOOLS_PATH="$HOME/.espressif/tools"
fi
if [[ -z "${IDF_PYTHON_ENV_PATH:-}" && -x "${IDF_TOOLS_PATH:-$HOME/.espressif}/python_env/idf6.0_py3.13_env/bin/python" ]]; then
    export IDF_PYTHON_ENV_PATH="${IDF_TOOLS_PATH:-$HOME/.espressif}/python_env/idf6.0_py3.13_env"
fi
source "$IDF_PATH/export.sh"
idf.py -B build/loader -DIDF_TARGET=esp32p4 build
