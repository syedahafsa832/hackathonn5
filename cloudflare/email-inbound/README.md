# tResolv inbound email Worker

This Worker receives emails routed by Cloudflare Email Routing and posts the
original raw MIME message directly to tResolv. It does not forward mail to a
Zoho/Gmail mailbox and does not use Cloudflare Email Sending.

## Cost

- Cloudflare Email Routing supports Worker destinations on the Workers Free plan.
- The Worker uses one request per inbound email.
- No Cloudflare Email Sending plan is used.
- No extra mailbox is created.

Cloudflare's free Workers plan has usage limits. Check the current Cloudflare
limits before a production rollout.

## Configure

1. Add `tresolv.online` to Cloudflare DNS. The domain registration remains at
   Spaceship; only DNS nameservers change.
2. Copy and verify every existing DNS record before changing nameservers. Keep
   the root MX records for Zoho unchanged.
3. In Cloudflare Email Service > Email Routing, enable the subdomain
   `inbound.tresolv.online`.
4. Add a catch-all routing rule for that subdomain and choose this Worker as
   the destination.
5. Set `BACKEND_EMAIL_INGEST_URL` in `wrangler.toml` to the deployed tResolv
   API endpoint.
6. Set the same random secret in both environments:
   - Backend: `INBOUND_EMAIL_SHARED_SECRET`
   - Cloudflare: `wrangler secret put TRESOLV_INBOUND_SHARED_SECRET`
7. Deploy from this directory with Wrangler:
   `npx wrangler deploy`
8. Attach the deployed Worker to the catch-all Email Routing rule.

## Address format

The backend generates one opaque address per brand:

`<40-character-token>@inbound.tresolv.online`

The address is not a mailbox. Cloudflare's catch-all route invokes the Worker;
the Worker passes the envelope recipient to tResolv, where the token is mapped
to the owning brand.

## Merchant setup

The merchant creates a Gmail filter to forward support emails to the generated
address. Their normal support inbox remains their mailbox of record. Do not
change their MX records or ask them for their Gmail password.

## Important limitations for the first version

- No historical inbox import; forwarding starts with new messages.
- The first version extracts text/plain and text/html. Attachments are not
  stored or presented to Luna yet.
- Replies are sent through the merchant's connected Gmail account using the
  `gmail.send` scope. The backend uses RFC Message-ID/In-Reply-To/References
  headers for forwarded-message threading and does not pass a fabricated Gmail
  internal thread ID.
- Test automatic Gmail forwarding with a real test message. Verify that the
  original sender and Message-ID are preserved before enabling replies.
- A Worker/backend outage must be monitored. Do not claim delivery until the
  first end-to-end message and reply have passed.
