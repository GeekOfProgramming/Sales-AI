"""Startup script for pyBIM-LLM Gateway Server & Multi-Laptop Access.
Binds to 0.0.0.0:8000 and optionally launches Cloudflare Tunnel for remote access.
"""

import sys
import io
import os

# Ensure UTF-8 stdout encoding with immediate line buffering for Windows
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "") != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

import subprocess
import socket
import threading
import time
from pathlib import Path
import uvicorn


def get_local_ips():
    """Retrieve local network IPv4 addresses."""
    ips = []
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith("127.") and not ip.startswith("169.254"):
                ips.append(ip)
    except Exception:
        pass
    return ips


def find_cloudflared():
    """Locate cloudflared.exe binary if present in workspace."""
    candidates = [
        Path(__file__).parent / "cloudflared.exe",
        Path(__file__).parent.parent / "local-ai-agent-starter" / "cloudflared.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def main():
    print("=" * 70)
    print("🚀 pyBIM-LLM Server - Multi-Laptop Workstation & AI Hub")
    print("=" * 70)

    local_ips = get_local_ips()
    primary_ip = local_ips[0] if local_ips else "10.120.24.34"

    print("\n📍 Connectivity Details:")
    print(f"  • Laptop 1 (Localhost):        http://localhost:8000")
    print(f"  • Laptop 2 & 3 (Wi-Fi / LAN):  http://{primary_ip}:8000")
    print(f"  • Revit Plugin Endpoint:       http://{primary_ip}:8000/generate-script")
    print(f"  • Web UI Studio:               http://{primary_ip}:8000/ui")
    print(f"  • Swagger API Docs:            http://{primary_ip}:8000/docs")

    cloudflared_path = find_cloudflared()
    tunnel_proc = None
    if cloudflared_path:
        print(f"\n🌐 Starting Cloudflare Quick Tunnel using: {cloudflared_path}", flush=True)
        try:
            tunnel_proc = subprocess.Popen(
                [cloudflared_path, "tunnel", "--url", "http://127.0.0.1:8000"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            def _watch_tunnel_output(proc):
                import re
                try:
                    for line in iter(proc.stdout.readline, ""):
                        if not line:
                            break
                        match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                        if match:
                            base_url = match.group(0)
                            public_ui_url = f"{base_url}/ui"
                            print("\n" + "=" * 70, flush=True)
                            print("🎉 GLOBAL PUBLIC HTTPS TUNNEL READY FOR ANY CITY:", flush=True)
                            print(f"👉 LINK TO SHARE:  {public_ui_url}", flush=True)
                            print("=" * 70, flush=True)
                            print("  (Anyone anywhere in the world can open this link in their browser!)\n", flush=True)

                            # Save link to PUBLIC_URL.txt for easy copy-pasting
                            try:
                                url_file = Path(__file__).parent / "PUBLIC_URL.txt"
                                url_file.write_text(
                                    f"=== pyBIM-LLM Public Access Link ===\n\n"
                                    f"Web Studio UI (Share with anyone in any city):\n"
                                    f"{public_ui_url}\n\n"
                                    f"API Base URL:\n"
                                    f"{base_url}\n",
                                    encoding="utf-8",
                                )
                                print(f"💾 Link also saved to: {url_file.name}", flush=True)
                            except Exception:
                                pass

                            # Copy link to Windows clipboard automatically
                            try:
                                subprocess.run(
                                    ["clip.exe"],
                                    input=public_ui_url.encode("utf-8"),
                                    check=False,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                )
                                print("📋 Link automatically copied to your clipboard! Just press Ctrl+V to send.\n", flush=True)
                            except Exception:
                                pass
                            break
                except Exception:
                    pass

            tunnel_thread = threading.Thread(target=_watch_tunnel_output, args=(tunnel_proc,), daemon=True)
            tunnel_thread.start()
        except Exception as e:
            print(f"⚠️ Cloudflare tunnel notice: {e}", flush=True)

    print("\n" + "-" * 70, flush=True)
    print("⚡ Starting Uvicorn Gateway on 0.0.0.0:8000 ... (Press Ctrl+C to stop)", flush=True)
    print("-" * 70 + "\n", flush=True)

    try:
        uvicorn.run(
            "backend.main:app",
            host="0.0.0.0",
            port=8000,
            reload=True,
            log_level="info",
        )
    finally:
        if tunnel_proc:
            tunnel_proc.terminate()


if __name__ == "__main__":
    main()
