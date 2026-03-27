"""GPU detection helper for ImageManager.

Run:  python build/detect_gpu.py

Queries all video controllers, classifies each one, and prints a recommendation
for which onnxruntime variant to install before building the app.

Primary query:  PowerShell Get-CimInstance (structured JSON, always present on Windows 10+)
Fallback query: WMIC /FORMAT:CSV (still present on Windows 11 but deprecated)
"""
from __future__ import annotations

import json
import subprocess
import sys

# ── Virtual/software adapters to ignore ──────────────────────────────────────
_VIRTUAL_KEYWORDS = (
    "microsoft basic display",
    "microsoft remote display",
    "microsoft hyper-v video",
    "vmware",
    "virtualbox",
    "parsec",
    "citrix",
)

# ── Classification ────────────────────────────────────────────────────────────

def classify(name: str) -> str:
    """Return a GPU kind string from the adapter name."""
    gpu_name = name.lower()
    
    # NVIDIA discrete
    if any(kind in gpu_name for kind in ("nvidia", "geforce", "rtx ", "gtx ", "quadro", "tesla")):
        return "nvidia-dgpu"
    
    # Intel discrete (Arc series — require both keywords to avoid false matches)
    if "intel" in gpu_name and "arc" in gpu_name:
        return "intel-dgpu"
    
    # AMD discrete — RX, R-series (R5/R7/R9), Radeon VII, Pro, W-series
    if any(kind in gpu_name for kind in ("radeon rx", "radeon r5", "radeon r7", "radeon r9",
                             "radeon vii", "radeon pro", "rx 5", "rx 6", "rx 7", "rx 9")):
        return "amd-dgpu"
    
    # AMD integrated — "Radeon Graphics", "Radeon Vega" (Ryzen iGPU)
    if "radeon" in gpu_name:
        return "amd-igpu"
    
    # Intel integrated — UHD, Iris, HD Graphics
    if "intel" in gpu_name:
        return "intel-igpu"
    return "unknown"


def _is_virtual(name: str) -> bool:
    gpu_name = name.lower()
    return any(virtual_adapter in gpu_name for virtual_adapter in _VIRTUAL_KEYWORDS)


def pick_best(gpus: list[dict]) -> dict | None:
    """Return the best GPU: prefer dGPU over iGPU, NVIDIA > AMD > Intel."""
    real = [gpu for gpu in gpus if not _is_virtual(gpu["name"])]
    pool = real if real else gpus  # keep originals only if everything was filtered
    for kind in ("nvidia-dgpu", "amd-dgpu", "intel-dgpu"):
        for gpu in pool:
            if classify(gpu["name"]) == kind:
                return gpu
    return pool[0] if pool else None


# ── Query backends ────────────────────────────────────────────────────────────

def _query_powershell() -> list[dict]:
    """Primary: Get-CimInstance via PowerShell with JSON output."""
    cmd = (
        "Get-CimInstance Win32_VideoController "
        "| Select-Object Name, AdapterRAM "
        "| ConvertTo-Json -Compress"
    )
    out = subprocess.check_output(
        ["powershell", "-NoProfile", "-Command", cmd],
        text=True, timeout=15, stderr=subprocess.DEVNULL
    )
    data = json.loads(out.strip())
    if isinstance(data, dict):
        data = [data]
    return [
        {"name": item.get("Name") or "", "ram": item.get("AdapterRAM") or 0}
        for item in data
        if item.get("Name")
    ]


def _query_wmic() -> list[dict]:
    """Fallback: WMIC with CSV output to avoid column-width parsing issues."""
    out = subprocess.check_output(
        ["wmic", "path", "win32_VideoController", "get",
         "Name,AdapterRAM", "/FORMAT:CSV"],
        text=True, timeout=10, stderr=subprocess.DEVNULL
    )
    lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
    if not lines:
        return []
    header = lines[0].split(",")  # e.g. ["Node", "AdapterRAM", "Name"]
    gpus = []
    for line in lines[1:]:
        parts = line.split(",", len(header) - 1)
        if len(parts) < len(header):
            continue
        col = dict(zip(header, parts))
        ram_str = col.get("AdapterRAM", "").strip()
        name = col.get("Name", "").strip()
        if not name:
            continue
        gpus.append({"name": name, "ram": int(ram_str) if ram_str.isdigit() else 0})
    return gpus


def query_gpus() -> list[dict]:
    """Query all video controllers. Tries PowerShell first, falls back to WMIC."""
    for label, fn in [("PowerShell", _query_powershell), ("WMIC", _query_wmic)]:
        try:
            gpus = fn()
            if gpus:
                return gpus
        except Exception as exc:
            print(f"[WARN] {label} query failed: {exc}")
    return []


# ── Recommendation logic ──────────────────────────────────────────────────────

def _ram_mb(ram_bytes: int) -> str:
    if ram_bytes <= 0:
        return "? MB"
    return f"{ram_bytes // (1024 * 1024):,} MB"


def recommendation(kind: str) -> list[str]:
    if kind == "nvidia-dgpu":
        return [
            "pip install onnxruntime-gpu        # CUDA — best perf, requires CUDA 12 + cuDNN",
            "pip install onnxruntime-directml   # DirectML — easier, no CUDA toolkit needed",
        ]
    if kind in ("amd-dgpu", "intel-dgpu", "amd-igpu", "intel-igpu"):
        return [
            "pip install onnxruntime-directml   # DirectML — works on any DX12 GPU",
        ]
    return [
        "pip install onnxruntime            # CPU only — no GPU detected or GPU unrecognized",
    ]


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("=" * 60)
    print("  ImageManager — GPU Detection")
    print("=" * 60)

    gpus = query_gpus()

    print()
    print("=== Detected video controllers ===")
    if not gpus:
        print("  (none found — WMIC and PowerShell both failed)")
    for i, gpu in enumerate(gpus, 1):
        virtual = "  [virtual — skipped]" if _is_virtual(gpu["name"]) else ""
        kind = classify(gpu["name"])
        print(f"  [{i}] {gpu['name']:<45}  {_ram_mb(gpu['ram']):>8}   → {kind}{virtual}")

    best = pick_best(gpus)

    print()
    if best:
        best_kind = classify(best["name"])
        print(f"=== Best GPU: {best['name']} ({best_kind}) ===")
        print()
        print("=== Recommendation ===")
        for line in recommendation(best_kind):
            print(f"  {line}")
    else:
        print("=== No real GPU detected ===")
        print()
        print("=== Recommendation ===")
        for line in recommendation("unknown"):
            print(f"  {line}")

    print()
    print("=== Current ORT providers (installed package) ===")
    try:
        import onnxruntime as ort
        providers = ort.get_available_providers()
        print(f"  {providers}")
        if providers == ["CPUExecutionProvider"]:
            print("  → CPU-only package installed. Install a GPU variant to enable acceleration.")
    except ImportError:
        print("  onnxruntime not importable in this Python environment.")

    print()


if __name__ == "__main__":
    main()
