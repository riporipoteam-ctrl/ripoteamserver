# Deployment checklist

## 1. GitHub

The repository is `riporipoteam-ctrl/ripoteamserver`.

In **Settings → Pages**, choose **GitHub Actions**.

Add repository variables:

- `HF_SPACE_REPO` = `Echoxr/ripoteam-cloud-pc`
- `HF_SPACE_URL` = `https://echoxr-ripoteam-cloud-pc.hf.space`
- `CLOUDFLARE_PROXY_URL` = optional Worker URL

Add repository secrets:

- `HF_TOKEN` = Hugging Face write token
- `HF_VNC_PASSWORD` = strong VNC password
- `HF_ADMIN_TOKEN` = long random admin token

Run **Create or update Hugging Face Space**. After it finishes, **Verify Hugging Face Space** checks the real `/api/health` endpoint automatically.

## 2. Hugging Face

The workflow uploads `hf-space/` to the public Gradio Space `Echoxr/ripoteam-cloud-pc`.

Set or verify Space secrets:

- `VNC_PASSWORD`
- `ADMIN_TOKEN`

Wait for the build to finish, then use the GitHub Pages dashboard or open the noVNC desktop path from the Space control page.

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

Run **Deploy optional Cloudflare proxy** and place the resulting URL in the `CLOUDFLARE_PROXY_URL` GitHub variable.

The noVNC desktop still connects directly to Hugging Face.

## Availability behavior

The GitHub Pages frontend remains available as a static site. When opened, it requests the Space health endpoint, waits while a sleeping or rebuilding Space starts, and reconnects to noVNC.

The verification workflow checks the Space after deployments. It does not schedule artificial traffic whose sole purpose is defeating Hugging Face's free-tier sleep policy.
