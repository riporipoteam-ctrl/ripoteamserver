---
title: Ripo Team Cloud PC
emoji: 🖥️
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
fullWidth: true
header: mini
suggested_hardware: zero-a10g
pinned: false
---

# Ripo Team Cloud PC

Ripo Team's browser-accessible Cloud PC and TikTok AI backend.

The Space runs `app_server_v2:app` through Uvicorn in a Debian-based Docker image. The image enables both amd64 and i386 so the same Wine prefix can run the 64-bit Aug 2021 Rec Room client and the official 32-bit Windows Steam bootstrap required by Steamworks.

TikTok Login Kit credentials remain configured through Hugging Face Variables and Secrets. Deployment verification checks the server health, TikTok OAuth status, and Rec Room runtime after each Space rollout.
