#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# gate.sh <name> <item>... -- decide whether a CI job/step whose component may not exist yet should run.
# <item> is a path or glob, or "make:<target>" (the Makefile has that target).
#
# All paths present  -> "enabled=true" on $GITHUB_OUTPUT, exit 0.
# Something missing  -> CI_STRICT=1: error annotation, exit 1 (the component is required from now on).
#                       otherwise: notice annotation, "enabled=false", exit 0 (skeleton phase, docs/MAINTAINER.md §2).
set -euo pipefail
name="${1:?usage: gate.sh <name> <path>...}"
shift
out="${GITHUB_OUTPUT:-/dev/stdout}"
missing=()
for p in "$@"; do
  case "$p" in
    make:*)
      if ! make -n "${p#make:}" > /dev/null 2>&1; then missing+=("$p"); fi
      ;;
    *)
      if ! compgen -G "$p" > /dev/null; then missing+=("$p"); fi
      ;;
  esac
done
if [ "${#missing[@]}" -eq 0 ]; then
  echo "enabled=true" >> "$out"
  exit 0
fi
if [ "${CI_STRICT:-}" = "1" ]; then
  echo "::error title=${name}::required component missing: ${missing[*]}"
  exit 1
fi
echo "::notice title=${name} not active yet::component not present yet: ${missing[*]} (repository variable CI_STRICT=1 makes this an error)"
echo "enabled=false" >> "$out"
