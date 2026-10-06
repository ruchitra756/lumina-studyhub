"""Start Lumina StudyHub:  python launch.py"""
import shutil
import threading
import webbrowser

HOST, PORT = "127.0.0.1", 8000


def main():
    from server import settings
    if not (settings.ROOT / ".env").exists():
        print("\n[!] No .env file found. Copy .env.example to .env and paste your GEMINI_API_KEY.\n")
    if shutil.which("ffmpeg") is None:
        print("[!] ffmpeg not found. Audio/video upload needs it: https://ffmpeg.org/download.html\n")
    import uvicorn
    url = f"http://{HOST}:{PORT}"
    print(f"Lumina StudyHub running at {url}  (press Ctrl+C to stop)")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("server.webapp:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
