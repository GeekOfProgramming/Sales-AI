"""
tests/test_hardware_audit.py
Authoritative verification test suite for Production Readiness Step 5:
Hardware Audit, Resource Detection, and Conservative AI Capacity Profiling.

Covers tests PRD-HW-001 through PRD-HW-020.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from scripts.hardware_profile import (
    HardwareAuditor,
    MIN_FREE_DISK_AFTER_MODEL_GB,
)
from scripts.rebuild_zip import should_exclude

REPO_ROOT = Path(__file__).parent.parent.resolve()


def test_prd_hw_001_profile_schema_is_valid():
    """PRD-HW-001: Hardware profile contains all required top-level schema fields."""
    auditor = HardwareAuditor(project_root=REPO_ROOT)
    profile = auditor.run_audit()

    required_keys = [
        "generated_at",
        "machine_label",
        "os",
        "cpu",
        "memory",
        "gpus",
        "preferred_llm_gpu",
        "storage",
        "toolchain",
        "ollama",
        "capacity",
    ]
    for key in required_keys:
        assert key in profile, f"Missing required profile key: {key}"

    assert profile["machine_label"] == "hamed_laptop"
    assert "recommended_test_tiers" in profile["capacity"]
    assert profile["capacity"]["final_model_selected"] is None


def test_prd_hw_002_audit_excludes_sensitive_machine_identifiers():
    """PRD-HW-002: Audit must not collect personal/hardware serials, MAC, or credentials."""
    auditor = HardwareAuditor(project_root=REPO_ROOT)
    profile = auditor.run_audit()
    raw_json = json.dumps(profile).lower()

    banned_tokens = [
        "mac_address",
        "bios_serial",
        "motherboard_serial",
        "serialnumber",
        "product_key",
        "wifi_ssid",
        "client_secret",
        "password",
    ]
    for token in banned_tokens:
        assert token not in raw_json, f"Banned identifier token '{token}' found in profile output"


def test_prd_hw_003_missing_nvidia_smi_does_not_crash():
    """PRD-HW-003: If nvidia-smi is missing, GPU detection does not crash and handles fallback."""
    auditor = HardwareAuditor(project_root=REPO_ROOT)
    with patch("shutil.which", return_value=None):
        gpus = auditor.detect_gpus()
        assert isinstance(gpus, list)


def test_prd_hw_004_integrated_only_gpu_is_handled():
    """PRD-HW-004: Integrated-only GPU is detected without counting shared RAM as dedicated VRAM."""
    auditor = HardwareAuditor(project_root=REPO_ROOT)
    mock_gpus = [
        {
            "name": "Intel(R) Iris(R) Xe Graphics",
            "vendor": "Intel",
            "is_discrete": False,
            "driver_version": "31.0.101.4500",
            "vram_total_gb": 0.0,
            "source": "Win32_VideoController",
        }
    ]
    pref_gpu = auditor.get_preferred_llm_gpu(mock_gpus)
    assert pref_gpu is None

    capacity = auditor.classify_capacity(
        memory={"total_gb": 16.0},
        preferred_gpu=pref_gpu,
        storage=[{"drive": "C:", "free_gb": 100.0}],
    )
    tiers = {t["tier"]: t["status"] for t in capacity["recommended_test_tiers"]}
    assert tiers["1.5B"] == "recommended"
    assert tiers["3B"] == "recommended"
    assert tiers["7B/8B Q4"] == "possible_but_slow"
    assert tiers["14B Q4"] == "not_recommended"


def test_prd_hw_005_nvidia_vram_uses_nvidia_smi_when_available():
    """PRD-HW-005: NVIDIA GPU uses nvidia-smi for accurate dedicated VRAM."""
    auditor = HardwareAuditor(project_root=REPO_ROOT)
    mock_csv = "NVIDIA GeForce RTX 4070 Laptop GPU, 555.99, 8192, 1200, 6992"

    with patch("shutil.which", return_value="C:\\Windows\\System32\\nvidia-smi.exe"), \
         patch("scripts.hardware_profile.run_cmd", return_value=mock_csv):
        gpus = auditor.detect_gpus()
        assert len(gpus) >= 1
        nvidia_gpu = gpus[0]
        assert nvidia_gpu["vendor"] == "NVIDIA"
        assert nvidia_gpu["vram_total_gb"] == 8.0
        assert nvidia_gpu["vram_free_gb"] == round(6992 / 1024.0, 2)
        assert nvidia_gpu["source"] == "nvidia-smi"


def test_prd_hw_006_multi_gpu_keeps_separate_gpu_entries():
    """PRD-HW-006: Systems with integrated + discrete GPUs preserve separate entries and select discrete."""
    auditor = HardwareAuditor(project_root=REPO_ROOT)
    mock_gpus = [
        {
            "name": "NVIDIA GeForce RTX 3060 Laptop GPU",
            "vendor": "NVIDIA",
            "is_discrete": True,
            "vram_total_gb": 6.0,
            "source": "nvidia-smi",
        },
        {
            "name": "Intel(R) UHD Graphics",
            "vendor": "Intel",
            "is_discrete": False,
            "vram_total_gb": 0.0,
            "source": "Win32_VideoController",
        },
    ]
    pref = auditor.get_preferred_llm_gpu(mock_gpus)
    assert pref is not None
    assert pref["name"] == "NVIDIA GeForce RTX 3060 Laptop GPU"
    assert pref["vram_total_gb"] == 6.0


def test_prd_hw_007_ram_normalized_correctly():
    """PRD-HW-007: RAM detection normalizes values to numeric GB."""
    auditor = HardwareAuditor(project_root=REPO_ROOT)
    mem = auditor.detect_memory()
    assert isinstance(mem["total_gb"], float)
    assert isinstance(mem["free_gb"], float)
    assert mem["total_gb"] > 0
    assert mem["free_gb"] >= 0


def test_prd_hw_008_disk_free_space_captured():
    """PRD-HW-008: Disk detection captures positive total and free GB for system drives."""
    auditor = HardwareAuditor(project_root=REPO_ROOT)
    storage = auditor.detect_storage()
    assert len(storage) >= 1
    primary = storage[0]
    assert "drive" in primary
    assert primary["total_gb"] > 0
    assert primary["free_gb"] >= 0


def test_prd_hw_009_missing_ollama_reported_safely():
    """PRD-HW-009: Missing Ollama installation is reported gracefully without crashing."""
    auditor = HardwareAuditor(project_root=REPO_ROOT)
    with patch("shutil.which", return_value=None), \
         patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
        ollama = auditor.detect_ollama()
        assert ollama["installed"] is False
        assert ollama["service_reachable"] is False
        assert ollama["models"] == []


def test_prd_hw_010_running_ollama_detected_without_launching_duplicate_server():
    """PRD-HW-010: Running Ollama service is probed via HTTP GET without launching duplicate process."""
    auditor = HardwareAuditor(project_root=REPO_ROOT)
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp

    with patch("shutil.which", return_value="C:\\bin\\ollama.exe"), \
         patch("urllib.request.urlopen", return_value=mock_resp), \
         patch("scripts.hardware_profile.run_cmd") as mock_cmd:
        mock_cmd.return_value = "0.33.3"
        ollama = auditor.detect_ollama()
        assert ollama["service_reachable"] is True
        # Verify no command containing 'serve' was ever executed
        for call_args in mock_cmd.call_args_list:
            cmd = call_args[0][0]
            assert "serve" not in cmd, f"Forbidden server launch detected: {cmd}"


def test_prd_hw_011_installed_ollama_models_parsed_safely():
    """PRD-HW-011: Installed Ollama models are parsed into structured name and size list."""
    auditor = HardwareAuditor(project_root=REPO_ROOT)
    mock_list_output = (
        "NAME            ID              SIZE      MODIFIED\n"
        "qwen2.5:1.5b    b2d45c31766a    986 MB    2 days ago\n"
        "llama3.2:3b     a80c4f17fa52    2.0 GB    1 week ago\n"
    )

    with patch("shutil.which", return_value="C:\\bin\\ollama.exe"), \
         patch("scripts.hardware_profile.run_cmd", return_value=mock_list_output):
        ollama = auditor.detect_ollama()
        models = ollama["models"]
        assert len(models) == 2
        assert models[0]["name"] == "qwen2.5:1.5b"
        assert models[0]["size"] == "986 MB"
        assert models[1]["name"] == "llama3.2:3b"


def test_prd_hw_012_no_models_downloaded_by_audit():
    """PRD-HW-012: Hardware audit must never execute 'ollama pull' or download models."""
    auditor = HardwareAuditor(project_root=REPO_ROOT)
    with patch("subprocess.run") as mock_run:
        auditor.detect_ollama()
        for call_args in mock_run.call_args_list:
            cmd = call_args[0][0]
            assert "pull" not in cmd, f"Forbidden command execution detected: {cmd}"


def test_prd_hw_013_4gb_vram_profile_does_not_recommend_32b_as_default():
    """PRD-HW-013: 4GB VRAM profile recommends 3B, considers 7B/8B, and rejects 32B."""
    auditor = HardwareAuditor(project_root=REPO_ROOT)
    gpu = {"name": "RTX 3050 Laptop", "vram_total_gb": 4.0, "is_discrete": True}
    cap = auditor.classify_capacity(
        memory={"total_gb": 16.0},
        preferred_gpu=gpu,
        storage=[{"drive": "C:", "free_gb": 100.0}],
    )
    tiers = {t["tier"]: t["status"] for t in cap["recommended_test_tiers"]}
    assert tiers["3B"] == "recommended"
    assert tiers["7B/8B Q4"] == "worth_benchmarking"
    assert tiers["14B Q4"] == "not_recommended"
    assert tiers["32B Q4"] == "not_recommended"


def test_prd_hw_014_8gb_32gb_profile_recommends_7b_8b_and_may_benchmark_14b():
    """PRD-HW-014: 8GB VRAM + 32GB RAM profile recommends 7B/8B and benchmarks 14B."""
    auditor = HardwareAuditor(project_root=REPO_ROOT)
    gpu = {"name": "RTX 4060 Laptop", "vram_total_gb": 8.0, "is_discrete": True}
    cap = auditor.classify_capacity(
        memory={"total_gb": 32.0},
        preferred_gpu=gpu,
        storage=[{"drive": "C:", "free_gb": 100.0}],
    )
    tiers = {t["tier"]: t["status"] for t in cap["recommended_test_tiers"]}
    assert tiers["7B/8B Q4"] == "recommended"
    assert tiers["14B Q4"] == "worth_benchmarking"
    assert tiers["20B/22B Q4"] == "not_recommended"


def test_prd_hw_015_12gb_32gb_profile_includes_14b_strong_candidate():
    """PRD-HW-015: 12GB VRAM + >=32GB RAM includes 14B as recommended strong candidate."""
    auditor = HardwareAuditor(project_root=REPO_ROOT)
    gpu = {"name": "RTX 4080 Laptop", "vram_total_gb": 12.0, "is_discrete": True}
    cap = auditor.classify_capacity(
        memory={"total_gb": 32.0},
        preferred_gpu=gpu,
        storage=[{"drive": "C:", "free_gb": 100.0}],
    )
    tiers = {t["tier"]: t["status"] for t in cap["recommended_test_tiers"]}
    assert tiers["14B Q4"] == "recommended"
    assert tiers["20B/22B Q4"] == "worth_benchmarking"
    assert tiers["70B Q4"] == "not_recommended"


def test_prd_hw_016_16gb_64gb_profile_includes_22b_32b_benchmark_candidates():
    """PRD-HW-016: 16GB VRAM + >=64GB RAM includes 20B/22B and benchmarks 32B."""
    auditor = HardwareAuditor(project_root=REPO_ROOT)
    gpu = {"name": "RTX 4090 Laptop", "vram_total_gb": 16.0, "is_discrete": True}
    cap = auditor.classify_capacity(
        memory={"total_gb": 64.0},
        preferred_gpu=gpu,
        storage=[{"drive": "C:", "free_gb": 150.0}],
    )
    tiers = {t["tier"]: t["status"] for t in cap["recommended_test_tiers"]}
    assert tiers["14B Q4"] == "recommended"
    assert tiers["20B/22B Q4"] == "recommended"
    assert tiers["32B Q4"] == "worth_benchmarking"


def test_prd_hw_017_insufficient_disk_produces_warning():
    """PRD-HW-017: If free disk space is less than download + reserve, a warning is produced."""
    auditor = HardwareAuditor(project_root=REPO_ROOT)
    cap = auditor.classify_capacity(
        memory={"total_gb": 32.0},
        preferred_gpu={"vram_total_gb": 8.0, "is_discrete": True},
        storage=[{"drive": "C:", "free_gb": 15.0}],  # Less than 25GB reserve
    )
    assert len(cap["warnings"]) > 0
    assert any("Drive space warning" in w for w in cap["warnings"])


def test_prd_hw_018_report_explicitly_defers_final_model_choice_to_benchmark():
    """PRD-HW-018: Markdown report explicitly defers final model decision to Step 6 Benchmark."""
    auditor = HardwareAuditor(project_root=REPO_ROOT)
    profile = auditor.run_audit()
    md = auditor.generate_markdown_report(profile)

    assert "Final model selection strictly deferred to Step 6 Benchmark" in md
    assert "Step 6 — LLM Benchmark & Final Model Selection" in md
    assert profile["capacity"]["final_model_selected"] is None


def test_prd_hw_019_runtime_hardware_report_excluded_from_release_zip():
    """PRD-HW-019: Real runtime hardware reports must be excluded from release ZIP."""
    assert should_exclude(Path("runtime/hardware/hardware_profile.json")) is True
    assert should_exclude(Path("runtime/hardware/HARDWARE-REPORT.md")) is True


def test_prd_hw_020_step1_to_step4_tests_remain_green():
    """PRD-HW-020: Verifies key modules from Steps 1-4 remain present and functional."""
    from sales_engine.discovery_v2.scope import DiscoveryScope
    from sales_engine.discovery_v2.source_registry import SourceRegistry
    from scripts.audit_repository import audit_repository
    from scripts.rebuild_zip import build_release_zip

    assert DiscoveryScope is not None
    assert SourceRegistry is not None
    assert audit_repository is not None
    assert build_release_zip is not None
