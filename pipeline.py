import os
import sys
import json
import time
import shutil
import random
import argparse
import subprocess
import logging
from datetime import datetime
from pathlib import Path

# Do NOT import playwright, only patchright
from patchright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import pandas as pd

# =============================================================================
# CONFIGURABLE CONSTANTS
# =============================================================================
CHROME_SOURCE_DIR   = os.path.expanduser("~/Library/Application Support/Google/Chrome")
AUTOMATION_PROFILE  = os.path.expanduser("~/.video_pipeline_chrome_profile")

MIN_SCENES = 10
MAX_SCENES = 15
EXCEL_SHEET = "Sheet1"
EXCEL_COL   = "Script"

TARGET_W, TARGET_H, FPS, CRF = 1920, 1080, 30, 18
GROK_DELAY_MIN = 45      # seconds between Grok generations
GROK_DELAY_MAX = 90
GROK_DAILY_VIDEO_BUDGET = 18   # conservative; stop before hitting hard cap

GEMINI_TIMEOUT  = 120 * 1000   # ms
GROK_TIMEOUT    = 480 * 1000   # ms
CHATGPT_TIMEOUT = 300 * 1000   # ms
DOM_STABLE_SECS = 3      # response considered done after DOM unchanged this long

MAX_RETRIES_PER_SCENE = 1
MIN_IMAGE_BYTES = 30_000
MIN_VIDEO_BYTES = 100_000

