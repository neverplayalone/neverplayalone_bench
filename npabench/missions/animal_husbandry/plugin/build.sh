#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 ]]; then
  echo "Usage: build.sh /path/to/paper-server-data" >&2
  exit 2
fi
animal_plugin_dir=$(cd "$(dirname "$0")" && pwd)
animal_libraries_dir=$(cd "$1/libraries" && pwd)
animal_build_dir=$(mktemp -d)
trap 'rm -rf -- "$animal_build_dir"' EXIT
animal_classpath=$(find "$animal_libraries_dir" -name '*.jar' -print | paste -sd: -)
javac --release 21 -cp "$animal_classpath" -d "$animal_build_dir" \
  "$animal_plugin_dir/src/main/java/org/npabench/animals/"*.java
cp "$animal_plugin_dir/src/main/resources/"* "$animal_build_dir/"
jar --create --file "$animal_plugin_dir/npabench-animal-husbandry.jar" -C "$animal_build_dir" .
