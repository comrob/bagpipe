#!/bin/bash

# Configuration
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"

# Forward help directly to the Python CLI help output.
if [[ "$1" == "--help" || "$1" == "-h" ]]; then
    export HOST_DATA_DIR="$PWD"
    export CURRENT_UID=$(id -u)
    export CURRENT_GID=$(id -g)

    docker compose -f "$COMPOSE_FILE" run --rm \
        -e HOST_DATA_DIR \
        -w /data \
        converter \
        python3 /home/dev/src/convert.py --help
    exit $?
fi

if [ -z "$1" ]; then
    echo "Usage: convert_bag <input_path> [options]"
    echo "Run 'convert_bag --help' to see all arguments."
    exit 1
fi

# ==============================================================================
# 1. DETERMINISTIC MOUNT CALCULATION
# ==============================================================================

# A. Handle INPUT (first positional path, options may come first)
# ------------------------------------------------------------------------------
ARGS=("$@")
INPUT_RAW=""
skip_next_scan=false
skip_topics_scan=false

for ((i=0; i<${#ARGS[@]}; i++)); do
    arg="${ARGS[i]}"

    if [[ "$skip_next_scan" == true ]]; then
        skip_next_scan=false
        continue
    fi

    if [[ "$skip_topics_scan" == true ]]; then
        if [[ "$arg" == -* ]]; then
            skip_topics_scan=false
        else
            continue
        fi
    fi

    if [[ "$arg" == "--" ]]; then
        NEXT_IDX=$((i+1))
        if (( NEXT_IDX < ${#ARGS[@]} )); then
            INPUT_RAW="${ARGS[NEXT_IDX]}"
        fi
        break
    fi

    case "$arg" in
        --out-dir|-o|--split-size|--distro)
            skip_next_scan=true
            continue
            ;;
        --skip-topics)
            skip_topics_scan=true
            continue
            ;;
        --out-dir=*|-o=*|--split-size=*|--distro=*)
            continue
            ;;
        -*)
            continue
            ;;
        *)
            INPUT_RAW="$arg"
            break
            ;;
    esac
done

if [[ -z "$INPUT_RAW" ]]; then
    echo "Error: missing input path."
    echo "Usage: convert_bag <input_path> [options]"
    exit 1
fi

INPUT_ABS=$(realpath -m -- "$INPUT_RAW")
MOUNT_HOST=$(dirname "$INPUT_ABS")

# B. Handle OUTPUT (Scan specifically for --out-dir)
# ------------------------------------------------------------------------------
OUTPUT_ABS=""
for ((i=0; i<$#; i++)); do
    arg="${ARGS[i]}"
    raw_out=""

    if [[ "$arg" == "--out-dir" || "$arg" == "-o" ]]; then
        # The NEXT argument is the output path
        NEXT_IDX=$((i+1))
        raw_out="${ARGS[NEXT_IDX]}"
    elif [[ "$arg" == --out-dir=* || "$arg" == -o=* ]]; then
        raw_out="${arg#*=}"
    fi

    if [[ -n "$raw_out" ]]; then
        # Ensure output directory exists on host
        mkdir -p "$raw_out"

        OUTPUT_ABS=$(realpath -m -- "$raw_out")

        # Expand MOUNT_HOST to include this output path
        while [[ "$OUTPUT_ABS" != "$MOUNT_HOST"* ]]; do
            MOUNT_HOST=$(dirname "$MOUNT_HOST")
            if [[ "$MOUNT_HOST" == "/" ]]; then break; fi
        done
        break
    fi
done

# ==============================================================================
# 2. STRICT RELATIVIZATION
# ==============================================================================
PY_ARGS=""
skip_next=false

for arg in "$@"; do
    if [ "$skip_next" = true ]; then
        # The previous argument was --out-dir/-o; relativize its value.
        if [[ -n "$OUTPUT_ABS" ]]; then
            REL_PATH=$(realpath -m --relative-to="$MOUNT_HOST" -- "$OUTPUT_ABS")
            PY_ARGS="$PY_ARGS $REL_PATH"
        else
            PY_ARGS="$PY_ARGS $arg"
        fi
        skip_next=false
        continue
    fi

    # Handle output option forms explicitly.
    if [[ "$arg" == "--out-dir" || "$arg" == "-o" ]]; then
        PY_ARGS="$PY_ARGS $arg"
        skip_next=true
        continue
    fi

    if [[ "$arg" == --out-dir=* || "$arg" == -o=* ]]; then
        FLAG_NAME="${arg%%=*}"
        FLAG_VALUE="${arg#*=}"

        if [[ -n "$OUTPUT_ABS" && "$(realpath -m -- "$FLAG_VALUE")" == "$OUTPUT_ABS" ]]; then
            REL_PATH=$(realpath -m --relative-to="$MOUNT_HOST" -- "$OUTPUT_ABS")
            PY_ARGS="$PY_ARGS $FLAG_NAME=$REL_PATH"
        else
            PY_ARGS="$PY_ARGS $arg"
        fi
        continue
    fi
    
    # [FIX] Skip flags starting with '-' so realpath doesn't crash on them
    if [[ "$arg" == -* ]]; then
        PY_ARGS="$PY_ARGS $arg"
        continue
    fi

    # Case 1: It matches the known INPUT path
    if [[ "$(realpath -m -- "$arg")" == "$INPUT_ABS" ]]; then
        REL_PATH=$(realpath -m --relative-to="$MOUNT_HOST" -- "$INPUT_ABS")
        PY_ARGS="$PY_ARGS $REL_PATH"

    # Case 2: It matches the known OUTPUT path
    elif [[ -n "$OUTPUT_ABS" && "$(realpath -m -- "$arg")" == "$OUTPUT_ABS" ]]; then
        REL_PATH=$(realpath -m --relative-to="$MOUNT_HOST" -- "$OUTPUT_ABS")
        PY_ARGS="$PY_ARGS $REL_PATH"
        
    # Case 3: Pass everything else through untouched (Topics, Numbers, etc)
    else
        PY_ARGS="$PY_ARGS $arg"
    fi
done

# ==============================================================================
# 3. EXECUTION
# ==============================================================================
echo "[DEBUG] Host Mount: $MOUNT_HOST"
echo "[DEBUG] Docker Args: $PY_ARGS"

export HOST_DATA_DIR="$MOUNT_HOST"
export CURRENT_UID=$(id -u)
export CURRENT_GID=$(id -g)

docker compose -f "$COMPOSE_FILE" run --rm \
    -e HOST_DATA_DIR \
    -w /data \
    converter \
    python3 /home/dev/src/convert.py $PY_ARGS