# Centralized Selectors
SELECTORS = {
    # ChatGPT Selectors
    "chatgpt_textbox": [
        {"role": "textbox", "name": "Message"},
        {"test_id": "prompt-textarea"},
        {"css": "#prompt-textarea"}
    ],
    "chatgpt_send_button": [
        {"test_id": "send-button"},
        {"role": "button", "name": "Send prompt"},
        {"css": "[data-testid='send-button']"}
    ],
    "chatgpt_stop_streaming": [
        {"role": "button", "name": "Stop generating"},
        {"css": "[aria-label='Stop generating']"}
    ],
    "chatgpt_response_container": [
        {"css": ".markdown.prose"},
        {"css": "[data-message-author-role='assistant']"}
    ],

    # Gemini Selectors
    "gemini_textbox": [
        {"role": "textbox", "name": "Enter a prompt here"},
        {"css": "rich-textarea"},
        {"css": "div[contenteditable='true']"}
    ],
    "gemini_upload_button": [
        {"role": "button", "name": "Upload image"},
        {"css": "button[aria-label='Upload image']"}
    ],
    "gemini_send_button": [
        {"role": "button", "name": "Send message"},
        {"css": "button[aria-label='Send message']"}
    ],
    "gemini_image_container": [
        {"css": "img[src^='blob:']"},
        {"css": "img[data-test-id='image-attachment']"},
        {"css": ".model-response img"}
    ],

    # Grok Selectors
    "grok_imagine_tab": [
        {"role": "tab", "name": "Imagine"},
        {"text": "Imagine"}
    ],
    "grok_textbox": [
        {"role": "textbox", "name": "Ask anything"},
        {"css": "textarea[placeholder*='Ask anything']"}
    ],
    "grok_upload_button": [
        {"role": "button", "name": "Attach images"},
        {"css": "button[aria-label='Attach images']"}
    ],
    "grok_send_button": [
        {"role": "button", "name": "Send"},
        {"css": "button[aria-label='Send']"}
    ],
    "grok_video_element": [
        {"css": "video"},
        {"role": "video"}
    ],
    "grok_limit_indicators": [
        {"text": "limit"},
        {"text": "try again later"},
        {"text": "content moderated"},
        {"text": "upgrade"}
    ]
}

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def setup_logging():
    logging.basicConfig(
        filename='pipeline_log.txt',
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

def log(msg, level=logging.INFO):
    print(msg)
    logging.log(level, msg)

def setup_caffeinate():
    """Ensure the script runs under `caffeinate -dimsu` to prevent sleep."""
    if os.environ.get("CAFFEINATED") != "1":
        print("Relaunching under caffeinate -dimsu...")
        env = os.environ.copy()
        env["CAFFEINATED"] = "1"
        cmd = ["caffeinate", "-dimsu", sys.executable] + sys.argv
        os.execvpe(cmd[0], cmd, env)

def notify(message, title="Video Pipeline"):
    """macOS desktop notifications via osascript."""
    log(f"[{title}] {message}", level=logging.WARNING)
    if sys.platform == "darwin":
        try:
            script = f'display notification "{message}" with title "{title}"'
            subprocess.run(["osascript", "-e", script], capture_output=True)
        except Exception:
            pass

def clone_chrome_profile(source_profile_name, dest_dir):
    """Clone the Chrome profile to isolate risk and avoid locks."""
    source_dir = os.path.join(CHROME_SOURCE_DIR, source_profile_name)
    if not os.path.exists(source_dir):
        print(f"Error: Source profile not found at {source_dir}")
        sys.exit(1)

    log(f"Cloning Chrome profile '{source_profile_name}' to {dest_dir}...")
    log("Ensure Chrome is fully quit (Cmd+Q) before doing this.")

    try:
        # Using rsync to copy the profile directory efficiently.
        # We only need the Default/Profile N directory itself, plus Local State.
        os.makedirs(dest_dir, exist_ok=True)

        # Copy 'Local State' if it exists in the root Chrome dir (important for auth in some cases)
        local_state_src = os.path.join(CHROME_SOURCE_DIR, "Local State")
        if os.path.exists(local_state_src):
            shutil.copy(local_state_src, os.path.join(dest_dir, "Local State"))

        # Target profile dir inside the clone
        target_profile_dir = os.path.join(dest_dir, source_profile_name)

        if not os.path.exists(target_profile_dir):
            subprocess.run(["rsync", "-a", f"{source_dir}/", f"{target_profile_dir}/"], check=True)
            log("Profile cloned successfully.")
        else:
            log("Target profile already exists, skipping clone.")
    except Exception as e:
        log(f"Failed to clone profile: {e}", level=logging.ERROR)
        sys.exit(1)

def resolve_selector(page, action_name):
    """Attempt to find a selector based on the fallback list."""
    strategies = SELECTORS.get(action_name, [])
    for strat in strategies:
        try:
            if "role" in strat:
                locator = page.get_by_role(strat["role"], name=strat.get("name"))
            elif "test_id" in strat:
                locator = page.get_by_test_id(strat["test_id"])
            elif "placeholder" in strat:
                locator = page.get_by_placeholder(strat["placeholder"])
            elif "text" in strat:
                locator = page.get_by_text(strat["text"])
            elif "css" in strat:
                locator = page.locator(strat["css"])
            else:
                continue

            # Quick check if it resolves to something visible/attached
            if locator.count() > 0:
                return locator.first
        except Exception:
            continue

    # Missed all selectors
    return None

def take_debug_screenshot(page, name):
    os.makedirs("debug", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"debug/{ts}_{name}.png"
    try:
        page.screenshot(path=path, full_page=True)
        log(f"Saved debug screenshot: {path}")
    except Exception as e:
        log(f"Failed to save debug screenshot: {e}", level=logging.ERROR)

def write_clipboard(page, text):
    """Write text to clipboard and trigger a paste in the focused element."""
    # Since pyperclip needs a display, and patchright has evaluate, we use page.evaluate for clipboard
    # However, for robustness in editors like Prosemirror (ChatGPT/Gemini), dispatching a paste event or
    # using keyboard insert is best.
    page.evaluate("text => navigator.clipboard.writeText(text)", text)
    page.keyboard.press("Meta+V" if sys.platform == "darwin" else "Control+V")
    time.sleep(0.5)

def get_editor_text(page, selector_name):
    """Read the editor back to verify text landed."""
    loc = resolve_selector(page, selector_name)
    if loc:
        return loc.inner_text()
    return ""

def load_checkpoint(project_dir):
    """Load the checkpoint file if it exists."""
    cp_path = os.path.join(project_dir, "checkpoint.json")
    if os.path.exists(cp_path):
        try:
            with open(cp_path, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "stage": 1,
        "last_scene": 0,
        "successful_scenes": [],
        "grok_videos_used_today": 0,
        "date": datetime.now().strftime("%Y-%m-%d")
    }

def save_checkpoint(project_dir, cp_data):
    """Save the checkpoint file."""
    cp_path = os.path.join(project_dir, "checkpoint.json")
    with open(cp_path, 'w') as f:
        json.dump(cp_data, f, indent=2)

def extract_json_from_text(text):
    """Locate the first balanced [ ... ] via bracket matching; strip code fences; json.loads."""
    import re
    # Strip markdown code fences
    text = re.sub(r'```json', '', text)
    text = re.sub(r'```', '', text)

    start_idx = text.find('[')
    if start_idx == -1:
        return None

    # Bracket matching to find the end
    depth = 0
    end_idx = -1
    for i in range(start_idx, len(text)):
        if text[i] == '[':
            depth += 1
        elif text[i] == ']':
            depth -= 1
            if depth == 0:
                end_idx = i + 1
                break

    if end_idx != -1:
        json_str = text[start_idx:end_idx]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
    return None

def run_stage_1(args, project_dir, checkpoint):
    """STAGE 1: Read Excel"""
    log("--- STAGE 1: Read Excel ---")
    prompts_dir = os.path.join(project_dir, "Prompts")
    os.makedirs(prompts_dir, exist_ok=True)

    try:
        df = pd.read_excel(args.excel, sheet_name=EXCEL_SHEET)
        if EXCEL_COL not in df.columns:
            raise ValueError(f"Column '{EXCEL_COL}' missing in Excel file.")

        # Join non-empty rows
        script_texts = df[EXCEL_COL].dropna().astype(str).tolist()
        full_script = "\n\n".join(script_texts)

        raw_script_path = os.path.join(prompts_dir, "raw_script.txt")
        with open(raw_script_path, "w") as f:
            f.write(full_script)

        log(f"Loaded script from {args.excel} ({len(script_texts)} rows). Saved to {raw_script_path}")

        checkpoint["stage"] = 2
        save_checkpoint(project_dir, checkpoint)
        return full_script
    except Exception as e:
        msg = f"Stage 1 Error: {e}"
        notify(msg)
        sys.exit(1)

def run_stage_2(page, project_dir, checkpoint, script_text, args):
    """STAGE 2: ChatGPT Scene Breakdown"""
    log("--- STAGE 2: ChatGPT Scene Breakdown ---")
    prompts_dir = os.path.join(project_dir, "Prompts")
    scenes_file = os.path.join(prompts_dir, "scenes.json")

    if args.resume and os.path.exists(scenes_file):
        log("Scenes JSON already exists, skipping ChatGPT breakdown.")
        checkpoint["stage"] = 3
        save_checkpoint(project_dir, checkpoint)
        try:
            with open(scenes_file, "r") as f:
                return json.load(f)
        except Exception:
            pass

    log("Navigating to chatgpt.com...")
    page.goto("https://chatgpt.com/?model=auto")
    page.wait_for_load_state("networkidle")

    # Check if logged in
    if page.locator("text=Log in").count() > 0 or page.locator("text=Sign in").count() > 0:
        notify("Session expired on ChatGPT — re-login in Chrome, then --resume")
        sys.exit(10)

    prompt = (
        "Act as a professional cinematic storyboard artist. Divide the script into 10–15 scenes (more if the script is long). "
        "Return ONLY a valid JSON array, no markdown, no prose. Each object must have: "
        "scene_number (int), title (≤8 words), narration (script excerpt), "
        "image_prompt (60–80 words, ultra-realistic cinematic, 16:9, with lighting, camera angle, depth of field, environment, mood, color palette, and a consistent recurring character/style description so all scenes match), "
        "duration_secs (5–15).\n\n"
        "SCRIPT:\n" + script_text
    )

    textbox = resolve_selector(page, "chatgpt_textbox")
    if not textbox:
        take_debug_screenshot(page, "chatgpt_textbox_miss")
        raise Exception("Could not find ChatGPT textbox.")

    textbox.focus()
    write_clipboard(page, prompt)

    # Verify text landed
    time.sleep(1)
    if not get_editor_text(page, "chatgpt_textbox"):
        log("Clipboard paste failed, trying insertText...", level=logging.WARNING)
        page.evaluate("document.execCommand('insertText', false, arguments[0])", prompt)

    send_btn = resolve_selector(page, "chatgpt_send_button")
    if send_btn:
        send_btn.click()
    else:
        page.keyboard.press("Enter")

    log("Waiting for response...")

    # Wait for generating to start
    time.sleep(2)

    # Completion detection: wait until stop button disappears AND DOM is stable
    while resolve_selector(page, "chatgpt_stop_streaming") is not None:
        time.sleep(1)

    # Wait for DOM to be stable for DOM_STABLE_SECS
    log("Generation complete, waiting for DOM stability...")
    stable_time = 0
    last_html = ""
    while stable_time < DOM_STABLE_SECS:
        current_html = page.content()
        if current_html == last_html:
            stable_time += 1
        else:
            stable_time = 0
            last_html = current_html
        time.sleep(1)

    # Extract response
    responses = page.locator("[data-message-author-role='assistant']")
    if responses.count() == 0:
        take_debug_screenshot(page, "chatgpt_no_response")
        raise Exception("No response from ChatGPT.")

    last_response = responses.last.inner_text()

    # JSON Extraction
    scenes_data = extract_json_from_text(last_response)

    if not scenes_data:
        log("JSON extraction failed. Sending correction...", level=logging.WARNING)
        textbox.focus()
        write_clipboard(page, "Return only the raw JSON array, no markdown, no commentary.")
        page.keyboard.press("Enter")

        time.sleep(2)
        while resolve_selector(page, "chatgpt_stop_streaming") is not None:
            time.sleep(1)

        stable_time = 0
        last_html = ""
        while stable_time < DOM_STABLE_SECS:
            current_html = page.content()
            if current_html == last_html:
                stable_time += 1
            else:
                stable_time = 0
                last_html = current_html
            time.sleep(1)

        last_response = page.locator("[data-message-author-role='assistant']").last.inner_text()
        scenes_data = extract_json_from_text(last_response)

        if not scenes_data:
            with open(os.path.join(prompts_dir, "failed_raw_response.txt"), "w") as f:
                f.write(last_response)
            notify("Fatal Error: Failed to extract JSON from ChatGPT twice.")
            sys.exit(1)

    with open(scenes_file, "w") as f:
        json.dump(scenes_data, f, indent=2)

    log(f"Successfully extracted {len(scenes_data)} scenes to {scenes_file}")

    checkpoint["stage"] = 3
    save_checkpoint(project_dir, checkpoint)
    return scenes_data

def download_gemini_image(page, scene_dir, scene_num, initial_image_count):
    """Attempt to download the newest Gemini image."""
    img_path = os.path.join(scene_dir, f"scene_{scene_num:02d}.jpg")
    try:
        # Get all images that might be the generated output
        images = page.locator("img[src^='blob:'], img[data-test-id='image-attachment'], .model-response img")
        count = images.count()
        if count <= initial_image_count:
            return False

        # Prefer the last one (newest)
        img_loc = images.nth(count - 1)

        # Try to extract the blob URL
        src = img_loc.get_attribute("src")
        if src and src.startswith("blob:"):
            download_script = """
            async (blobUrl) => {
                const response = await fetch(blobUrl);
                const blob = await response.blob();
                const reader = new FileReader();
                return new Promise((resolve, reject) => {
                    reader.onloadend = () => resolve(reader.result);
                    reader.onerror = reject;
                    reader.readAsDataURL(blob);
                });
            }
            """
            data_url = page.evaluate(download_script, src)
            if data_url:
                import base64
                header, encoded = data_url.split(",", 1)
                data = base64.b64decode(encoded)
                with open(img_path, "wb") as f:
                    f.write(data)

        # Fallback to taking an element screenshot as a last resort
        if not os.path.exists(img_path) or os.path.getsize(img_path) < MIN_IMAGE_BYTES:
            img_loc.screenshot(path=img_path)

        if os.path.exists(img_path) and os.path.getsize(img_path) > MIN_IMAGE_BYTES:
            return True

        return False
    except Exception as e:
        log(f"Error downloading image: {e}", level=logging.ERROR)
        return False

def run_stage_3(page, project_dir, checkpoint, scenes_data, args):
    """STAGE 3: Gemini Image Generation"""
    log("--- STAGE 3: Gemini Image Generation ---")
    images_dir = os.path.join(project_dir, "Images")
    os.makedirs(images_dir, exist_ok=True)

    # Generate ruler if missing
    ruler_path = os.path.abspath("ruler_16x9.png")
    if not os.path.exists(ruler_path):
        from PIL import Image
        img = Image.new('RGBA', (1920, 1080), (0, 0, 0, 0))
        img.save(ruler_path)

    log("Navigating to gemini.google.com...")
    page.goto("https://gemini.google.com/app")
    page.wait_for_load_state("networkidle")

    # Check if logged in
    if page.locator("text=Sign in").count() > 0 or page.locator("text=Use Gemini").count() > 0:
        notify("Session expired on Gemini — re-login in Chrome, then --resume")
        sys.exit(10)

    for scene in scenes_data:
        scene_num = scene.get("scene_number")
        scene_dir = os.path.join(images_dir, f"Scene_{scene_num:02d}")
        os.makedirs(scene_dir, exist_ok=True)
        img_path = os.path.join(scene_dir, f"scene_{scene_num:02d}.jpg")

        if args.resume and scene_num in checkpoint.get("successful_scenes", []) and os.path.exists(img_path) and os.path.getsize(img_path) > MIN_IMAGE_BYTES:
            log(f"Skipping Scene {scene_num} (already generated).")
            continue

        log(f"Generating image for Scene {scene_num}...")

        prompt = scene.get("image_prompt", "") + " match the established character and style exactly. Output a single 16:9 image."

        retries = 0
        success = False
        while retries <= MAX_RETRIES_PER_SCENE and not success:
            try:
                # Upload ruler
                file_input = page.locator("input[type='file']")
                if file_input.count() > 0:
                    file_input.first.set_input_files(ruler_path)
                else:
                    gemini_upload = resolve_selector(page, "gemini_upload_button")
                    if gemini_upload:
                        with page.expect_file_chooser() as fc_info:
                            gemini_upload.click()
                        file_chooser = fc_info.value
                        file_chooser.set_files(ruler_path)
                    else:
                        log("Could not find file input or upload button. Skipping ruler upload.", level=logging.WARNING)

                time.sleep(1)

                # Input prompt
                textbox = resolve_selector(page, "gemini_textbox")
                if not textbox:
                    raise Exception("Could not find Gemini textbox.")

                textbox.focus()
                write_clipboard(page, prompt)

                # Verify text landed
                time.sleep(1)
                if not get_editor_text(page, "gemini_textbox"):
                    log("Clipboard paste failed, trying insertText...", level=logging.WARNING)
                    page.evaluate("document.execCommand('insertText', false, arguments[0])", prompt)

                # Send
                send_btn = resolve_selector(page, "gemini_send_button")
                if send_btn:
                    send_btn.click()
                else:
                    page.keyboard.press("Enter")

                log("Waiting for image generation...")

                # Polling for new image
                initial_image_count = page.locator("img[src^='blob:'], img[data-test-id='image-attachment'], .model-response img").count()
                start_time = time.time()
                while time.time() - start_time < (GEMINI_TIMEOUT / 1000.0):
                    if download_gemini_image(page, scene_dir, scene_num, initial_image_count):
                        success = True
                        break
                    time.sleep(2)

                if not success:
                    raise Exception("Timeout or failed to download image.")

            except Exception as e:
                log(f"Error on Scene {scene_num}: {e}", level=logging.ERROR)
                retries += 1
                if retries <= MAX_RETRIES_PER_SCENE:
                    log("Retrying...")
                    time.sleep(5)
                else:
                    log(f"Scene {scene_num} failed after {MAX_RETRIES_PER_SCENE} retries.", level=logging.ERROR)
                    with open(os.path.join(project_dir, "failed_scenes.txt"), "a") as f:
                        ts = datetime.now().strftime("%H:%M:%S")
                        f.write(f"Scene {scene_num} | {ts} | gemini: {e}\n")

        if success:
            log(f"Successfully saved image for Scene {scene_num}.")
            if "successful_scenes" not in checkpoint:
                checkpoint["successful_scenes"] = []
            if scene_num not in checkpoint["successful_scenes"]:
                checkpoint["successful_scenes"].append(scene_num)

        checkpoint["last_scene"] = scene_num
        save_checkpoint(project_dir, checkpoint)

        # Random delay
        delay = random.randint(8, 15)
        log(f"Waiting {delay}s before next scene...")
        time.sleep(delay)

    if args.pause_for_review:
        notify("Gemini stage complete. Paused for review. Resume with --resume.")
        sys.exit(10)

    checkpoint["stage"] = 4
    save_checkpoint(project_dir, checkpoint)

def run_stage_4(page, project_dir, checkpoint, scenes_data, args):
    """STAGE 4: ChatGPT Animation Prompts"""
    log("--- STAGE 4: ChatGPT Animation Prompts ---")
    prompts_dir = os.path.join(project_dir, "Prompts")
    anim_file = os.path.join(prompts_dir, "anim_prompts.json")

    if args.resume and os.path.exists(anim_file):
        log("Animation Prompts JSON already exists, skipping Stage 4.")
        checkpoint["stage"] = 5
        save_checkpoint(project_dir, checkpoint)
        try:
            with open(anim_file, "r") as f:
                return json.load(f)
        except Exception:
            pass

    # Filter only scenes that successfully generated images
    successful = checkpoint.get("successful_scenes", [])
    valid_scenes = [s for s in scenes_data if s.get("scene_number") in successful]

    if not valid_scenes:
        log("No successful scenes to animate. Skipping.", level=logging.WARNING)
        checkpoint["stage"] = 5
        save_checkpoint(project_dir, checkpoint)
        return []

    log("Navigating to chatgpt.com...")
    page.goto("https://chatgpt.com/?model=auto")
    page.wait_for_load_state("networkidle")

    # Check if logged in
    if page.locator("text=Log in").count() > 0 or page.locator("text=Sign in").count() > 0:
        notify("Session expired on ChatGPT — re-login in Chrome, then --resume")
        sys.exit(10)

    scenes_text = json.dumps(valid_scenes, indent=2)

    prompt = (
        "You are a cinematic animation director. For each scene below create a Grok Imagine image-to-video animation prompt. "
        "Return ONLY a valid JSON array. Each object: "
        "scene_number (int), camera, subject_motion, environment_motion, mood, lighting, style ('cinematic realism, film quality, smooth motion, natural physics'), "
        "full_prompt (60–80-word combined string). Keep motion subtle and physically plausible; assume a ~6–10s clip.\n\n"
        "SCENES:\n" + scenes_text
    )

    textbox = resolve_selector(page, "chatgpt_textbox")
    if not textbox:
        take_debug_screenshot(page, "chatgpt_textbox_miss_stg4")
        raise Exception("Could not find ChatGPT textbox.")

    textbox.focus()
    write_clipboard(page, prompt)

    # Verify text landed
    time.sleep(1)
    if not get_editor_text(page, "chatgpt_textbox"):
        page.evaluate("document.execCommand('insertText', false, arguments[0])", prompt)

    send_btn = resolve_selector(page, "chatgpt_send_button")
    if send_btn:
        send_btn.click()
    else:
        page.keyboard.press("Enter")

    log("Waiting for response...")
    time.sleep(2)

    # Wait until stop button disappears AND DOM is stable
    while resolve_selector(page, "chatgpt_stop_streaming") is not None:
        time.sleep(1)

    log("Generation complete, waiting for DOM stability...")
    stable_time = 0
    last_html = ""
    while stable_time < DOM_STABLE_SECS:
        current_html = page.content()
        if current_html == last_html:
            stable_time += 1
        else:
            stable_time = 0
            last_html = current_html
        time.sleep(1)

    # Extract response
    responses = page.locator("[data-message-author-role='assistant']")
    if responses.count() == 0:
        take_debug_screenshot(page, "chatgpt_no_response_stg4")
        raise Exception("No response from ChatGPT.")

    last_response = responses.last.inner_text()

    # JSON Extraction
    anim_data = extract_json_from_text(last_response)

    if not anim_data:
        log("JSON extraction failed. Sending correction...", level=logging.WARNING)
        textbox.focus()
        write_clipboard(page, "Return only the raw JSON array, no markdown, no commentary.")
        page.keyboard.press("Enter")

        time.sleep(2)
        while resolve_selector(page, "chatgpt_stop_streaming") is not None:
            time.sleep(1)

        stable_time = 0
        last_html = ""
        while stable_time < DOM_STABLE_SECS:
            current_html = page.content()
            if current_html == last_html:
                stable_time += 1
            else:
                stable_time = 0
                last_html = current_html
            time.sleep(1)

        last_response = page.locator("[data-message-author-role='assistant']").last.inner_text()
        anim_data = extract_json_from_text(last_response)

        if not anim_data:
            with open(os.path.join(prompts_dir, "failed_anim_response.txt"), "w") as f:
                f.write(last_response)
            notify("Fatal Error: Failed to extract JSON from ChatGPT twice (Stage 4).")
            sys.exit(1)

    with open(anim_file, "w") as f:
        json.dump(anim_data, f, indent=2)

    log(f"Successfully extracted {len(anim_data)} animation prompts to {anim_file}")

    checkpoint["stage"] = 5
    save_checkpoint(project_dir, checkpoint)
    return anim_data

def check_grok_quota(page):
    """Scan the page for limit/throttle/moderation indicators."""
    indicators = SELECTORS.get("grok_limit_indicators", [])
    for strat in indicators:
        text = strat.get("text", "")
        if text and page.locator(f"text='{text}'").count() > 0:
            return True
        # Also check insensitive match via JS
        has_text = page.evaluate(f"() => document.body.innerText.toLowerCase().includes('{text.lower()}')")
        if has_text:
            return True
    return False

def download_grok_video(page, scene_dir, scene_num, initial_video_count):
    """Wait for and download the Grok video output."""
    video_path = os.path.join(scene_dir, f"scene_{scene_num:02d}.mp4")
    try:
        video_locators = page.locator("video")
        if video_locators.count() > initial_video_count:
            video_el = video_locators.last
            # Patchright doesn't have a direct download video element, we can grab src
            src = video_el.get_attribute("src")
            if src and src.startswith("blob:"):
                # We can inject a script to download the blob
                download_script = """
                async (blobUrl) => {
                    const response = await fetch(blobUrl);
                    const blob = await response.blob();
                    const reader = new FileReader();
                    return new Promise((resolve, reject) => {
                        reader.onloadend = () => resolve(reader.result);
                        reader.onerror = reject;
                        reader.readAsDataURL(blob);
                    });
                }
                """
                data_url = page.evaluate(download_script, src)
                if data_url:
                    import base64
                    header, encoded = data_url.split(",", 1)
                    data = base64.b64decode(encoded)
                    with open(video_path, "wb") as f:
                        f.write(data)

            # If not blob or blob failed, look for a download button nearby
            if not os.path.exists(video_path) or os.path.getsize(video_path) < MIN_VIDEO_BYTES:
                download_btn = page.locator("button[aria-label*='ownload']").last
                if download_btn.count() > 0:
                    with page.expect_download() as download_info:
                        download_btn.click()
                    download = download_info.value
                    download.save_as(video_path)

        if os.path.exists(video_path) and os.path.getsize(video_path) > MIN_VIDEO_BYTES:
            return True
        return False
    except Exception as e:
        log(f"Error downloading Grok video: {e}", level=logging.ERROR)
        return False

def run_stage_5(page, project_dir, checkpoint, anim_data, args):
    """STAGE 5: Grok Imagine"""
    log("--- STAGE 5: Grok Imagine ---")
    videos_dir = os.path.join(project_dir, "Videos")
    os.makedirs(videos_dir, exist_ok=True)
    images_dir = os.path.join(project_dir, "Images")

    # Reset daily budget if new day
    today_str = datetime.now().strftime("%Y-%m-%d")
    if checkpoint.get("date") != today_str:
        checkpoint["date"] = today_str
        checkpoint["grok_videos_used_today"] = 0
        save_checkpoint(project_dir, checkpoint)

    log("Navigating to grok.com...")
    page.goto("https://grok.com/")
    page.wait_for_load_state("networkidle")

    # Check if logged in
    if page.locator("text=Sign in").count() > 0 or page.locator("text=Log in").count() > 0:
        notify("Session expired on Grok — re-login in Chrome, then --resume")
        sys.exit(10)

    for anim in anim_data:
        scene_num = anim.get("scene_number")
        video_path = os.path.join(videos_dir, f"scene_{scene_num:02d}.mp4")
        img_path = os.path.join(images_dir, f"Scene_{scene_num:02d}", f"scene_{scene_num:02d}.jpg")

        if args.resume and os.path.exists(video_path) and os.path.getsize(video_path) > MIN_VIDEO_BYTES:
            log(f"Skipping Scene {scene_num} (video already generated).")
            continue

        if not os.path.exists(img_path):
            log(f"Warning: Missing image for Scene {scene_num}. Cannot animate.", level=logging.WARNING)
            continue

        if checkpoint.get("grok_videos_used_today", 0) >= GROK_DAILY_VIDEO_BUDGET:
            notify(f"Grok quota budget ({GROK_DAILY_VIDEO_BUDGET}) reached for today. Pause and resume tomorrow.")
            sys.exit(10)

        log(f"Generating video for Scene {scene_num}...")
        prompt = anim.get("full_prompt", "")

        # Ensure we are on Imagine tab
        imagine_tab = resolve_selector(page, "grok_imagine_tab")
        if imagine_tab:
            imagine_tab.click()
            time.sleep(2)

        retries = 0
        success = False

        while retries <= MAX_RETRIES_PER_SCENE and not success:
            try:
                # Pre-check moderation/quota
                if check_grok_quota(page):
                    notify("Grok quota or moderation block detected PRE-submit! Pause and resume later.")
                    sys.exit(10)

                # Upload image
                file_input = page.locator("input[type='file']")
                if file_input.count() > 0:
                    file_input.first.set_input_files(img_path)
                else:
                    grok_upload = resolve_selector(page, "grok_upload_button")
                    if grok_upload:
                        with page.expect_file_chooser() as fc_info:
                            grok_upload.click()
                        file_chooser = fc_info.value
                        file_chooser.set_files(img_path)
                    else:
                        raise Exception("Could not find Grok upload button.")

                time.sleep(2)

                # Enter prompt
                textbox = resolve_selector(page, "grok_textbox")
                if not textbox:
                    raise Exception("Could not find Grok textbox.")

                textbox.focus()
                write_clipboard(page, prompt)

                # Verify
                time.sleep(1)
                if not get_editor_text(page, "grok_textbox"):
                    page.evaluate("document.execCommand('insertText', false, arguments[0])", prompt)

                # Submit
                send_btn = resolve_selector(page, "grok_send_button")
                if send_btn:
                    send_btn.click()
                else:
                    page.keyboard.press("Enter")

                checkpoint["grok_videos_used_today"] += 1
                save_checkpoint(project_dir, checkpoint)

                log("Waiting for video generation...")

                # Poll
                initial_video_count = page.locator("video").count()
                start_time = time.time()
                while time.time() - start_time < (GROK_TIMEOUT / 1000.0):
                    # Post-submit quota/moderation check
                    if check_grok_quota(page):
                        notify("Grok quota or moderation block detected POST-submit! Pause and resume later.")
                        sys.exit(10)

                    if download_grok_video(page, videos_dir, scene_num, initial_video_count):
                        success = True
                        break

                    time.sleep(5)

                if not success:
                    raise Exception("Timeout or failed to download Grok video.")

            except Exception as e:
                log(f"Error on Scene {scene_num} (Grok): {e}", level=logging.ERROR)
                retries += 1
                if retries <= MAX_RETRIES_PER_SCENE:
                    log("Retrying (technical failure)...")
                    time.sleep(30)
                else:
                    log(f"Grok Scene {scene_num} failed permanently.", level=logging.ERROR)
                    with open(os.path.join(project_dir, "failed_scenes.txt"), "a") as f:
                        ts = datetime.now().strftime("%H:%M:%S")
                        f.write(f"Scene {scene_num} | {ts} | grok: {e}\n")

        if success:
            log(f"Successfully saved video for Scene {scene_num}.")

        checkpoint["last_scene"] = scene_num
        save_checkpoint(project_dir, checkpoint)

        # Mandatory random delay between generations to save quota
        delay = random.randint(GROK_DELAY_MIN, GROK_DELAY_MAX)
        log(f"Mandatory Grok delay: Waiting {delay}s before next scene...")
        time.sleep(delay)

    checkpoint["stage"] = 6
    save_checkpoint(project_dir, checkpoint)

def run_stage_6(project_dir, checkpoint, args):
    """STAGE 6: FFmpeg Normalization and Stitching"""
    log("--- STAGE 6: FFmpeg Normalization and Stitching ---")
    videos_dir = os.path.join(project_dir, "Videos")
    final_dir = os.path.join(project_dir, "Final")
    os.makedirs(final_dir, exist_ok=True)

    final_out = os.path.join(final_dir, "final_stitched.mp4")
    if args.resume and os.path.exists(final_out) and os.path.getsize(final_out) > MIN_VIDEO_BYTES:
        log("Final video already exists. Done.")
        return

    # Get all successfully generated videos in order
    video_files = sorted([f for f in os.listdir(videos_dir) if f.startswith("scene_") and f.endswith(".mp4")])

    if not video_files:
        log("No videos found to stitch.")
        return

    normalized_videos = []

    # Normalize each video to exactly 1920x1080 30fps yuv420p with silent audio
    for vf in video_files:
        in_path = os.path.join(videos_dir, vf)
        norm_path = os.path.join(videos_dir, f"norm_{vf}")

        if args.resume and os.path.exists(norm_path) and os.path.getsize(norm_path) > MIN_VIDEO_BYTES:
            normalized_videos.append(norm_path)
            continue

        log(f"Normalizing {vf}...")
        # Scale to fit inside 1920x1080, maintaining aspect ratio, pad the rest. Add silent audio so concat works smoothly.
        vf_opts = [
            "ffmpeg", "-y", "-i", in_path,
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-vf", f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease:flags=lanczos,pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2,fps={FPS},format=yuv420p,setsar=1",
            "-c:v", "libx264", "-crf", str(CRF), "-preset", "medium",
            "-c:a", "aac", "-shortest",
            norm_path
        ]

        try:
            subprocess.run(vf_opts, check=True, capture_output=True)
            normalized_videos.append(norm_path)
        except subprocess.CalledProcessError as e:
            log(f"Failed to normalize {vf}: {e.stderr.decode()}", level=logging.ERROR)

    if not normalized_videos:
        log("No normalized videos to stitch.")
        return

    # Create concat demuxer list
    concat_list_path = os.path.join(videos_dir, "concat_list.txt")
    with open(concat_list_path, "w") as f:
        for nv in normalized_videos:
            f.write(f"file '{os.path.basename(nv)}'\n")

    log(f"Stitching {len(normalized_videos)} videos...")
    concat_opts = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_list_path,
        "-c", "copy",
        final_out
    ]

    try:
        subprocess.run(concat_opts, check=True, capture_output=True)
        log(f"Successfully generated final stitched video at {final_out}")
        notify("Video Pipeline Complete!", title="Success")
    except subprocess.CalledProcessError as e:
        log(f"Failed to stitch videos: {e.stderr.decode()}", level=logging.ERROR)
        notify("Failed to stitch videos.", title="Error")

def run_pipeline(args):
    setup_logging()

    if not args.project:
        log("Error: --project is required.", level=logging.ERROR)
        sys.exit(1)

    project_dir = os.path.abspath(args.project)
    os.makedirs(project_dir, exist_ok=True)

    checkpoint = load_checkpoint(project_dir)
    stage = checkpoint.get("stage", 1)

    # Initialize Patchright
    with sync_playwright() as p:
        # Clone profile if needed
        clone_chrome_profile(args.profile, AUTOMATION_PROFILE)
        user_data_dir = os.path.join(AUTOMATION_PROFILE)

        # We need to pass the profile directory name via args because persistent_context uses the root
        # Launch persistent context
        log("Launching browser...")
        try:
            context = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                channel="chrome",
                headless=False,
                no_viewport=True,
                args=[
                    f"--profile-directory={args.profile}",
                    "--no-first-run",
                    "--no-default-browser-check"
                ]
            )
            page = context.new_page()

            # STAGE 1
            script_text = ""
            if stage <= 1:
                if not args.excel:
                    log("Error: --excel is required for initial run.", level=logging.ERROR)
                    sys.exit(1)
                script_text = run_stage_1(args, project_dir, checkpoint)
                stage = checkpoint.get("stage")
            elif stage > 1:
                raw_script_path = os.path.join(project_dir, "Prompts", "raw_script.txt")
                if os.path.exists(raw_script_path):
                    with open(raw_script_path, "r") as f:
                        script_text = f.read()

            # STAGE 2
            scenes_data = []
            if stage <= 2:
                scenes_data = run_stage_2(page, project_dir, checkpoint, script_text, args)
                stage = checkpoint.get("stage")
            elif stage > 2:
                scenes_file = os.path.join(project_dir, "Prompts", "scenes.json")
                if os.path.exists(scenes_file):
                    with open(scenes_file, "r") as f:
                        scenes_data = json.load(f)

            # STAGE 3
            if stage <= 3:
                run_stage_3(page, project_dir, checkpoint, scenes_data, args)
                stage = checkpoint.get("stage")

            # STAGE 4
            anim_data = []
            if stage <= 4:
                anim_data = run_stage_4(page, project_dir, checkpoint, scenes_data, args)
                stage = checkpoint.get("stage")
            elif stage > 4:
                anim_file = os.path.join(project_dir, "Prompts", "anim_prompts.json")
                if os.path.exists(anim_file):
                    with open(anim_file, "r") as f:
                        anim_data = json.load(f)

            # STAGE 5
            if stage <= 5:
                run_stage_5(page, project_dir, checkpoint, anim_data, args)
                stage = checkpoint.get("stage")

            # STAGE 6
            if stage <= 6:
                run_stage_6(project_dir, checkpoint, args)

            log("Pipeline execution finished.")

        except Exception as e:
            import traceback
            log("Pipeline failed with exception:", level=logging.ERROR)
            log(traceback.format_exc(), level=logging.ERROR)
            notify(f"Pipeline error: {e}")
            sys.exit(1)
        finally:
            if 'context' in locals():
                context.close()


# =============================================================================
# MAIN CLI ENTRYPOINT
# =============================================================================
if __name__ == "__main__":
    setup_caffeinate()

    parser = argparse.ArgumentParser(description="Unattended Script-to-Video Pipeline")
    parser.add_argument("--excel", help="Path to Excel script file")
    parser.add_argument("--project", help="Project name (creates folder)")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--calibrate", action="store_true", help="Open sites to calibrate selectors")
    parser.add_argument("--profile", default="Default", help="Chrome profile to clone (e.g., 'Default', 'Profile 1')")
    parser.add_argument("--style-ref", action="store_true", help="Generate a style reference image in Gemini first")
    parser.add_argument("--pause-for-review", action="store_true", help="Pause after Gemini stage for manual review")

    args = parser.parse_args()

    if args.calibrate:
        setup_logging()
        log("Calibration mode not fully implemented yet.")
        sys.exit(0)

    run_pipeline(args)
