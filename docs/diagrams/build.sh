#!/usr/bin/env bash
# Render all PlantUML diagrams of the documentation (SVG + PNG).
#
#   ./build.sh            # render everything
#   ./build.sh -tsvg      # only SVG (any extra PlantUML option is passed through)
#
# Needs "plantuml" in PATH (jar + wrapper, see README.md) and GraphViz "dot".
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

if ! command -v plantuml >/dev/null 2>&1; then
    echo "plantuml not found - install it first (see README.md in this folder)." >&2
    exit 1
fi
if ! command -v dot >/dev/null 2>&1; then
    echo "Hint: GraphViz (dot) is missing - some diagram types may look wrong." >&2
fi

shopt -s nullglob
files=(*.puml)
if [[ ${#files[@]} -eq 0 ]]; then
    echo "No .puml files found." >&2
    exit 1
fi

echo "==> Rendering ${#files[@]} diagrams ..."
if [[ $# -gt 0 ]]; then
    plantuml -charset UTF-8 "$@" "${files[@]}"
else
    plantuml -tsvg -charset UTF-8 "${files[@]}"
    plantuml -tpng -charset UTF-8 "${files[@]}"
fi

missing=0
for f in "${files[@]}"; do
    base="${f%.puml}"
    for ext in svg png; do
        [[ -f "$base.$ext" ]] || { echo "  missing: $base.$ext" >&2; missing=1; }
    done
done
[[ "$missing" == 0 ]] && echo "==> Done: $(ls -1 ./*.svg | wc -l) SVG + $(ls -1 ./*.png | wc -l) PNG"
