# SalesAI Production Readiness — Step 5
## Hamed Laptop Hardware Audit & AI Capacity Profile
### Measure First — Choose Model Later

# 0. Baseline

Use the current cleaned repository after accepted Step 4.

Do NOT change Phase 1–12 business logic.

Do NOT install large models in this step.

Do NOT run live email or external discovery.

This step only creates and runs a safe hardware/environment audit so the next step can
choose and benchmark the right local AI model for Hamed's laptop.

---

# 1. Goal

Create a repeatable Windows hardware audit that answers:

```text
What CPU does the laptop have?
How much RAM?
What GPU?
How much dedicated VRAM?
Is NVIDIA available?
What NVIDIA driver?
How much free disk space?
What Windows version?
What architecture?
What Python version?
What Git version?
Is Ollama installed?
Which Ollama models are already installed?
Is the Ollama service reachable?
```

Then produce a conservative AI-capability profile:

```text
small_model
medium_model
large_model_candidate
not_recommended
```

This is a recommendation only.

No model is automatically downloaded in Step 5.

---

# 2. Primary Scripts

Create:

```text
setup/hardware_audit.ps1
scripts/hardware_profile.py
```

Optional:

```text
setup/HARDWARE-AUDIT.bat
```

for double-click execution.

PowerShell should gather Windows-native facts.

Python should normalize the result and generate the model-capability recommendation.

---

# 3. Output Files

Runtime outputs must NOT be committed or packaged.

Recommended:

```text
runtime/hardware/hardware_profile.json
runtime/hardware/HARDWARE-REPORT.md
```

The JSON is machine-readable.

The Markdown report is human-readable.

These paths must be ignored by Git/release packaging.

---

# 4. CPU Detection

Collect at minimum:

```text
manufacturer
model/name
physical cores
logical processors / threads
max clock if available
architecture
```

Use Windows/CIM sources, for example:

```powershell
Get-CimInstance Win32_Processor
```

Do not depend only on marketing strings.

---

# 5. RAM Detection

Collect:

```text
total physical RAM
available/free RAM if measurable
```

Normalize to:

```text
GB
```

Do not round so aggressively that 15.8 GB becomes misleadingly 16 if exact raw bytes are
available; report both raw and readable value where useful.

---

# 6. GPU Detection

Use Windows GPU inventory:

```powershell
Get-CimInstance Win32_VideoController
```

Collect:

```text
GPU name
adapter RAM if trustworthy
driver version
```

Important:
Windows WMI `AdapterRAM` is not always reliable for modern GPUs.

If NVIDIA is present and `nvidia-smi` exists, use `nvidia-smi` as the preferred source for:

```text
GPU name
driver version
VRAM total
VRAM free
VRAM used
```

Do not fail if NVIDIA is absent.

---

# 7. NVIDIA Query

If available, run a deterministic query such as:

```text
nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used,memory.free --format=csv,noheader,nounits
```

Parse safely.

No shell interpolation from user input.

---

# 8. Multi-GPU

If the laptop has:

```text
integrated GPU
+
dedicated GPU
```

preserve both in inventory.

For local LLM capacity, prefer the dedicated discrete GPU.

Do not add iGPU shared system memory to dedicated VRAM.

---

# 9. Storage Detection

Collect project/system drive information:

```text
drive
filesystem
total GB
free GB
```

At minimum check the drive where SalesAI will be installed.

Model installation recommendations must consider free disk space.

---

# 10. Operating System

Collect:

```text
Windows edition
version
build
64-bit / architecture
```

Do not require admin privileges merely to read OS details.

---

# 11. Toolchain Detection

Collect availability/version for:

```text
Python
pip
Git
PowerShell
Ollama
```

Where installed.

Example statuses:

```text
installed
missing
version_unknown
```

---

# 12. Ollama Detection

Check:

```text
ollama --version
ollama list
```

Then test local service reachability safely.

Preferred local endpoint:

```text
http://127.0.0.1:11434
```

Do not start a second Ollama server if one is already running.

Record:

```text
installed
service_reachable
installed_models[]
```

Do not expose unrelated environment secrets.

---

# 13. Current Models

