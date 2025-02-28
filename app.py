import os
import re
import time
import queue
import logging
import threading
import subprocess
import gradio as gr
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from platform import system
import uvicorn  # Import uvicorn for running the FastAPI app

# Configuration
STEAMCMD_DIR = os.path.join(os.getcwd(), "steamcmd")
STEAMCMD_EXE = os.path.join(STEAMCMD_DIR, "steamcmd.exe" if system() == "Windows" else "steamcmd.sh")
LOG_FILE = "logs/app.log"

# Ensure directories exist
os.makedirs("downloads", exist_ok=True)
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

# Set up logging
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
# Also log to console
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger('').addHandler(console)

# Global variable to store Gradio share URL
SHARE_URL = None

# Create FastAPI app for serving files
fastapi_app = FastAPI()

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
            # Direct method for Linux
            subprocess.run(
                f"cd {STEAMCMD_DIR} && wget -q https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz && tar -xzf steamcmd_linux.tar.gz && rm steamcmd_linux.tar.gz",
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
        download_dir = os.path.join(os.getcwd(), "downloads", game_id)
        os.makedirs(download_dir, exist_ok=True)
        
        cmd = [
            STEAMCMD_EXE
        ] + login_cmd + [
            "+force_install_dir", download_dir,
            "+app_update", game_id, "validate", 
            "+quit"
        ]
        
        logging.info(f"Starting download with command: {' '.join(cmd)}")
        
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
            logging.info(f"SteamCMD: {line.strip()}")
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
        logging.error(f"Download error: {str(e)}")
        progress_queue.put(f"error: {str(e)}")

def generate_download_path(game_id):
    """Generate a download path"""
    global SHARE_URL
    # If we have a Gradio share URL, we'll display that info
    if SHARE_URL:
        return f"Game files downloaded to server. Access at {SHARE_URL}/file/downloads/{game_id}/"
    return f"Game files downloaded to server directory: /downloads/{game_id}/"

def serve_download_directory():
    """Set up routes to serve the download directory"""
    @fastapi_app.get("/file/downloads/{game_id}/{file_path:path}")
    async def serve_file(game_id: str, file_path: str):
        download_path = os.path.join(os.getcwd(), "downloads", game_id, file_path)
        if os.path.exists(download_path) and os.path.isfile(download_path):
            return FileResponse(download_path)
        return {"error": "File not found"}
    
    @fastapi_app.get("/file/downloads/{game_id}")
    async def list_files(game_id: str):
        download_path = os.path.join(os.getcwd(), "downloads", game_id)
        
        if not os.path.exists(download_path):
            return HTMLResponse("Directory not found")
        
        files = []
        for root, dirs, filenames in os.walk(download_path):
            rel_path = os.path.relpath(root, download_path)
            if rel_path == ".":
                rel_path = ""
            for filename in filenames:
                file_path = os.path.join(rel_path, filename)
                files.append(file_path)
        
        html = "<html><head><title>Files</title></head><body><h1>Downloaded Files</h1><ul>"
        for file in files:
            file_url = f"/file/downloads/{game_id}/{file}"
            html += f"<li><a href='{file_url}'>{file}</a></li>"
        html += "</ul></body></html>"
        
        return HTMLResponse(html)

def create_interface():
    """Create and configure Gradio interface"""
    with gr.Blocks(title="Steam Game Downloader") as interface:
        # System Status Section
        gr.Markdown("## System Status")
        status_text = gr.Textbox(label="SteamCMD Status", interactive=False)
        install_btn = gr.Button("Install SteamCMD", visible=False)
        share_url_text = gr.Textbox(label="Shared URL", interactive=False)

        # Login Section
        gr.Markdown("## Login")
        with gr.Row():
            username = gr.Textbox(label="Username", placeholder="Steam username (or leave empty for anonymous)")
            password = gr.Textbox(label="Password", type="password", placeholder="Steam password")
        anonymous = gr.Checkbox(label="Login Anonymously (for free games)", value=True)

        # Download Section
        gr.Markdown("## Game Download")
        game_input = gr.Textbox(label="Game ID or Steam URL", placeholder="e.g., 570 or https://store.steampowered.com/app/570/")
        download_btn = gr.Button("Start Download")

        # Progress Section
        gr.Markdown("## Download Progress")
        progress_bar = gr.Progress()
        progress_info = gr.Textbox(label="Status", interactive=False)
        download_path = gr.Textbox(label="Download Location", visible=False, interactive=False)
        error_output = gr.Textbox(label="Error Messages", visible=False)

        # System Check on Load
        def system_check():
            global SHARE_URL
            share_url_value = f"Gradio Share URL: {SHARE_URL}" if SHARE_URL else "Waiting for Gradio share URL..."
            
            if check_steamcmd():
                return gr.update(value="SteamCMD Ready", visible=True), gr.update(visible=False), gr.update(value=share_url_value, visible=True)
            return gr.update(value="SteamCMD Missing", visible=True), gr.update(visible=True), gr.update(value=share_url_value, visible=True)
        
        interface.load(system_check, outputs=[status_text, install_btn, share_url_text])

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

                if not anonymous and (not username or not password):
                    raise gr.Error("Username and password required when not using anonymous login")
                
                if not anonymous and not validate_credentials(username, password):
                    raise gr.Error("Invalid Steam credentials")

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
                                error_output: gr.update(value="", visible=False),
                                download_path: gr.update(visible=False)
                            }
                        elif item.startswith("error:"):
                            raise gr.Error(item[6:])
                        elif item == "complete":
                            yield {
                                download_path: gr.update(value=generate_download_path(game_id), visible=True),
                                progress_info: "Download Complete!",
                                error_output: gr.update(value="", visible=False)
                            }
                    except queue.Empty:
                        continue

            except Exception as e:
                yield {
                    error_output: gr.update(value=str(e), visible=True),
                    download_path: gr.update(visible=False),
                    progress_info: "Error occurred."
                }

        download_btn.click(
            handle_download,
            inputs=[username, password, anonymous, game_input],
            outputs=[progress_info, error_output, download_path],
            show_progress="full"
        )

    return interface

def update_share_url(share_url):
    """Update the global share URL"""
    global SHARE_URL
    SHARE_URL = share_url
    logging.info(f"Gradio share URL: {share_url}")

if __name__ == "__main__":
    # First make sure SteamCMD is installed before starting the web interface
    if not check_steamcmd():
        install_steamcmd()
    
    app = create_interface()
    
    # Serve the FastAPI app in a separate thread
    threading.Thread(target=lambda: uvicorn.run(fastapi_app, host="0.0.0.0", port=8080), daemon=True).start()
    
    port = int(os.getenv("PORT", 7860))
    logging.info(f"Starting application on port {port}")
    
    # Launch Gradio with share=True to get a public URL
    app.launch(
        server_port=port, 
        server_name="0.0.0.0", 
        share=True,  # This enables Gradio sharing
        prevent_thread_lock=True,
        show_error=True
    )
    
    # Keep the script running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Application stopped by user")