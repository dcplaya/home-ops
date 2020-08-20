#!/bin/bash
set -e

REPO_ROOT=$(git rev-parse --show-toplevel)
SECRETS_ROOT="${REPO_ROOT}/.secrets"
YAMLLINT_CONFIG="${REPO_ROOT}/.github/yamllint.config"

need() {
    command -v "$1" &>/dev/null || (echo "Binary '$1' is missing but required" && exit 1)
}

need "yamllint"

message() {
  echo -e "\n######################################################################"
  echo "# $1"
  echo "######################################################################"
}

message "Running YAML lint on all YAML files in repository (except for .secrets folder)"

FILES_TO_PROCESS=$(find "${REPO_ROOT}" -type d -path "$SECRETS_ROOT" -prune -o \( -type f -name '*.yaml' -or -name '*.yml' \) -print)

# Loop over the files that should be processed
set +e
FAILED=0
while IFS= read -r file; do
  echo "- Processing $file"
  yamllint -c "$YAMLLINT_CONFIG" "$file"
  if [[ $? == 1 ]]; then
    FAILED=1
  fi
done <<< "$FILES_TO_PROCESS"
set -e

message "all done!"

if [[ $FAILED != 0 ]]; then
  exit $FAILED
fi