For installed Ollama models record only safe metadata:

```text
name
size
modified date if available
```

No model should be pulled or removed in Step 5.

---

# 14. Optional CUDA / Driver Information

If NVIDIA tooling exposes it, record:

```text
driver version
reported CUDA compatibility/version
```

But do not require CUDA Toolkit installation.

Ollama can use the driver/runtime without the developer CUDA Toolkit.

Do not fail the audit if `nvcc` is missing.

---

# 15. AI Capacity Classification

Create a conservative classification based on:

```text
system RAM
dedicated VRAM
GPU presence
free disk
CPU
```

Do not claim benchmark performance before Step 6.

Recommended output format:

```text
recommended_test_tiers:
- 1.5B
- 3B
- 7B/8B Q4
- 14B Q4
- 20B/22B Q4
- 32B Q4
- 70B Q4

status:
recommended
worth_benchmarking
possible_but_slow
not_recommended
```

This is a candidate matrix, not a final model decision.

---

# 16. Conservative Heuristics

Use conservative baseline heuristics only.

Suggested guidance:

## No dedicated GPU / <= 16 GB system RAM

```text
1.5B / 3B: recommended
7B/8B Q4: possible but CPU-bound / slow
14B+: not recommended
```

## 4 GB VRAM + 16 GB RAM

```text
3B: recommended
7B/8B Q4: worth benchmarking / hybrid
14B+: not recommended
```

## 8 GB VRAM + 32 GB RAM

```text
7B/8B Q4: recommended
14B Q4: worth benchmarking / partial offload
20B+: generally not preferred
```

## 12 GB VRAM + >= 32 GB RAM

```text
7B/8B: recommended
14B Q4: recommended / strong candidate
20B/22B Q4: worth benchmarking
32B Q4: possible hybrid, not default
```

## 16 GB VRAM + >= 64 GB RAM

```text
14B: recommended
20B/22B Q4: recommended candidate
32B Q4: worth benchmarking / hybrid
70B Q4: possible RAM-heavy test, not default
```

## >= 24 GB VRAM + >= 64 GB RAM

```text
14B / 22B / 32B Q4: strong candidates
70B Q4: worth benchmarking depending on RAM/VRAM
```

These are not measured performance claims.

---

# 17. Disk Safety

Before recommending a model tier for download, check free disk.

Reserve headroom for:

```text
Windows
Python environments
repository
runtime databases
logs
multiple candidate models during benchmark
```

Do not recommend consuming nearly all free disk space.

A minimum free-space reserve should remain configurable.

Example:

```text
MIN_FREE_DISK_AFTER_MODEL_GB = 25
```

---

# 18. Hardware Profile Schema

Recommended JSON:

```json
{
  "generated_at": "...",
  "machine_label": "hamed_laptop",
  "os": {},
  "cpu": {},
  "memory": {},
  "gpus": [],
  "preferred_llm_gpu": {},
  "storage": [],
  "toolchain": {},
  "ollama": {
    "installed": true,
    "service_reachable": true,
    "version": "...",
    "models": []
  },
  "capacity": {
    "recommended_test_tiers": [],
    "warnings": [],
    "final_model_selected": null
  }
}
```

Do not include:

```text
username
full home path
Wi-Fi SSID
IP addresses other than local Ollama status
serial number
device UUID
Windows product key
MAC address
personal files
```

---

# 19. Privacy

The hardware report is technical.

Do not collect personally identifying machine identifiers unless strictly required.

Explicitly exclude:

```text
BIOS serial
motherboard serial
Windows license/product key
MAC addresses
user account name
browser data
network credentials
```

---

# 20. Human-Readable Report

Generate:

```text
runtime/hardware/HARDWARE-REPORT.md
```

Sections:

```text
System Summary
CPU
RAM
GPU / VRAM
Storage
Windows
Developer Toolchain
Ollama Status
Installed Models
Candidate Model Tiers
Warnings
Next Step
```

The report must clearly say:

```text
Final model selection requires Step 6 benchmark.
```

---

# 21. Exit Codes

Recommended:

```text
0 = audit completed successfully
1 = partial audit / recoverable missing tooling
2 = critical audit failure
```

