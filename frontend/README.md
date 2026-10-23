# OpenEvolve Vue frontend

This is a standalone Vue 3 + Vite frontend. The Flask visualizer remains the API
server and continues to own task execution, task files, checkpoints, and feedback
queue operations.

## Local development

Start the Flask API in one terminal:

```bash
python scripts/visualizer.py --host 127.0.0.1 --port 8080
```

Install dependencies and start Vue in another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the Vite URL printed in the terminal (normally `http://127.0.0.1:5173`).
Vite proxies `/api` and `/manual/api` to Flask and removes the browser `Origin`
header on the local proxy connection, matching Flask's local-only API policy.
Set `VITE_BACKEND_ORIGIN` to change the proxy target.

## Production

Build with `npm run build`; generated static assets are written to `dist/`.
The current Flask API intentionally accepts local requests only. A deployment
outside local development needs a trusted reverse proxy that forwards API
requests to Flask over loopback and preserves that local-only boundary. Do not
expose the API directly to an untrusted network; add authentication and an
explicit origin policy before doing so.
