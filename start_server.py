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
    print("🚀 SalesAI Server - Local Lead Intelligence Gateway")
    print("=" * 70)

    bind_host = os.getenv("SALESAI_HOST", "127.0.0.1")
    bind_port = int(os.getenv("SALESAI_PORT", "8000"))

    print("\n📍 Connectivity Details:")
    print(f"  • Sales Operator Console:      http://{bind_host}:{bind_port}/sales")
    print(f"  • Swagger API Documentation:   http://{bind_host}:{bind_port}/docs")
    print(f"  • Health Check:                http://{bind_host}:{bind_port}/health")

    enable_tunnel = os.environ.get("ENABLE_PUBLIC_TUNNEL", "").lower() in ("1", "true", "yes") or "--tunnel" in sys.argv
    cloudflared_path = find_cloudflared()
    tunnel_proc = None
    if cloudflared_path and enable_tunnel:
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

    bind_host = os.getenv("SALESAI_HOST", "127.0.0.1")
    bind_port = int(os.getenv("SALESAI_PORT", "8000"))

    print("\n" + "-" * 70, flush=True)
    print(f"⚡ Starting Uvicorn Gateway on {bind_host}:{bind_port} ... (Press Ctrl+C to stop)", flush=True)
    print("-" * 70 + "\n", flush=True)

    try:
        # Enforce SO_EXCLUSIVEADDRUSE on Windows to prevent silent socket hijacking
        # and ensure conflicting processes fail immediately instead of running concurrently
        if sys.platform == "win32":
            orig_bind_socket = uvicorn.config.Config.bind_socket

            def safe_bind_socket(config_self):
                if config_self.uds is None and config_self.fd is None:
                    family = socket.AF_INET if ":" not in (config_self.host or "") else socket.AF_INET6
                    sock = socket.socket(family=family)
                    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    else:
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    try:
                        sock.bind((config_self.host, config_self.port))
                    except OSError as exc:
                        import logging
                        logger = logging.getLogger("uvicorn.error")
                        logger.error(f"[ERROR] Port {config_self.port} is already in use by another process. Startup aborted.")
                        sys.exit(1)
                    sock.set_inheritable(True)
                    return sock
                return orig_bind_socket(config_self)

            uvicorn.config.Config.bind_socket = safe_bind_socket

        uvicorn.run(
            "backend.main:app",
            host=bind_host,
            port=bind_port,
            reload=True,
            log_level="info",
        )
    finally:
        if tunnel_proc:
            tunnel_proc.terminate()


if __name__ == "__main__":
    main()
