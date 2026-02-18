# Render Deployment Configuration Guide

To deploy your Customer Success AI FTE to Render, use the following settings. We have optimized the system to run as a "Monolith," meaning the API and all background workers (Gmail, AI Processor) run in a single process.

## 1. Backend: Web Service (API + Workers)

This service handles everything: FastAPI, WhatsApp webhooks, Gmail polling, and AI processing.

| Field                              | Value                      |
| ---------------------------------- | -------------------------- |
| **Service Type**                   | **Web Service**            |
| **Name**                           | `customer-success-backend` |
| **Root Directory**                 | `backend`                  |
| **Runtime**                        | `Docker`                   |
| **Dockerfile Path**                | `Dockerfile`               |
| **Docker Build Context Directory** | `.`                        |
| **Health Check Path**              | `/health`                  |

> [!TIP]
> **Environment Variables**: Make sure to add all variables from [.env.example](file:///e:/hackathon5/hack5/.env.example). Render will automatically inject these into your container.

---

## 2. Frontend: Static Site (Web Form)

This is your customer-facing contact form.

| Field                 | Value                          |
| --------------------- | ------------------------------ |
| **Service Type**      | **Static Site**                |
| **Name**              | `customer-success-frontend`    |
| **Root Directory**    | `web-form`                     |
| **Build Command**     | `npm install && npm run build` |
| **Publish Directory** | `build`                        |

### **Static Site Environment Variables**

You **MUST** add the following environment variable to the Static Site in Render so it can talk to your backend:

- `REACT_APP_API_BASE_URL`: The URL of your **Backend Web Service** (e.g., `https://customer-success-backend.onrender.com`)

---

## 3. Deployment Steps

1. **Deploy Backend**: Create the Web Service first and wait for it to be "Live". Copy its URL.
2. **Deploy Frontend**: Create the Static Site. In the **Environment** tab, add `REACT_APP_API_BASE_URL` with the URL you copied from the backend.
3. **Verify**: Open your frontend URL, submit a test ticket, and check the Render dashboard logs of the backend to see the AI and Gmail poller starting up.

> [!NOTE]
> The system now automatically starts the Gmail Poller and Message Processor internally when the FastAPI app starts. You do NOT need separate background workers.
