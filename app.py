import os
import re
import time
import queue
import logging
import threading
import subprocess
import gradio as gr
from platform import system

# Configuration
STEAMCMD_DIR = os.path.join(os.getcwd(), "steamcmd")
STEAMCMD_EXE = os.path.join(STEAMCMD_DIR, "steamcmd.exe" if system() == "Windows" else "steamcmd.sh")
LOG_FILE = "app.log"
PUBLIC_URL_BASE = "http://localhost/downloads"  # Replace with your actual public URL

# Set up logging
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

def check_steamcmd():
    """Check if SteamCMD is installed and accessible"""
    if os.path.exists(STEAMCMD_EXE):
        logging.info("SteamCMD found at: %s", STEAMCMD_EXE)
        return True
    logging.warning("SteamCMD not found at: %s", STEAMCMD_EXE)
    return False

def install_steamcmd():
    """Install SteamCMD in the designated directory"""
    try:
        os.makedirs(STEAMCMD_DIR, exist_ok=True)
        logging.info("Installing SteamCMD...")

        if system() == "Windows":
            url = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"
            zip_path = os.path.join(STEAMCMD_DIR, "steamcmd.zip")
            subprocess.run(f"curl -o {zip_path} {url}", shell=True, check=True)
            subprocess.run(f"tar -xf {zip_path} -C {STEAMCMD_DIR}", shell=True, check=True)
            os.remove(zip_path)
        else:
            subprocess.run(
                f"wget -q -O {STEAMCMD_EXE} https://steamcdn-a.akamaihd.net/client/installer/steamcmd.sh",
                shell=True, check=True
            )
            subprocess.run(f"chmod +x {STEAMCMD_EXE}", shell=True, check=True)

        logging.info("SteamCMD installed successfully")
        return True
    except Exception as e:
        logging.error("Installation failed: %s", str(e))
        return False

def extract_game_id(input_str):
    """Extract Steam Game ID from input (supports both URL and direct ID)"""
    match = re.search(r'(?:app/|^)(\d+)', input_str)
    if match and match.group(1).isdigit():
        return match.group(1)
    raise ValueError("Invalid Game ID or URL")

def validate_credentials(username, password):
    """Validate Steam credentials using SteamCMD"""
    try:
        cmd = [STEAMCMD_EXE, "+login", username, password, "+quit"]
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30
        )
        if "FAILED" in result.stderr:
            logging.warning("Login failed for user: %s", username)
            return False
        return True
    except Exception as e:
        logging.error("Validation error: %s", str(e))
        return False

def download_worker(game_id, username, password, anonymous, progress_queue):
    """Background worker for handling SteamCMD download process"""
    try:
        login_cmd = ["+login", "anonymous"] if anonymous else ["+login", username, password]
        cmd = [STEAMCMD_EXE] + login_cmd + ["+app_update", game_id, "validate", "+quit"]
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )

        start_time = time.time()
        total_size = 0
        downloaded = 0

        for line in iter(process.stdout.readline, ''):
            progress_queue.put(line)
            
            # Parse download progress
            if "progress:" in line:
                match = re.search(r'progress: \d+\.\d+ \((\d+)/(\d+)\)', line)
                if match:
                    downloaded = int(match.group(1))
                    total_size = int(match.group(2))
                    
                    elapsed = time.time() - start_time
                    speed = downloaded / elapsed if elapsed > 0 else 0
                    remaining = (total_size - downloaded) / speed if speed > 0 else 0
                    
                    progress = {
                        "percentage": (downloaded / total_size) * 100,
                        "downloaded": downloaded,
                        "total": total_size,
                        "elapsed": elapsed,
                        "remaining": remaining
                    }
                    progress_queue.put(progress)

        process.wait()
        if process.returncode != 0:
            progress_queue.put("error: Download failed")
        else:
            progress_queue.put("complete")

    except Exception as e:
        progress_queue.put(f"error: {str(e)}")

def generate_public_link(game_id):
    """Generate public link for downloaded content (simplified)"""
    return f"{PUBLIC_URL_BASE}/{game_id}"

def create_interface():
    """Create and configure Gradio interface"""
    with gr.Blocks(title="Steam Game Downloader") as interface:
        # System Status Section
        gr.Markdown("## System Status")
        status_text = gr.Textbox(label="SteamCMD Status", interactive=False)
        install_btn = gr.Button("Install SteamCMD", visible=False)

        # Login Section
        gr.Markdown("## Login")
        with gr.Row():
            username = gr.Textbox(label="Username")
            password = gr.Textbox(label="Password", type="password")
        anonymous = gr.Checkbox(label="Login Anonymously (for free games)")

        # Download Section
        gr.Markdown("## Game Download")
        game_input = gr.Textbox(label="Game ID or Steam URL")
        download_btn = gr.Button("Start Download")

        # Progress Section
        gr.Markdown("## Download Progress")
        progress_bar = gr.Progress()
        progress_info = gr.Textbox(label="Status", interactive=False)
        public_link = gr.Textbox(label="Download Link", visible=False)
        error_output = gr.Textbox(label="Error Messages", visible=False)

        # System Check on Load
        def system_check():
            if check_steamcmd():
                return gr.update(value="SteamCMD Ready", visible=True), gr.update(visible=False)
            return gr.update(value="SteamCMD Missing", visible=True), gr.update(visible=True)
        
        interface.load(system_check, outputs=[status_text, install_btn])

        # Installation Handler
        def handle_install():
            if install_steamcmd():
                return gr.update(value="SteamCMD Installed", visible=True), gr.update(visible=False)
            return gr.update(value="Installation Failed", visible=True), gr.update(visible=True)
        
        install_btn.click(handle_install, outputs=[status_text, install_btn])

        # Download Handler
        def handle_download(username, password, anonymous, game_input, progress=gr.Progress()):
            try:
                game_id = extract_game_id(game_input)
                progress(0, desc="Initializing...")

                if not anonymous and not validate_credentials(username, password):
                    raise gr.Error("Invalid credentials")

                progress_queue = queue.Queue()
                thread = threading.Thread(
                    target=download_worker,
                    args=(game_id, username, password, anonymous, progress_queue),
                    daemon=True
                )
                thread.start()

                start_time = time.time()
                while thread.is_alive():
                    try:
                        item = progress_queue.get(timeout=1)
                        
                        if isinstance(item, dict):
                            progress(
                                item["percentage"] / 100,
                                desc=f"Downloading: {item['downloaded']/1e6:.1f}MB/{item['total']/1e6:.1f}MB"
                            )
                            elapsed = time.strftime("%H:%M:%S", time.gmtime(item["elapsed"]))
                            remaining = time.strftime("%H:%M:%S", time.gmtime(item["remaining"]))
                            progress_info = f"Elapsed: {elapsed} | Remaining: {remaining}"
                            yield {
                                progress_info: progress_info,
                                error_output: "",
                                public_link: ""
                            }
                        elif item.startswith("error:"):
                            raise gr.Error(item[6:])
                        elif item == "complete":
                            yield {
                                public_link: generate_public_link(game_id),
                                progress_info: "Download Complete!",
                                error_output: ""
                            }
                    except queue.Empty:
                        continue

            except Exception as e:
                yield {
                    error_output: str(e),
                    public_link: "",
                    progress_info: ""
                }

        download_btn.click(
            handle_download,
            inputs=[username, password, anonymous, game_input],
            outputs=[progress_info, error_output, public_link],
            show_progress="full"
        )

    return interface

if __name__ == "__main__":
    app = create_interface()
    app.launch(server_port=7860, show_error=True)