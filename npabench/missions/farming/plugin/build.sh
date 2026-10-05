#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: build.sh /path/to/paper-server-data" >&2
  exit 2
fi

farming_plugin_dir=$(cd "$(dirname "$0")" && pwd)
farming_libraries_dir=$1/libraries
if [[ ! -d "$farming_libraries_dir/io/papermc/paper/paper-api" ]]; then
  echo "Paper API libraries not found in $farming_libraries_dir" >&2
  exit 1
fi

farming_build_dir=$(mktemp -d)
trap 'rm -r -- "$farming_build_dir"' EXIT
farming_classpath=$(find "$farming_libraries_dir" -name '*.jar' -print | paste -sd: -)
javac --release 21 -cp "$farming_classpath" -d "$farming_build_dir" \
  "$farming_plugin_dir/src/main/java/org/npabench/farming/FarmingLedgerPlugin.java"
cp "$farming_plugin_dir/src/main/resources/plugin.yml" "$farming_build_dir/plugin.yml"
cp "$farming_plugin_dir/src/main/resources/config.yml" "$farming_build_dir/config.yml"
jar --create --file "$farming_plugin_dir/npabench-farming-ledger.jar" -C "$farming_build_dir" .
