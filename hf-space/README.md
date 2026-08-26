---
title: Ripo Team Cloud PC
emoji: 🖥️
colorFrom: indigo
colorTo: blue
sdk: gradio
app_file: app_server_v2.py
python_version: 3.12
fullWidth: true
header: mini
suggested_hardware: zero-a10g
pinned: false
---

# Ripo Team Cloud PC

Ripo Team's browser-accessible Cloud PC, TikTok AI backend, prerecorded TikTok LIVE broadcaster, and streamed Aug-25-2021 Rec Room runtime.

This Space stays on the free Gradio/ZeroGPU-compatible SDK. Rec Room uses a SHA256-pinned portable Wine amd64-wow64 runtime so the 64-bit game and the official 32-bit Windows Steam client can run without Debian i386/multilib packages.

## Prerecorded TikTok LIVE

The server exposes a prerecorded LIVE control API and the GitHub Pages control center can upload videos in resumable 8 MB chunks, store a persistent video library, start a broadcast immediately, loop a video, or schedule a future start. Video is normalized with FFmpeg to a portrait 720x1280 H.264/AAC RTMP stream and sent through the existing TikTok LIVE Producer/browser credential path.

This feature does not bypass TikTok's LIVE eligibility or RTMP restrictions. The connected TikTok account must have valid LIVE access and the server must be able to obtain a current streaming destination. OAuth Login Kit alone does not grant permission to start arbitrary LIVE sessions.

Scheduled jobs require the server process to be running at the scheduled time. A sleeping/free-tier host cannot guarantee exact-time starts or uninterrupted 24/7 broadcasting.

TikTok Login Kit credentials remain configured through Hugging Face Variables and Secrets. Deployment verification checks the live server health and Rec Room runtime after rollout.
