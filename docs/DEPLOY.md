# Deployment checklist

## 1. Hugging Face trusted publishing

Open the settings for Space `Echoxr/ripoteam-cloud-pc`.

Under **Trusted Publishers**, add **GitHub Actions** with these exact claims:

- Repository: `riporipoteam-ctrl/ripoteamserver`
- Branch: `main`
- Workflow: `sync-huggingface.yml`

This gives the workflow a short-lived, Space-scoped token through GitHub OIDC. No permanent `HF_TOKEN` GitHub secret is required.

In the same Space settings, add these secrets:

- `VNC_PASSWORD` — a strong password for the Linux desktop
- `ADMIN_TOKEN` — a separate long random value for Hermes and log controls

## 2. GitHub

In repository **Settings → Pages**, choose **GitHub Actions**.

The production Space URL is already configured as `https://echoxr-ripoteam-cloud-pc.hf.space`.

Run **Actions → Create or update Hugging Face Space → Run workflow**. The workflow uploads only the `hf-space/` folder. After it succeeds, **Verify Hugging Face Space** waits for the real `/api/health` endpoint automatically.

The Pages workflow deploys the `site/` folder.

## 3. Hermes

Use the GitHub Pages Hermes panel or the Space control panel to install Hermes.

Then open the Linux terminal and run:

```bash
source ~/.bashrc
hermes setup
```

Configure your model provider and Telegram/Discord credentials. Start the gateway:

```bash
hermes gateway
```

## 4. Cloudflare, optional

The Cloudflare Worker provides an always-reachable wake/proxy endpoint for health and control API requests.

Add GitHub secrets:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`

Run **Deploy optional Cloudflare proxy** and place the resulting Worker URL in a repository variable named `CLOUDFLARE_PROXY_URL`.

The noVNC desktop still connects directly to Hugging Face.

## Availability behavior

The GitHub Pages frontend remains available as a static site. When opened, it requests the Space health endpoint, waits while a sleeping or rebuilding Space starts, and reconnects to noVNC.

The verification workflow checks the Space after deployments. It does not schedule artificial traffic whose sole purpose is defeating Hugging Face's free-tier sleep policy. Hugging Face documents paid hardware as the supported route for indefinite execution.
