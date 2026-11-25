export interface Env {
  /** Full tResolv endpoint, e.g. https://api.tresolv.online/api/email/inbound */
  BACKEND_EMAIL_INGEST_URL: string;
  /** Set with: wrangler secret put TRESOLV_INBOUND_SHARED_SECRET */
  TRESOLV_INBOUND_SHARED_SECRET: string;
}

interface ForwardableEmailMessage {
  readonly from: string;
  readonly to: string;
  readonly raw: ReadableStream<Uint8Array>;
  readonly rawSize: number;
  setReject(reason: string): void;
}

const MAX_INBOUND_BYTES = 25 * 1024 * 1024;

export default {
  async email(message: ForwardableEmailMessage, env: Env): Promise<void> {
    if (!env.BACKEND_EMAIL_INGEST_URL || !env.TRESOLV_INBOUND_SHARED_SECRET) {
      // Fail closed. Do not silently accept and discard merchant support mail.
      message.setReject("tResolv inbound email is not configured");
      return;
    }

    if (message.rawSize > MAX_INBOUND_BYTES) {
      message.setReject("Email exceeds the tResolv inbound size limit");
      return;
    }

    try {
      // Preserve the original RFC 5322/MIME message. The backend parses it
      // with Python's standard email parser and does not need a mailbox.
      const raw = await new Response(message.raw).arrayBuffer();
      const response = await fetch(env.BACKEND_EMAIL_INGEST_URL, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env.TRESOLV_INBOUND_SHARED_SECRET}`,
          "Content-Type": "message/rfc822",
          "X-Tresolv-Envelope-To": message.to,
          "X-Tresolv-Envelope-From": message.from,
        },
        body: raw,
      });

      if (response.ok) {
        return;
      }

      const detail = (await response.text()).slice(0, 300);
      // Permanent client errors mean the recipient is unknown, disabled, or
      // malformed. Reject those clearly. Transient server/rate-limit errors
      // are thrown so Cloudflare records a failed Worker invocation rather
      // than treating the message as successfully processed.
      if (response.status >= 400 && response.status < 500 &&
          response.status !== 408 && response.status !== 429) {
        message.setReject(`tResolv could not accept this email (HTTP ${response.status})`);
        console.error("tResolv rejected inbound email", response.status, detail);
        return;
      }

      throw new Error(`tResolv inbound endpoint returned HTTP ${response.status}: ${detail}`);
    } catch (error) {
      console.error("Inbound email delivery to tResolv failed", {
        from: message.from,
        to: message.to,
        error: error instanceof Error ? error.message : String(error),
      });
      // Do not convert transient failures into a successful delivery.
      throw error;
    }
  },
};
