# Cloudflare wake/proxy layer

This Worker gives the GitHub Pages frontend an always-available endpoint. It proxies real requests to the Hugging Face Space, which naturally wakes a sleeping Space when a user opens the Cloud PC.

It does not send artificial scheduled traffic.

1. Set `SPACE_ORIGIN` in `wrangler.jsonc` to the `hf.space` origin.
2. Set `ALLOWED_ORIGIN` to your exact GitHub Pages origin.
3. Run `npm install` and `npm run deploy`.
4. Put the resulting Worker URL in `site/config.js` as `cloudflareProxyUrl`.

The noVNC iframe connects directly to Hugging Face by default. The Worker is primarily used for health checks and authenticated control API requests.
