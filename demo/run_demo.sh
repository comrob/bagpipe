#!/bin/bash
set -e  # Exit immediately on error

# ==============================================================================
# STEP 0: LOCATION INDEPENDENCE
# ==============================================================================
# 1. Get the absolute path of the directory containing this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# 2. Move into that directory so all relative paths work
cd "$SCRIPT_DIR"

echo "[INFO] Running demo from: $SCRIPT_DIR"

# --- Configuration (Relative to SCRIPT_DIR) ---
DATA_DIR="data"
INPUT_DIR="$DATA_DIR/inputs"
OUTPUT_DIR="$DATA_DIR/outputs"

# Specific Subfolders
INPUT_SEQ="$INPUT_DIR/sequence"
INPUT_PLUGIN="$INPUT_DIR/debayer"

LAYOUT_FILE="foxglove_layout.json"
BAGPIPE="../bagpipe.sh"
CONVERTER_SCRIPT="$BAGPIPE convert"

# Google Drive IDs
ID_BAG_0="1u-dvvvne-OXfz2YMDGVg2FBtSolf2pNk"
ID_BAG_1="1TQjpWbwi5CyzFpfE1lIxQR-w3Q16340K"
ID_BAG_2="1xlR7QFL9wKGkANAENt3HxEPX7KY2IbMH"
ID_BAG_DEBAYER="15EIfn-Y8oaYU2eOhSZITvcI_6zjbaOC6"
ID_LAYOUT="15DyLCDZd7rhZ3nwSuKCv-ZdzOwjEyt6H"

# --- Helper Functions ---
function download_if_missing() {
    local file_id=$1
    local output_name=$2
    local parent_dir=$(dirname "$output_name")
    
    mkdir -p "$parent_dir"
    
    if [ ! -f "$output_name" ]; then
        echo "[DOWNLOAD] Fetching $output_name..."
        gdown "$file_id" -O "$output_name"
    else
        echo "[SKIP] $output_name already exists."
    fi
}

function print_header() {
    echo -e "\n========================================"
    echo "  $1"
    echo "========================================"
}

# ==============================================================================
# STEP 1: VIRTUAL ENVIRONMENT SETUP
# ==============================================================================
print_header "STEP 1: Checking Dependencies"

if [ -f "./setup_venv.sh" ]; then
    if [ ! -x "./setup_venv.sh" ]; then chmod +x "./setup_venv.sh"; fi
    ./setup_venv.sh
else
    echo "[ERR] setup_venv.sh not found in $SCRIPT_DIR!"
    exit 1
fi

source .venv/bin/activate
echo "[INFO] Virtual environment activated: $(which python3)"

if [ ! -x "$BAGPIPE" ]; then
    chmod +x "$BAGPIPE"
fi

# ==============================================================================
# STEP 2: SETUP & DOWNLOAD
# ==============================================================================
print_header "STEP 2: Organizing Test Data"
mkdir -p "$INPUT_SEQ"
mkdir -p "$INPUT_PLUGIN"
mkdir -p "$OUTPUT_DIR"

download_if_missing "$ID_BAG_0" "$INPUT_SEQ/test_0.bag"
download_if_missing "$ID_BAG_1" "$INPUT_SEQ/test_1.bag"
download_if_missing "$ID_BAG_2" "$INPUT_SEQ/test_2.bag"
download_if_missing "$ID_BAG_DEBAYER" "$INPUT_PLUGIN/test_debayer.bag"
download_if_missing "$ID_LAYOUT" "$DATA_DIR/$LAYOUT_FILE"

# ==============================================================================
# STEP 3: TEST CASE: Single File Conversion
# ==============================================================================
print_header "STEP 3: Testing Single File Conversion"

OUT_SINGLE="$OUTPUT_DIR/case1_single"
echo "[INFO] Target Output: $OUT_SINGLE"
rm -rf "$OUT_SINGLE"

# Turn on echo for the command
set -x
$CONVERTER_SCRIPT "$INPUT_SEQ/test_0.bag" --out-dir "$OUT_SINGLE"
set +x

if [ -f "$OUT_SINGLE/test_0.mcap" ]; then
    echo "✅ Case 1 successful."
else
    echo "❌ Failed."
    exit 1
fi

# ==============================================================================
# STEP 4: TEST CASE: Full Folder Conversion (Replaces old Sequence test)
# ==============================================================================
print_header "STEP 4: Testing Folder Input (Series Mode)"

OUT_FOLDER="$OUTPUT_DIR/case3_folder"
echo "[INFO] Target Output: $OUT_FOLDER"
rm -rf "$OUT_FOLDER"

set -x
# We pass the FOLDER, not the individual files. This works with the Simple script.
$CONVERTER_SCRIPT "$INPUT_SEQ" --series --out-dir "$OUT_FOLDER"
set +x

if [ -f "$OUT_FOLDER/test_0.mcap" ]; then
    echo "✅ Case 3 successful."
else
    echo "❌ Failed."
    exit 1
fi

# ==============================================================================
# STEP 5: TEST CASE: Splitting by Size
# ==============================================================================
print_header "STEP 5: Testing Split (Limit 50MB)"

OUT_SPLIT="$OUTPUT_DIR/case4_split"
echo "[INFO] Target Output: $OUT_SPLIT"
rm -rf "$OUT_SPLIT"

set -x
$CONVERTER_SCRIPT "$INPUT_SEQ/test_0.bag" --out-dir "$OUT_SPLIT" --split-size 50M
set +x

if [ -f "$OUT_SPLIT/test_0_1.mcap" ]; then
    echo "✅ Case 4 successful."
else
    echo "❌ Failed."
    exit 1
fi

# ==============================================================================
# STEP 6: TEST CASE: Plugin Functionality (Debayer)
# ==============================================================================
print_header "STEP 6: Testing Plugins (Debayer)"

OUT_PLUGIN="$OUTPUT_DIR/case5_plugin"
echo "[INFO] Target Output: $OUT_PLUGIN"
rm -rf "$OUT_PLUGIN"

set -x
$CONVERTER_SCRIPT "$INPUT_PLUGIN/test_debayer.bag" --out-dir "$OUT_PLUGIN" --with-plugins
set +x

if [ -f "$OUT_PLUGIN/test_debayer.mcap" ]; then
    echo "✅ Case 5 successful."
else
    echo "❌ Failed."
    exit 1
fi

# ==============================================================================
# STEP 7: TEST CASE: Dry Run
# ==============================================================================
print_header "STEP 7: Testing Dry Run Mode"
OUT_DRY="$OUTPUT_DIR/case6_dryrun"

# We run with --dry-run. This should NOT produce files, but should exit with 0.
set -x
$CONVERTER_SCRIPT "$INPUT_SEQ" --series --out-dir "$OUT_DRY" --dry-run
set +x

if [ ! -d "$OUT_DRY" ]; then
    echo "✅ Success: Dry run finished without creating output folders/files."
else
    if [ -z "$(ls -A $OUT_DRY 2>/dev/null)" ]; then
         echo "✅ Success: Output folder is empty or non-existent."
    else
         echo "❌ Failure: Dry run created files!"
         exit 1
    fi
fi

# ==============================================================================
# STEP 8: VISUALIZATION
# ==============================================================================
print_header "STEP 8: Done"
echo "Results are in: $OUTPUT_DIR"
echo "To view in Foxglove:"
echo "  1. Layout: $SCRIPT_DIR/$DATA_DIR/$LAYOUT_FILE"
echo "  2. Drag any folder from 'data/outputs/' into the app."