#!/usr/bin/env bash
set -e

echo "=== Setting up Unattended Script-to-Video Browser-Automation Pipeline ==="

# 1. Verify Python 3 (>=3.10)
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
    if awk 'BEGIN {exit !('"$PY_VER"' >= 3.10)}'; then
        echo "Found Python >= 3.10: $PY_VER"
    else
        echo "Error: Python 3.10 or higher is required. Found $PY_VER."
        exit 1
    fi
else
    echo "Error: Python 3 is not installed. Please install Python 3.10+."
    exit 1
fi

# 2. Install Homebrew if missing, then ffmpeg
if ! command -v brew &>/dev/null; then
    echo "Homebrew not found. Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
if ! command -v ffmpeg &>/dev/null; then
    echo "Installing ffmpeg..."
    brew install ffmpeg
else
    echo "ffmpeg is already installed."
fi

# 3. Create a venv and install dependencies
echo "Creating Python virtual environment in .venv..."
python3 -m venv .venv
source .venv/bin/activate

echo "Installing pip dependencies..."
pip install patchright pandas openpyxl pillow pyperclip requests

# 4. Patchright install chrome
echo "Installing Patchright browsers..."
patchright install chrome

# 5. Confirm Chrome Profile Paths
CHROME_SOURCE_DIR="$HOME/Library/Application Support/Google/Chrome"
if [ ! -d "$CHROME_SOURCE_DIR" ]; then
    echo "Warning: Default Chrome data directory not found at $CHROME_SOURCE_DIR"
else
    echo "Found Chrome data directory."
    echo "Available profiles (folders):"
    ls -1d "$CHROME_SOURCE_DIR"/*/ | grep -E 'Default|Profile' | xargs -n 1 basename
fi

# 6. Generate the transparent 16:9 ruler PNG
echo "Generating 16:9 transparent ruler PNG..."
cat << 'EOF' > generate_ruler.py
from PIL import Image
img = Image.new('RGBA', (1920, 1080), (0, 0, 0, 0))
img.save('ruler_16x9.png')
print("Saved ruler_16x9.png")
EOF
python3 generate_ruler.py
rm generate_ruler.py

# 7. Print IMPORTANT REMINDERS and require Acknowledgement
echo ""
echo "========================================================================="
echo "                          IMPORTANT REMINDERS                            "
echo "========================================================================="
echo "1. Log in to ChatGPT, Gemini, and SuperGrok in Chrome normally first."
echo "2. Cmd+Q Chrome **completely** before the first run (so the profile can be cloned without a lock)."
echo "3. Don't touch the mouse/keyboard during the overnight run."
echo "4. **Automating these services may violate their Terms of Service and can get your paid accounts throttled or banned. You accept that risk.**"
echo "5. Grok's daily video cap means a 15-scene project may span **2+ nights** — just rerun with --resume."
echo "6. The final 1080p file is upscaled from Grok's 720p output."
echo "7. CapCut is NOT automated: import Final/final_stitched.mp4 into CapCut for voiceover, captions, music, and final export (1920x1080, 30fps, H.264)."
echo "========================================================================="
echo ""

while true; do
    read -p "Type 'I ACCEPT' to acknowledge these risks and finish setup: " ACCEPT_INPUT
    if [ "$ACCEPT_INPUT" = "I ACCEPT" ]; then
        echo "Setup complete. You may now run pipeline.py"
        break
    else
        echo "You must type 'I ACCEPT' exactly to proceed."
    fi
done
