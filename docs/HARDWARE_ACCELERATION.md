# Hardware Acceleration in m3u-proxy

This document explains how to use hardware-accelerated video processing in m3u-proxy using the integrated GPU detection and FFmpeg optimization.

## Overview

The m3u-proxy container now includes automatic hardware acceleration detection that:

- 🔍 **Auto-detects GPU hardware** (NVIDIA, Intel, AMD)
- ⚡ **Configures optimal FFmpeg settings** automatically
- 🚀 **Provides easy Python APIs** for hardware-accelerated transcoding
- 📊 **Falls back gracefully** to CPU when no GPU is available

## Hardware Support

### NVIDIA GPUs
- **Requirements**: NVIDIA Container Toolkit
- **Acceleration**: CUDA, NVENC, NVDEC
- **Best For**: High-performance transcoding with multiple concurrent streams
- **Docker Setup**: Use `deploy.resources.reservations.devices` with `driver: nvidia`

### Intel GPUs
- **Requirements**: `/dev/dri` devices passed to container
- **Acceleration**: VAAPI, QuickSync (QSV)
- **Best For**: Efficient transcoding with good quality/performance balance
- **Docker Setup**: Mount `/dev/dri:/dev/dri`

### AMD GPUs
- **Requirements**: `/dev/dri` devices passed to container
- **Acceleration**: VAAPI
- **Best For**: Open-source acceleration solution
- **Docker Setup**: Mount `/dev/dri:/dev/dri`

## Docker Setup Examples

### NVIDIA GPU (Recommended)

```yaml
# docker-compose.yml
services:
  m3u-proxy:
    image: m3u-proxy:latest
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    ports:
      - "8085:8085"
```

### Intel/AMD GPU

```yaml
# docker-compose.yml
services:
  m3u-proxy:
    image: m3u-proxy:latest
    devices:
      - /dev/dri:/dev/dri
    ports:
      - "8085:8085"
```

### Docker Run Commands

```bash
# NVIDIA GPU
docker run -d --name m3u-proxy \
  --gpus all \
  -p 8085:8085 \
  m3u-proxy:latest

# Intel/AMD GPU  
docker run -d --name m3u-proxy \
  --device /dev/dri:/dev/dri \
  -p 8085:8085 \
  m3u-proxy:latest

# CPU Only (no special setup needed)
docker run -d --name m3u-proxy \
  -p 8085:8085 \
  m3u-proxy:latest
```

## Using Hardware Acceleration in Code

### Import the Module

```python
from hwaccel import hw_accel, get_ffmpeg_hwaccel_args, is_hwaccel_available
```

### Check Hardware Availability

```python
if is_hwaccel_available():
    print(f"🚀 Hardware acceleration available: {hw_accel.get_type()}")
else:
    print("💻 Using CPU-only processing")
```

### Get FFmpeg Arguments

```python
# Basic hardware acceleration args
basic_args = hw_accel.get_basic_args()

# Transcoding with specific codec
h264_args = get_ffmpeg_hwaccel_args("h264")
h265_args = get_ffmpeg_hwaccel_args("h265")

# Build FFmpeg command
cmd = ["ffmpeg"] + h264_args + ["-i", "input.m3u8", "output.mp4"]
```

### Example: Stream Transcoding

```python
import asyncio
import subprocess
from hwaccel import get_ffmpeg_hwaccel_args, hw_accel

async def transcode_stream(input_url: str, output_path: str):
    # Get optimal hardware acceleration settings
    hwaccel_args = get_ffmpeg_hwaccel_args("h264")
    
    cmd = ["ffmpeg", "-y"] + hwaccel_args + [
        "-i", input_url,
        "-c:v", "h264_nvenc" if hw_accel.get_type() == "nvidia" else "h264_vaapi",
        "-preset", "fast",
        "-b:v", "2M",
        "-c:a", "aac",
        output_path
    ]
    
    process = await asyncio.create_subprocess_exec(*cmd)
    await process.communicate()
    return process.returncode == 0
```

## Environment Variables

The hardware detection script creates these environment variables:

- `HW_ACCEL_AVAILABLE`: `true` if hardware acceleration is available
- `HW_ACCEL_TYPE`: Type of acceleration (`nvidia`, `intel`, `amd`, `vaapi`, `cpu`)
- `HW_ACCEL_DEVICE`: Device identifier (`cuda`, `vaapi`, or empty)

These are also saved to `/tmp/hwaccel.env` for easy access.

## Startup Process

When the container starts, it automatically:

1. 🔍 **Detects available GPU hardware** using `lspci`
2. 🔧 **Checks device accessibility** (`/dev/nvidia*`, `/dev/dri/*`)
3. ⚡ **Tests FFmpeg capabilities** for detected hardware
4. 📊 **Generates optimal settings** and saves configuration
5. 🚀 **Starts your application** with hardware acceleration ready

## Performance Benefits

### NVIDIA GPUs
- **10-20x faster** encoding vs CPU
- **Multiple concurrent streams** (4+ simultaneously)
- **Lower CPU usage** (90%+ reduction)
- **Hardware-optimized presets** (fast, medium, slow)

### Intel/AMD GPUs  
- **3-8x faster** encoding vs CPU
- **Better quality/bitrate** efficiency
- **Lower power consumption**
- **Good for 1-2 concurrent streams**

### CPU Fallback
- **Universal compatibility**
- **High quality encoding** (slower)
- **Single stream recommended**
- **Uses software encoders** (libx264, libx265)

## Troubleshooting

### No GPU Detected
```bash
# Check if GPU devices are available
docker run --rm m3u-proxy:latest ls -la /dev/dri /dev/nvidia*

# Check hardware detection logs
docker logs <container-name> | grep "🔍"
```

### NVIDIA Issues
```bash
# Test NVIDIA container runtime
docker run --rm --gpus all nvidia/cuda:11.0-base nvidia-smi

# Check NVIDIA driver on host
nvidia-smi
```

### Intel/AMD Issues
```bash
# Check DRI devices on host
ls -la /dev/dri/

# Test VAAPI in container
docker run --rm --device /dev/dri:/dev/dri m3u-proxy:latest vainfo
```

## Container Logs

The container startup will show hardware detection results:

```
📦 m3u-proxy starting up...
🐍 Python version: Python 3.14.8
🎬 FFmpeg version: 8.0
🔍 Running hardware acceleration check...
🔍 Hardware detection: NVIDIA GPU (GeForce RTX 3080)
✅ FFmpeg NVIDIA acceleration: AVAILABLE
🔰 NVIDIA GPU: GeForce RTX 3080
✅ Hardware acceleration configuration loaded
🚀 Starting m3u-proxy application...
```

## API Integration

The hardware acceleration is designed to integrate seamlessly with your existing streaming proxy logic. Simply import the module and use the provided functions to get optimal FFmpeg arguments for any transcoding operations.

The system automatically handles:
- Hardware detection and capability testing
- Optimal encoder selection (NVENC, VAAPI, CPU)
- Fallback scenarios when hardware isn't available
- Performance tuning based on detected GPU type