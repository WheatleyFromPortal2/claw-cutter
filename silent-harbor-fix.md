# Silent Harbor Fix — Ollama Slowdown on Locked Windows

## Problem

Generation via Ollama on Windows slows down significantly (often 5–10×) when the
system screen locks. This affects both CUDA (NVIDIA) and ROCm (AMD) backends and
persists until the user unlocks.

## Root Cause

Windows uses the **Windows Display Driver Model (WDDM)** to arbitrate all GPU
access. When the screen locks, Windows transitions to the **secure desktop**
(Winlogon desktop), which triggers several behaviors that starve background compute:

1. **GPU power-state drop** — WDDM signals the GPU driver to enter a lower P-state
   (e.g. P8 instead of P0) because no interactive display work is needed. This
   halves or quarters the GPU's memory bandwidth and shader clock, directly
   throttling matrix-multiply throughput during inference.

2. **CUDA/ROCm context preemption** — The GPU scheduler may preempt long-running
   compute kernels to service the lock-screen compositor. Each preemption flushes
   the SM pipeline and re-loads model weights from VRAM, adding 10–300 ms of
   overhead per token on large models.

3. **Background app CPU throttle** — Windows 10/11 (especially with "Power saver"
   or balanced plans) reduces the scheduling quantum for processes belonging to the
   locked user session. Ollama's CPU threads — used for prompt tokenisation, KV-cache
   management, and CPU-fallback layers — get less wall-clock time, causing the GPU
   to stall waiting for the next batch.

4. **TDR watchdog** — If a CUDA kernel runs longer than `TdrDelay` seconds
   (default: 2 s on older driver versions), the kernel is aborted and Ollama has to
   restart the request from scratch, causing sporadic multi-second pauses rather than
   uniform slowdown.

## Fixes (best first)

### Fix 1 — Run Ollama as a Windows Service (recommended)

Services run under the **SYSTEM** account, which is never "locked". The GPU stays
in its high-performance P-state and the CPU scheduler treats the service the same
whether the screen is locked or not.

Use [NSSM](https://nssm.cc/) (Non-Sucking Service Manager):

```powershell
# Run as Administrator
nssm install OllamaService "C:\Users\<YourUser>\AppData\Local\Programs\Ollama\ollama.exe"
nssm set OllamaService AppParameters "serve"
nssm set OllamaService AppEnvironmentExtra "OLLAMA_HOST=0.0.0.0:11434"
nssm start OllamaService
```

After this, stop running `ollama serve` manually — the service handles it.
The Ollama tray app can still be used to manage models; it will connect to the
service endpoint automatically.

### Fix 2 — Force GPU maximum performance via NVIDIA Control Panel

1. Open **NVIDIA Control Panel** → **Manage 3D Settings** → **Global Settings**.
2. Set **Power management mode** → **Prefer maximum performance**.
3. Apply and restart Ollama.

This prevents the driver from dropping the P-state on lock. It increases idle
power consumption but eliminates the slowdown entirely for NVIDIA GPUs.

For **AMD** GPUs, open **AMD Software: Adrenalin** → **Gaming** → **Graphics** →
**Advanced** → set **Power Tuning** to **Maximum Performance** (or pin the GPU
clock via the Manual tuning slider).

### Fix 3 — Use the High Performance or Ultimate Performance power plan

```powershell
# Activate Ultimate Performance (hidden by default)
powercfg -duplicatescheme e9a42b02-d5df-448d-aa00-03f14749eb61
# Then open Control Panel → Power Options and select it
```

This prevents Windows from throttling CPU scheduling for the locked session and
also influences GPU driver power policy on many systems.

### Fix 4 — Increase TDR delay (stops sporadic aborts)

If you see sudden multi-second pauses rather than uniform slowdown, TDR is aborting
kernels. Raise the threshold:

```
HKEY_LOCAL_MACHINE\System\CurrentControlSet\Control\GraphicsDrivers
  TdrDelay  REG_DWORD  60   (seconds; default is 2)
```

Reboot after editing. Note: this does not fix the P-state slowdown — it only stops
hard aborts on very long single kernels.

### Fix 5 — Disable Hardware-Accelerated GPU Scheduling (HAGS) if enabled

On some driver/GPU combinations HAGS introduces extra preemption latency for
compute workloads when the display is idle. To check and disable:

**Settings** → **System** → **Display** → **Graphics settings** →
toggle **Hardware-Accelerated GPU Scheduling** off → reboot.

Test with and without HAGS; some systems are faster with it on, others with it off.

## Summary

| Fix | Effort | Eliminates slowdown? |
|-----|--------|----------------------|
| Run as Windows Service | Medium | Yes (most reliable) |
| NVIDIA Max Performance mode | Low | Yes for NVIDIA |
| High/Ultimate Performance plan | Low | Partial |
| Raise TdrDelay | Low | Stops aborts only |
| Disable HAGS | Low | Sometimes |

The combination of **Fix 1 + Fix 2** (service + max performance GPU mode) is the
most robust solution and is recommended for any machine running Ollama unattended.
