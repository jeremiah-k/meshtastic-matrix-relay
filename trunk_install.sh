#!/bin/bash

set -euo pipefail

check_mark="\033[1;32m✔\033[0m"
header() { echo -e "\n\033[1m$1\033[0m"; }

header "Welcome to Trunk!\n"
echo " This will download and install the latest Trunk Launcher. When you run 'trunk'"
echo " you'll really be running the Trunk Launcher, which figures out which version of"
echo " Trunk to run depending on which repo you're in and downloads it if needed. The"
echo " first time you use Trunk in a repo it will download the latest version."

bin_path="/usr/local/bin"
install_path="${bin_path}/trunk"
if ! [[ -d ${bin_path} ]]; then
  if [[ -n ${USERPROFILE-} ]]; then
    # Windows only
    bin_path="${USERPROFILE:-${HOME}}\\\\bin"
    install_path="${bin_path}\\\\trunk"
  else
    bin_path="${HOME}/.local/bin"
    install_path="${bin_path}/trunk"
  fi
fi

header "\nThis script will install\n"
echo -e " \033[4m${install_path}\033[0m\n"

header "Downloading and Installing\n"

echo "  Downloading Trunk Launcher..."
tmp_file=$(mktemp)
curl -fsSL https://trunk.io/releases/trunk -o "${tmp_file}"
echo -e "\033[1A ${check_mark} Downloading Trunk Launcher... done"

echo "   Installing Trunk Launcher (requires sudo access)..."
chmod +x "${tmp_file}"
$(command -v sudo || true) bash -c "mkdir -p '${bin_path}' && mv ${tmp_file} '${install_path}'"
echo -e " ${check_mark} Installing Trunk Launcher... done\n"

# Keep this in sync with init.cc
header "Next Steps\n"
echo -e " 1. \033[1mInitialize trunk in a repo\033[0m"
echo -e "    \033[0;90mRun \033[0;36mtrunk init\033[0;90m to setup trunk in a git repo\033[0m\n"
echo -e " 2. \033[1mAnalyze your flaky tests\033[0m"
echo -e "    \033[0;90mRun \033[0;36mtrunk flakytests\033[0;90m to start uploading test files\033[0m\n"
echo -e " 3. \033[1mRead documentation\033[0m"
echo -e "    \033[0;90mOur documentation can be found at \033[0;4mhttps://docs.trunk.io\033[0m\n"
echo -e " 4. \033[1mGet help and give feedback\033[0m"
echo -e "    \033[0;90mJoin the trunk community at \033[0;4mhttps://slack.trunk.io\033[0m"
