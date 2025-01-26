#!/usr/bin/env bash

set -euo pipefail

# Default percentage of the default power limit
DEFAULT_PERCENTAGE=90

usage() {
    echo "Usage: sudo $0 [-p PERCENTAGE]"
    echo "  -p PERCENTAGE   Set power limit to PERCENTAGE%% of the default power limit (70-120, default: $DEFAULT_PERCENTAGE%%)"
    exit 1
}

# Parse command-line options
PERCENTAGE="$DEFAULT_PERCENTAGE"

while getopts ":p:h" opt; do
    case "$opt" in
        p) PERCENTAGE="$OPTARG" ;;
        h) usage ;;
        *) echo "Invalid option: -$OPTARG" >&2; usage ;;
    esac
done

# Validate PERCENTAGE (must be an integer between 70 and 120)
if ! [[ "$PERCENTAGE" =~ ^[0-9]+$ ]] || [ "$PERCENTAGE" -lt 70 ] || [ "$PERCENTAGE" -gt 120 ]; then
    echo "Error: PERCENTAGE must be an integer between 70 and 120." >&2
    exit 1
fi

# Ensure nvidia-smi exists
if ! command -v nvidia-smi >/dev/null; then
    echo "Error: nvidia-smi not found. NVIDIA drivers may not be installed." >&2
    exit 1
fi

# Get GPU indices and default power limits
mapfile -t GPU_INFO < <(nvidia-smi --query-gpu=index,power.default_limit --format=csv,noheader,nounits)

if [ "${#GPU_INFO[@]}" -eq 0 ]; then
    echo "Error: No NVIDIA GPUs detected." >&2
    exit 1
fi

# Apply power limit to each GPU
for INFO in "${GPU_INFO[@]}"; do
    IFS=',' read -r GPU_INDEX DEFAULT_POWER <<< "$INFO"
    GPU_INDEX="${GPU_INDEX//[[:blank:]]/}"
    DEFAULT_POWER="${DEFAULT_POWER//[[:blank:]]/}"

    NEW_POWER=$(awk "BEGIN {printf \"%d\", $DEFAULT_POWER * $PERCENTAGE / 100}")

    if nvidia-smi -i "$GPU_INDEX" -pl "$NEW_POWER" >/dev/null 2>&1; then
        echo "GPU $GPU_INDEX: Power limit set to $NEW_POWER W ($PERCENTAGE% of $DEFAULT_POWER W)."
    else
        echo "Error: Failed to set power limit for GPU $GPU_INDEX." >&2
        exit 1
    fi
done