Missing Ollama is NOT a critical hardware-audit failure.

---

# 22. Robustness

The audit must work even if:

```text
Ollama not installed
Git not installed
Python version differs
NVIDIA absent
nvidia-smi absent
only integrated GPU exists
multiple drives exist
multiple GPUs exist
```

It should report missing components, not crash.

---

# 23. No Installation Yet

Step 5 MUST NOT automatically:

```text
install Python
install Git
install Ollama
install drivers
pull models
modify PATH
change Windows settings
```

Installation comes later.

---

# 24. UI Integration

Optional but useful:

Add a read-only:

```text
/sales/system
```

or system card that reads an existing generated hardware profile.

Do not make UI work a blocker for Step 5.

The CLI/report output is the primary requirement.

---

# 25. Future Installer Input

The generated hardware profile must be usable later by the installer.

For example:

```text
hardware_profile.json
→ installer model recommendation
→ operator confirms
→ selected model installed
```

No automatic decision in Step 5.

---

# 26. Required Tests

Create:

```text
tests/test_hardware_audit.py
```

Cover at least:

```text
PRD-HW-001 profile schema is valid
PRD-HW-002 audit excludes sensitive machine identifiers
PRD-HW-003 missing nvidia-smi does not crash
PRD-HW-004 integrated-only GPU is handled
PRD-HW-005 NVIDIA VRAM uses nvidia-smi when available
PRD-HW-006 multi-GPU keeps separate GPU entries
PRD-HW-007 RAM normalized correctly
PRD-HW-008 disk free-space captured
PRD-HW-009 missing Ollama reported safely
PRD-HW-010 running Ollama detected without launching duplicate server
PRD-HW-011 installed Ollama models parsed safely
PRD-HW-012 no models downloaded by audit
PRD-HW-013 4GB VRAM profile does not recommend 32B as default
PRD-HW-014 8GB/32GB profile recommends 7B/8B and may benchmark 14B
PRD-HW-015 12GB/32GB profile includes 14B strong candidate
PRD-HW-016 16GB/64GB profile includes 22B/32B benchmark candidates
PRD-HW-017 insufficient disk produces warning
PRD-HW-018 report explicitly defers final model choice to benchmark
PRD-HW-019 runtime hardware report excluded from release ZIP
PRD-HW-020 Phase 1–12 + Production Steps 1–4 regressions remain green
```

---

# 27. Full Regression

Run:

```text
python -m pytest tests/test_hardware_audit.py -v
python -m pytest tests/ -q
```

Require zero new deterministic failures.

---

# 28. Real Laptop Execution

After implementation, DO NOT invent Hamed's laptop specifications.

Provide the exact command to run on Hamed's Windows laptop.

Preferred:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup\hardware_audit.ps1
```

or:

```text
setup\HARDWARE-AUDIT.bat
```

The resulting:

```text
runtime/hardware/hardware_profile.json
runtime/hardware/HARDWARE-REPORT.md
```

must be returned for review before Step 6.

---

# 29. Packaging

Rebuild the developer/source ZIP.

The generated real hardware report must remain runtime-only and excluded.

The audit scripts themselves should be included.

Denylist must remain clean.

---

# 30. Completion Gate

Step 5 implementation is accepted when:

```text
hardware audit script exists
CPU/RAM/GPU/VRAM/storage/OS detected
NVIDIA path works when available
non-NVIDIA path works safely
Ollama/toolchain status detected
no models are installed automatically
no personal machine identifiers collected
candidate model tiers generated conservatively
final model choice explicitly deferred
hardware profile usable by future installer
all tests green
release ZIP clean
```

Then run the audit on Hamed's actual laptop.

Do NOT proceed to model benchmark until the actual hardware report is reviewed.

---

# 31. Final Response

Return:

1. files created/modified
2. hardware audit command
3. hardware profile schema
4. CPU/RAM/GPU/storage detection
5. Ollama/toolchain detection
6. privacy exclusions
7. model-capacity heuristics
8. tests
9. full repository regression
10. Git commit
11. ZIP file count
12. denylist result
13. final SalesAI_export.zip
14. exact instructions for running the audit on Hamed's laptop
