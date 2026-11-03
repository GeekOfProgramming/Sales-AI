"""
scripts/hardware_profile.py
Authoritative Hardware & AI Capacity Auditor for SalesAI.
Collects Windows hardware facts safely without collecting personal identifiers.
Generates conservative candidate model recommendations for subsequent benchmarking in Step 6.
"""

from __future__ import annotations

import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure UTF-8 output encoding for Windows consoles
if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "") != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Minimum free disk headroom required after candidate model download (GB)
MIN_FREE_DISK_AFTER_MODEL_GB = 25.0

# Typical estimated model sizes on disk (in GB) for Q4 quantizations
MODEL_DISK_ESTIMATES = {
    "1.5B": 1.5,
    "3B": 2.5,
    "7B/8B Q4": 5.0,
    "14B Q4": 9.0,
    "20B/22B Q4": 14.0,
    "32B Q4": 20.0,
    "70B Q4": 42.0,
}


def run_cmd(cmd: List[str], timeout: int = 10) -> Optional[str]:
    """Runs a system command safely and returns its stdout, or None on failure."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        return proc.stdout.strip() if proc.returncode == 0 else None
    except Exception:
        return None


def run_powershell(script: str, timeout: int = 15) -> Optional[str]:
    """Executes a PowerShell script block and returns stdout."""
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    return run_cmd(cmd, timeout=timeout)


class HardwareAuditor:
    """Safe Windows Hardware Auditor with conservative AI capacity profiling."""

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path(__file__).parent.parent.resolve()

    def detect_os(self) -> Dict[str, Any]:
        """Collects non-sensitive Windows OS version and architecture."""
        ps_script = (
            "Get-CimInstance Win32_OperatingSystem | "
            "Select-Object Caption, Version, BuildNumber, OSArchitecture | "
            "ConvertTo-Json -Compress"
        )
        raw = run_powershell(ps_script)
        caption = platform.system() + " " + platform.release()
        version = platform.version()
        build = ""
        arch = platform.machine()

        if raw:
            try:
                data = json.loads(raw)
                caption = data.get("Caption", caption).strip()
                version = data.get("Version", version).strip()
                build = str(data.get("BuildNumber", build)).strip()
                arch = data.get("OSArchitecture", arch).strip()
            except Exception:
                pass

        return {
            "caption": caption,
            "version": version,
            "build_number": build,
            "architecture": arch,
        }

    def detect_cpu(self) -> Dict[str, Any]:
        """Detects processor specifications."""
        ps_script = (
            "Get-CimInstance Win32_Processor | "
            "Select-Object Manufacturer, Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed | "
            "ConvertTo-Json -Compress"
        )
        raw = run_powershell(ps_script)
        name = platform.processor() or "Unknown CPU"
        manufacturer = "Unknown"
        cores = os.cpu_count() or 1
        threads = os.cpu_count() or 1
        max_clock_mhz = None

        if raw:
            try:
                data = json.loads(raw)
                # If multiple CPUs returned, take the first
                if isinstance(data, list):
                    data = data[0]
                name = data.get("Name", name).strip()
                manufacturer = data.get("Manufacturer", manufacturer).strip()
                cores = int(data.get("NumberOfCores") or cores)
                threads = int(data.get("NumberOfLogicalProcessors") or threads)
                max_clock_mhz = data.get("MaxClockSpeed")
            except Exception:
                pass

        return {
            "name": name,
            "manufacturer": manufacturer,
            "physical_cores": cores,
            "logical_processors": threads,
            "max_clock_mhz": max_clock_mhz,
            "architecture": platform.machine(),
        }

    def detect_memory(self) -> Dict[str, Any]:
        """Collects physical RAM statistics in bytes and GB."""
        ps_script = (
            "Get-CimInstance Win32_OperatingSystem | "
            "Select-Object TotalVisibleMemorySize, FreePhysicalMemory | "
            "ConvertTo-Json -Compress"
        )
        raw = run_powershell(ps_script)
        total_kb = 0
        free_kb = 0

        if raw:
            try:
                data = json.loads(raw)
                total_kb = int(data.get("TotalVisibleMemorySize") or 0)
                free_kb = int(data.get("FreePhysicalMemory") or 0)
            except Exception:
                pass

        total_bytes = total_kb * 1024
        free_bytes = free_kb * 1024
        total_gb = round(total_bytes / (1024**3), 2)
        free_gb = round(free_bytes / (1024**3), 2)

        return {
            "total_bytes": total_bytes,
            "total_gb": total_gb,
            "free_bytes": free_bytes,
            "free_gb": free_gb,
        }

    def detect_gpus(self) -> List[Dict[str, Any]]:
        """
        Detects all GPUs, preferring nvidia-smi for dedicated NVIDIA VRAM
        and falling back gracefully for integrated or AMD graphics.
        """
        gpus: List[Dict[str, Any]] = []

        # 1. Try nvidia-smi first for accurate dedicated VRAM
        nvidia_smi = shutil.which("nvidia-smi")
        if nvidia_smi:
            query = "name,driver_version,memory.total,memory.used,memory.free"
            cmd = [nvidia_smi, f"--query-gpu={query}", "--format=csv,noheader,nounits"]
            out = run_cmd(cmd)
            if out:
                for line in out.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 5:
                        name, drv, total_mb, used_mb, free_mb = parts[:5]
                        try:
                            tot_mb = float(total_mb)
                            tot_gb = round(tot_mb / 1024.0, 2)
                            u_gb = round(float(used_mb) / 1024.0, 2)
                            f_gb = round(float(free_mb) / 1024.0, 2)
                        except ValueError:
                            tot_gb = 0.0
                            u_gb = 0.0
                            f_gb = 0.0
                        gpus.append({
                            "name": name,
                            "vendor": "NVIDIA",
                            "is_discrete": True,
                            "driver_version": drv,
                            "vram_total_mb": float(total_mb) if total_mb.replace('.', '', 1).isdigit() else 0.0,
                            "vram_total_gb": tot_gb,
                            "vram_free_gb": f_gb,
                            "vram_used_gb": u_gb,
                            "source": "nvidia-smi",
                        })

        # 2. Query Windows VideoController for any additional GPUs (e.g. iGPU)
        ps_script = (
            "Get-CimInstance Win32_VideoController | "
            "Select-Object Name, DriverVersion, AdapterRAM | "
            "ConvertTo-Json -Compress"
        )
        raw = run_powershell(ps_script)
        if raw:
            try:
                data = json.loads(raw)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    g_name = item.get("Name", "Unknown GPU").strip()
                    drv = item.get("DriverVersion", "").strip()

                    # Check if already captured by nvidia-smi
                    already_found = any(g["name"].lower() == g_name.lower() for g in gpus)
                    if already_found:
                        continue

                    # Classify integrated vs discrete based on naming heuristics
                    lower_name = g_name.lower()
                    is_igpu = any(tag in lower_name for tag in ["intel", "iris", "uhd", "radeon(tm) graphics", "apu"])
                    is_discrete = not is_igpu or any(tag in lower_name for tag in ["geforce", "rtx", "gtx", "quadro", "radeon rx"])

                    vendor = "Unknown"
                    if "nvidia" in lower_name:
                        vendor = "NVIDIA"
                    elif "intel" in lower_name:
                        vendor = "Intel"
                    elif "amd" in lower_name or "radeon" in lower_name:
                        vendor = "AMD"

                    adapter_ram = item.get("AdapterRAM") or 0
                    ram_gb = round(int(adapter_ram) / (1024**3), 2) if adapter_ram else 0.0

                    gpus.append({
                        "name": g_name,
                        "vendor": vendor,
                        "is_discrete": is_discrete,
                        "driver_version": drv,
                        "vram_total_mb": round(ram_gb * 1024, 2),
                        "vram_total_gb": ram_gb if is_discrete else 0.0,  # Never count shared iGPU memory as dedicated VRAM
                        "vram_free_gb": None,
                        "vram_used_gb": None,
                        "source": "Win32_VideoController",
                    })
            except Exception:
                pass

        return gpus

    def get_preferred_llm_gpu(self, gpus: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Selects the discrete GPU with the largest dedicated VRAM for LLM inference."""
        discrete = [g for g in gpus if g.get("is_discrete")]
        if not discrete:
            return None
        # Sort by VRAM descending
        discrete.sort(key=lambda g: g.get("vram_total_gb", 0.0), reverse=True)
        return discrete[0]

    def detect_storage(self) -> List[Dict[str, Any]]:
        """Detects drive free and total space for project and system drives."""
        storage: List[Dict[str, Any]] = []
        ps_script = (
            "Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | "
            "Select-Object DeviceID, FileSystem, Size, FreeSpace | "
            "ConvertTo-Json -Compress"
        )
        raw = run_powershell(ps_script)
        if raw:
            try:
                data = json.loads(raw)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    dev_id = item.get("DeviceID", "").strip()
                    fs = item.get("FileSystem", "").strip()
                    total_bytes = int(item.get("Size") or 0)
                    free_bytes = int(item.get("FreeSpace") or 0)

                    storage.append({
                        "drive": dev_id,
                        "filesystem": fs,
                        "total_gb": round(total_bytes / (1024**3), 2),
                        "free_gb": round(free_bytes / (1024**3), 2),
                    })
            except Exception:
                pass

        return storage

    def detect_toolchain(self) -> Dict[str, Any]:
        """Detects presence and versions of developer tools."""
        tools: Dict[str, Any] = {}

        # Python
        tools["python"] = {
            "installed": True,
            "version": platform.python_version(),
            "path": sys.executable,
        }

        # Git
        git_path = shutil.which("git")
        git_ver = run_cmd(["git", "--version"]) if git_path else None
        tools["git"] = {
            "installed": bool(git_path),
            "version": git_ver.replace("git version", "").strip() if git_ver else None,
            "path": git_path,
        }

        # Pip
        pip_path = shutil.which("pip")
        pip_ver = run_cmd(["pip", "--version"]) if pip_path else None
        tools["pip"] = {
            "installed": bool(pip_path),
            "version": pip_ver.split()[1] if pip_ver and len(pip_ver.split()) > 1 else None,
            "path": pip_path,
        }

        # PowerShell
        ps_path = shutil.which("powershell")
        ps_ver = run_cmd(["powershell", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"]) if ps_path else None
        tools["powershell"] = {
            "installed": bool(ps_path),
            "version": ps_ver,
            "path": ps_path,
        }

        return tools

    def detect_ollama(self) -> Dict[str, Any]:
        """
        Detects Ollama CLI, reachability of local service (WITHOUT launching a new server),
        and safely inventories installed models.
        """
        ollama_bin = shutil.which("ollama")
        installed = bool(ollama_bin)
        version_str = None
        if installed:
            v_out = run_cmd(["ollama", "--version"])
            if v_out:
                match = re.search(r"(\d+\.\d+\.\d+)", v_out)
                version_str = match.group(1) if match else v_out.strip()

        # Check if local Ollama HTTP API is reachable (timeout = 1.0s)
        service_reachable = False
        try:
            req = urllib.request.Request("http://127.0.0.1:11434/api/tags", headers={"User-Agent": "SalesAI-Audit"})
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    service_reachable = True
        except Exception:
            service_reachable = False

        # Inventory installed models via 'ollama list' or /api/tags
        models: List[Dict[str, Any]] = []
        if installed:
            list_out = run_cmd(["ollama", "list"])
            if list_out:
                lines = list_out.strip().splitlines()
                # Skip header line (NAME ID SIZE MODIFIED)
                for line in lines[1:]:
                    parts = line.split()
                    if len(parts) >= 3:
                        name = parts[0]
                        size = parts[2]
                        if len(parts) >= 4 and parts[3].upper() in ("B", "KB", "MB", "GB", "TB"):
                            size = f"{parts[2]} {parts[3]}"
                        models.append({
                            "name": name,
                            "size": size,
                        })

        return {
            "installed": installed,
            "service_reachable": service_reachable,
            "version": version_str,
            "path": ollama_bin,
            "models": models,
        }

    def classify_capacity(
        self,
        memory: Dict[str, Any],
        preferred_gpu: Optional[Dict[str, Any]],
        storage: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Calculates conservative candidate model tiers based on hardware heuristics.
        Does not claim benchmark performance; defers final choice to Step 6.
        """
        ram_gb = memory.get("total_gb", 0.0)
        vram_gb = preferred_gpu.get("vram_total_gb", 0.0) if preferred_gpu else 0.0
        has_gpu = bool(preferred_gpu and vram_gb > 0)

        # Free disk check on target project / system drive
        target_drive_letter = str(self.project_root.drive).upper() or "C:"
        target_drive = next((s for s in storage if str(s.get("drive", "")).upper() == target_drive_letter), None)
        if not target_drive and storage:
            # Fall back to C: or first drive
            target_drive = next((s for s in storage if str(s.get("drive", "")).upper() == "C:"), storage[0])

        free_disk_gb = target_drive.get("free_gb", 999.0) if target_drive else 999.0

        tier_statuses: List[Dict[str, Any]] = []
        warnings: List[str] = []

        tiers = ["1.5B", "3B", "7B/8B Q4", "14B Q4", "20B/22B Q4", "32B Q4", "70B Q4"]

        for tier in tiers:
            status = "not_recommended"
            reason = ""

            if not has_gpu:
                # CPU / System RAM only
                if tier in ["1.5B", "3B"]:
                    status = "recommended"
                    reason = "CPU inference suitable for fast lightweight tasks."
                elif tier == "7B/8B Q4":
                    if ram_gb >= 14.5:  # Standard 16GB system often reports 15.6GB-15.8GB due to hardware reservation
                        status = "possible_but_slow"
                        reason = "CPU-bound; workable for low throughput."
                    else:
                        status = "not_recommended"
                        reason = "Insufficient RAM for CPU 8B inference."
                else:
                    status = "not_recommended"
                    reason = "Requires dedicated GPU for practical throughput."

            elif vram_gb < 6.0:
                # ~4GB VRAM + RAM
                if tier == "1.5B":
                    status = "recommended"
                    reason = "Fits comfortably in VRAM."
                elif tier == "3B":
                    status = "recommended"
                    reason = "Optimal fit for 4GB VRAM."
                elif tier == "7B/8B Q4":
                    status = "worth_benchmarking"
                    reason = "Partial VRAM offload / hybrid CPU-GPU."
                else:
                    status = "not_recommended"
                    reason = "Exceeds 4GB VRAM capacity."

            elif vram_gb < 10.0:
                # ~8GB VRAM
                if tier in ["1.5B", "3B"]:
                    status = "recommended"
                    reason = "Extremely fast on 8GB VRAM."
                elif tier == "7B/8B Q4":
                    status = "recommended"
                    reason = "Fully fits in 8GB VRAM (recommended baseline)."
                elif tier == "14B Q4":
                    status = "worth_benchmarking"
                    reason = "Partial offload with system RAM."
                else:
                    status = "not_recommended"
                    reason = "20B+ exceeds 8GB VRAM offload budget."

            elif vram_gb < 14.0:
                # ~12GB VRAM
                if tier in ["1.5B", "3B", "7B/8B Q4"]:
                    status = "recommended"
                    reason = "Fits fully in 12GB VRAM."
                elif tier == "14B Q4":
                    status = "recommended"
                    reason = "Strong candidate; fully fits in 12GB VRAM."
                elif tier == "20B/22B Q4":
                    status = "worth_benchmarking"
                    reason = "Worth benchmarking with RAM spillover."
                elif tier == "32B Q4":
                    status = "possible_but_slow"
                    reason = "Heavy hybrid offload required."
                else:
                    status = "not_recommended"
                    reason = "Exceeds 12GB VRAM capability."

            elif vram_gb < 20.0:
                # ~16GB VRAM
                if tier in ["1.5B", "3B", "7B/8B Q4", "14B Q4"]:
                    status = "recommended"
                    reason = "Fully offloaded in 16GB VRAM."
                elif tier == "20B/22B Q4":
                    status = "recommended"
                    reason = "Strong candidate; fits in 16GB VRAM."
                elif tier == "32B Q4":
                    status = "worth_benchmarking"
                    reason = "High parameter benchmark candidate."
                elif tier == "70B Q4":
                    status = "possible_but_slow"
                    reason = "Requires 64GB+ system RAM offload."
                else:
                    status = "not_recommended"
                    reason = "Too large for 16GB VRAM."

            else:
                # >= 24GB VRAM
                if tier in ["1.5B", "3B", "7B/8B Q4", "14B Q4", "20B/22B Q4", "32B Q4"]:
                    status = "recommended"
                    reason = "Full GPU acceleration."
                elif tier == "70B Q4":
                    status = "worth_benchmarking"
                    reason = "High-tier benchmark candidate."

            # Check disk space safety
            est_disk = MODEL_DISK_ESTIMATES.get(tier, 5.0)
            if free_disk_gb < (est_disk + MIN_FREE_DISK_AFTER_MODEL_GB):
                warnings.append(
                    f"Drive space warning: downloading {tier} (~{est_disk}GB) leaves less than "
                    f"{MIN_FREE_DISK_AFTER_MODEL_GB}GB reserve on disk ({free_disk_gb:.1f}GB free)."
                )
                if status == "recommended":
                    status = "worth_benchmarking"

            tier_statuses.append({
                "tier": tier,
                "status": status,
                "estimated_download_gb": est_disk,
                "notes": reason,
            })

        return {
            "recommended_test_tiers": tier_statuses,
            "warnings": list(set(warnings)),
            "final_model_selected": None,  # Strictly deferred to Step 6
        }

    def run_audit(self) -> Dict[str, Any]:
        """Runs the complete hardware audit and returns normalized profile dictionary."""
        os_info = self.detect_os()
        cpu_info = self.detect_cpu()
        memory_info = self.detect_memory()
        gpus_info = self.detect_gpus()
        preferred_gpu = self.get_preferred_llm_gpu(gpus_info)
        storage_info = self.detect_storage()
        toolchain_info = self.detect_toolchain()
        ollama_info = self.detect_ollama()

        capacity_info = self.classify_capacity(
            memory=memory_info,
            preferred_gpu=preferred_gpu,
            storage=storage_info,
        )

        profile = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "machine_label": "hamed_laptop",
            "os": os_info,
            "cpu": cpu_info,
            "memory": memory_info,
            "gpus": gpus_info,
            "preferred_llm_gpu": preferred_gpu,
            "storage": storage_info,
            "toolchain": toolchain_info,
            "ollama": ollama_info,
            "capacity": capacity_info,
        }
        return profile

    def generate_markdown_report(self, profile: Dict[str, Any]) -> str:
        """Renders human-readable HARDWARE-REPORT.md from profile."""
        os_info = profile["os"]
        cpu = profile["cpu"]
        mem = profile["memory"]
        gpus = profile["gpus"]
        pref_gpu = profile["preferred_llm_gpu"]
        storage = profile["storage"]
        tools = profile["toolchain"]
        ollama = profile["ollama"]
        cap = profile["capacity"]

        gpu_desc = f"{pref_gpu['name']} ({pref_gpu.get('vram_total_gb', 0)} GB VRAM)" if pref_gpu else "Integrated / No Discrete GPU"

        md = []
        md.append("# SalesAI Hardware Audit & AI Capacity Report")
        md.append(f"> **Generated at**: `{profile['generated_at']}`  ")
        md.append(f"> **Machine Label**: `{profile['machine_label']}`  ")
        md.append("> **Status**: Preliminary Hardware Profile — **Final model selection strictly deferred to Step 6 Benchmark**.\n")

        md.append("## 1. System Summary")
        md.append("| Property | Detected Specification |")
        md.append("| :--- | :--- |")
        md.append(f"| **Operating System** | {os_info.get('caption', 'Windows')} ({os_info.get('architecture', 'x64')}) |")
        md.append(f"| **Processor** | {cpu.get('name', 'Unknown')} ({cpu.get('physical_cores', 0)} cores / {cpu.get('logical_processors', 0)} threads) |")
        md.append(f"| **System RAM** | {mem.get('total_gb', 0)} GB Total ({mem.get('free_gb', 0)} GB Free) |")
        md.append(f"| **Primary GPU** | {gpu_desc} |")
        md.append(f"| **Ollama Service** | {'Reachable (:11434)' if ollama.get('service_reachable') else ('Installed but service stopped' if ollama.get('installed') else 'Not Installed')} |\n")

        md.append("## 2. Processor (CPU)")
        md.append(f"- **Model**: {cpu.get('name')}")
        md.append(f"- **Manufacturer**: {cpu.get('manufacturer')}")
        md.append(f"- **Physical Cores**: {cpu.get('physical_cores')}")
        md.append(f"- **Logical Threads**: {cpu.get('logical_processors')}")
        md.append(f"- **Architecture**: {cpu.get('architecture')}\n")

        md.append("## 3. System Memory (RAM)")
        md.append(f"- **Total RAM**: {mem.get('total_gb')} GB ({mem.get('total_bytes', 0):,} bytes)")
        md.append(f"- **Free RAM**: {mem.get('free_gb')} GB\n")

        md.append("## 4. Graphics Adapters (GPU / VRAM)")
        if not gpus:
            md.append("*No display adapters detected.*")
        else:
            for i, g in enumerate(gpus, 1):
                disc_tag = "Discrete / Dedicated" if g.get("is_discrete") else "Integrated"
                vram_info = f"{g.get('vram_total_gb', 0)} GB" if g.get("vram_total_gb") else "Shared / Unspecified"
                md.append(f"### GPU {i}: {g.get('name')}")
                md.append(f"- **Type**: {disc_tag}")
                md.append(f"- **Vendor**: {g.get('vendor')}")
                md.append(f"- **Dedicated VRAM**: {vram_info}")
                md.append(f"- **Driver Version**: {g.get('driver_version') or 'N/A'}")
                md.append(f"- **Detection Source**: `{g.get('source')}`\n")

        md.append("## 5. Storage Drives")
        if not storage:
            md.append("*No logical storage volumes detected.*")
        else:
            md.append("| Drive | File System | Total Capacity | Free Space |")
            md.append("| :--- | :--- | :--- | :--- |")
            for d in storage:
                md.append(f"| `{d.get('drive')}` | {d.get('filesystem')} | {d.get('total_gb')} GB | {d.get('free_gb')} GB |")
            md.append("")

        md.append("## 6. Developer Toolchain")
        md.append("| Tool | Installed | Version | Path |")
        md.append("| :--- | :--- | :--- | :--- |")
        for tname, tinfo in tools.items():
            inst = "✅ Yes" if tinfo.get("installed") else "❌ No"
            v = tinfo.get("version") or "N/A"
            p = f"`{tinfo.get('path')}`" if tinfo.get("path") else "-"
            md.append(f"| **{tname.title()}** | {inst} | {v} | {p} |")
        md.append("")

        md.append("## 7. Ollama Status & Existing Models")
        md.append(f"- **CLI Installed**: {'Yes' if ollama.get('installed') else 'No'}")
        md.append(f"- **Service Reachable**: {'Yes (http://127.0.0.1:11434)' if ollama.get('service_reachable') else 'No'}")
        md.append(f"- **Version**: {ollama.get('version') or 'N/A'}")

        models = ollama.get("models", [])
        if models:
            md.append("\n**Existing Installed Models:**")
            for m in models:
                md.append(f"- `{m.get('name')}` ({m.get('size')})")
        else:
            md.append("\n*No local Ollama models currently installed.*")
        md.append("")

        md.append("## 8. Candidate Model Capacity Tiers (Conservative Heuristics)")
        md.append("> **Note**: These tiers represent preliminary candidates based on hardware dimensions. Final selection requires actual throughput measurement in Step 6.\n")
        md.append("| Model Tier | Preliminary Status | Est. Download | Rationale |")
        md.append("| :--- | :--- | :--- | :--- |")
        for t in cap.get("recommended_test_tiers", []):
            st_badge = {
                "recommended": "🟢 **recommended**",
                "worth_benchmarking": "🟡 **worth_benchmarking**",
                "possible_but_slow": "🟠 *possible_but_slow*",
                "not_recommended": "🔴 not_recommended",
            }.get(t["status"], t["status"])
            md.append(f"| `{t['tier']}` | {st_badge} | ~{t.get('estimated_download_gb')} GB | {t.get('notes')} |")
        md.append("")

        if cap.get("warnings"):
            md.append("## 9. Hardware & Disk Warnings")
            for w in cap["warnings"]:
                md.append(f"- ⚠️ {w}")
            md.append("")

        md.append("## 10. Next Step")
        md.append("1. Operator reviews this hardware report.")
        md.append("2. Proceed to **Step 6 — LLM Benchmark & Final Model Selection** to run live speed/accuracy benchmarks on the recommended candidate tiers.")
        md.append("3. Once benchmarked, select the final model for production deployment.\n")

        return "\n".join(md)

    def save_report(self, output_dir: Optional[Path] = None) -> Dict[str, Path]:
        """Saves hardware_profile.json and HARDWARE-REPORT.md."""
        target_dir = output_dir or (self.project_root / "runtime" / "hardware")
        target_dir.mkdir(parents=True, exist_ok=True)

        profile = self.run_audit()
        md_text = self.generate_markdown_report(profile)

        json_path = target_dir / "hardware_profile.json"
        md_path = target_dir / "HARDWARE-REPORT.md"

        json_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        md_path.write_text(md_text, encoding="utf-8")

        return {
            "json": json_path,
            "markdown": md_path,
        }


def main():
    auditor = HardwareAuditor()
    paths = auditor.save_report()
    print("=" * 65)
    print("🔍 SalesAI Hardware Audit Complete")
    print("=" * 65)
    print(f"JSON Profile:     {paths['json']}")
    print(f"Markdown Report:  {paths['markdown']}")
    print("=" * 65)
    sys.exit(0)


if __name__ == "__main__":
    main()
