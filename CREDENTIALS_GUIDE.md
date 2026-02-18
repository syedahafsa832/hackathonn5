# Credentials Setup Guide: Customer Success Digital FTE

This guide will help you obtain the necessary credentials to make the Customer Success FTE project fully functional.

## 1. xAI API Key (Grok)

1. Go to the [xAI Console](https://console.x.ai/).
2. Sign in or create an account.
3. Navigate to the **API Keys** section.
4. Click **Create API Key**.
5. Copy the key and add it to `.env` as `XAI_API_KEY`.

## 2. Gmail API (Google Cloud)

To enable the agent to read and send emails:

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project named "Customer Success FTE".
3. Search for **Gmail API** and click **Enable**.
4. Go to **APIs & Services > OAuth consent screen**.
   - Select "External".
   - Fill in app name and email.
   - Add the `https://www.googleapis.com/auth/gmail.modify` scope.
5. Go to **APIs & Services > Credentials**.
   - Click **Create Credentials > OAuth client ID**.
   - Select **Web application**.
   - Add `http://localhost` as a redirect URI.
   - Copy the **Client ID** and **Client Secret** into `.env`.
6. **Get Refresh Token**: Use a tool like [OAuth 2.0 Playground](https://developers.google.com/oauthplayground/) or a script to authorize your app and get a refresh token. Add it as `GMAIL_REFRESH_TOKEN`.

## 3. Twilio (WhatsApp Sandbox)

1. Sign up for a free [Twilio Account](https://www.twilio.com/try-twilio).
2. Go to the **Twilio Console** and copy your **Account SID** and **Auth Token**.
3. Navigate to **Messaging > Try it out > Send a WhatsApp message**.
4. Follow instructions to join the Sandbox (e.g., send "join [word]" to the provided number).
5. Copy the Twilio sandbox phone number to `.env` as `TWILIO_WHATSAPP_NUMBER`.

## 4. Local Database

The system uses PostgreSQL. By default, the `docker-compose.yml` sets up a database on `localhost:5432` with user `postgres` and password `postgres`.
`DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/fte_db`

## 5. Kafka

Kafka is used for interior event streaming. No external credentials are needed as it runs locally via Docker.
`KAFKA_BOOTSTRAP_SERVERS=localhost:9092`
