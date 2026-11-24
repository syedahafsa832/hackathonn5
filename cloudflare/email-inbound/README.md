# tResolv inbound email Worker

This Worker receives emails from Cloudflare Email Routing and posts the
original raw MIME message directly to tResolv. It does not forward customer
emails to Hafsa's Zoho inbox and does not create or require a mailbox.

## Cost

- Cloudflare Email Routing supports Worker destinations on the Free plan.
- Workers Free includes a daily request allowance. Check current limits.
- No Cloudflare Email Sending plan is used.
- No Unipile/Nylas account, extra mailbox, or paid email-routing provider is used.

## Domain and DNS setup

The domain registration stays at Spaceship. You do NOT transfer ownership or
registration to Cloudflare. You change the authoritative nameservers at
Spaceship to the two Cloudflare nameservers assigned to your zone. Cloudflare
then hosts DNS.

**Do not change nameservers until the DNS inventory is complete.** Export or
screenshot every active DNS record at Spaceship and confirm Cloudflare's
import contains them. Explicitly check:

- Website A / AAAA / CNAME records and any www records.
- Zoho Mail MX records for the root domain.
- Zoho SPF, DKIM and DMARC TXT records.
- Domain verification TXT records and other product records.

Keep the root-domain Zoho MX records pointed at Zoho. Email routing is only
being enabled for inbound.tresolv.online, not for tresolv.online itself.
Do not delete the root Zoho MX/SPF/DKIM/DMARC records.

After the nameserver change has propagated:

1. Open Cloudflare Email Routing / Email Service for tresolv.online.
2. Enable routing for the subdomain inbound.tresolv.online using Cloudflare's
   subdomain email-routing setup.
3. Create a catch-all rule for the inbound subdomain and choose the deployed
   tresolv-email-inbound Worker as its destination.
4. Keep root email routing disabled so hello@tresolv.online continues to be
   hosted by Zoho.

Official docs:
- https://developers.cloudflare.com/email-service/
- https://developers.cloudflare.com/email-service/configuration/email-routing-addresses/
- https://developers.cloudflare.com/email-service/configuration/subdomains/
- https://developers.cloudflare.com/dns/zone-setups/full-setup/setup/

## Backend configuration

Run backend/migrations/064_email_forwarding.sql once in the Supabase SQL Editor before using these routes.

Set these environment variables on the deployed backend:

- INBOUND_EMAIL_DOMAIN=inbound.tresolv.online
- INBOUND_EMAIL_SHARED_SECRET=(random secret of at least 32 bytes)

Do not commit the secret. Use the exact same value for the Cloudflare Worker
secret below.

The backend endpoint is:

POST https://YOUR-API-HOST/api/email/inbound

## Worker configuration

1. Replace YOUR-API-HOST in wrangler.toml with the actual deployed API hostname.
2. From this directory, set the Worker secret:

   npx wrangler secret put TRESOLV_INBOUND_SHARED_SECRET

3. Paste the same secret used for INBOUND_EMAIL_SHARED_SECRET.
4. Deploy:

   npx wrangler deploy

5. In Cloudflare Email Routing, attach the deployed Worker to the catch-all
   rule for inbound.tresolv.online.

The Worker sends the original RFC822 bytes and the SMTP envelope recipient.
The backend maps the opaque recipient token to one brand.

## Gmail send-only OAuth project

The legacy Gmail OAuth project must remain untouched. Create a separate,
free Google Cloud project for this new path so its Data Access configuration
does not list the legacy gmail.modify scope.

In the new Google project:

1. Enable the Gmail API in the new Google Cloud project.
2. Configure the OAuth consent screen and publish the app as In production.
3. Configure the app's authorized domain, homepage, privacy policy and terms.
4. In Data Access, request only:
   - https://www.googleapis.com/auth/gmail.send
   - openid
   - email
5. Create a Web application OAuth client.
6. Add this exact authorized redirect URI:
   https://YOUR-API-HOST/api/brands/gmail/callback
7. Complete Google's normal sensitive-scope OAuth verification for gmail.send.
   This path does not request gmail.modify, gmail.readonly, or
   https://mail.google.com/, so it does not require the restricted-scope
   CASA security assessment. Google may still require a standard OAuth
   verification review. Do not treat an unverified 100-user app as ready for
   paying customers.

Set these backend environment variables:

- GMAIL_SEND_CLIENT_ID
- GMAIL_SEND_CLIENT_SECRET

Keep the existing GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET unchanged for the
legacy full-access Gmail integration.

## Merchant setup in the tResolv dashboard

1. Open Brands and expand the store.
2. Under Free email forwarding, create the private forwarding address.
3. In the merchant's Gmail settings, add that address under Forwarding.
4. Google sends a forwarding confirmation email to the Worker. Return to
   tResolv, refresh the verification code, and copy it into Gmail.
5. Click "I entered the code in Gmail" in tResolv.
6. Connect Gmail for sending only.
7. In Gmail filters, create a rule that forwards only the merchant's support
   mail to the tResolv address. Keep the merchant's normal inbox as the
   mailbox of record.

This does not import historical email. Forwarding begins with new messages.

## First self-test

Use an inbox you control before enabling automatic replies:

1. Send one new email from a separate test address to the merchant's support
   inbox.
2. Confirm a ticket appears in the correct tResolv store.
3. Confirm the sender and full text are correct.
4. Reply to the ticket from tResolv and verify the reply is sent from the
   merchant's connected Gmail address.
5. Reply to that email from the test customer account.
6. Confirm the follow-up appears on the same ticket rather than creating a
   second ticket.
7. Repeat with HTML-only content and with an attachment. The initial release
   should preserve ordinary message text; it does not yet ingest/store
   attachments.

## Known limits before production

- No historical inbox import.
- Attachments are not yet stored or surfaced to Luna.
- Gmail's private threadId is unavailable to forwarding. The integration
  uses RFC Message-ID, In-Reply-To and References; verify real Gmail and
  customer-mail-client behavior with the self-test above.
- This initial Worker delivers directly to the backend and does not yet use a
  durable queue. Monitor Cloudflare Email Routing activity and backend logs.
  Do not claim production-grade delivery recovery until a queue/replay path
  has been implemented and tested.
- The first version recognizes Google's forwarding-noreply@google.com and
  mail-noreply@google.com senders, a forwarding-confirmation subject, and an
  8-10 digit code. If Google changes its template, the verification code may
  not be surfaced until the parser is updated.

Cloudflare routing references:
- https://developers.cloudflare.com/email-service/configuration/email-routing-addresses/ (routing rules and plus-addressing)
- https://developers.cloudflare.com/email-service/configuration/subdomains/
