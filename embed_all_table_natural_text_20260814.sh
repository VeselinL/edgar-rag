#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHUNKS_DIRECTORY="$PROJECT_ROOT/data/chunks"
OUTPUT_ROOT="$PROJECT_ROOT/data/embeddings/table-natural_text-20260814"
PYTHON="$PROJECT_ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    PYTHON=python3
fi

companies=(
    aptiv
    aurora
    ford
    general_motors
    alphabet
    mobileye
    nvidia
    ouster
    qualcomm
    tesla
)

declare -A tickers=(
    [aptiv]=APTV
    [aurora]=AUR
    [ford]=F
    [general_motors]=GM
    [alphabet]=GOOGL
    [mobileye]=MBLY
    [nvidia]=NVDA
    [ouster]=OUST
    [qualcomm]=QCOM
    [tesla]=TSLA
)

for company in "${companies[@]}"; do
    ticker="${tickers[$company]}"
    shopt -s nullglob
    chunk_paths=("$CHUNKS_DIRECTORY/$ticker/"*-10-K.chunks.jsonl)
    shopt -u nullglob
    if (( ${#chunk_paths[@]} == 0 )); then
        echo "No chunk file found for $company ($ticker)." >&2
        exit 1
    fi

    mapfile -t chunk_paths < <(printf '%s\n' "${chunk_paths[@]}" | sort -r)
    chunk_path="${chunk_paths[0]}"
    chunk_name="$(basename "${chunk_path%.chunks.jsonl}")"
    output_path="$OUTPUT_ROOT/$ticker/$chunk_name.bgebase.embeddings.npz"

    "$PYTHON" -m src.embeddings.embed_chunks "$company" \
        --chunks-directory "$CHUNKS_DIRECTORY" \
        --output "$output_path" \
        --model-name bgebase \
        "$@"
done
