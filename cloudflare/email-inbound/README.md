# tResolv inbound email Worker

This Worker receives emails through Cloudflare Email Routing and posts the
original raw MIME message directly to the tResolv backend. It does not forward
customer mail to Hafsa's Zoho inbox and does not create or require a mailbox.

## Cost and limits

- Cloudflare Email Routing supports Worker destinations on the Workers Free plan.
- Inbound Email Routing has no per-month email quota in the published limits.
- Worker execution is still subject to Workers Free CPU/memory/request limits.
- No Cloudflare Email Sending, Unipile, Nylas, or paid mailbox is used.

## Domain and DNS setup

The domain registration stays at Spaceship. You do NOT transfer registration
or ownership to Cloudflare. You change the domain's authoritative nameservers
at Spaceship to the two Cloudflare nameservers assigned to your zone. Cloudflare
then hosts DNS.

**Do not change nameservers until the DNS inventory is complete.** Export or
screenshot every active DNS record at Spaceship and confirm Cloudflare's import
contains them. Explicitly check:

- Website A / AAAA / CNAME records and any www records.
- Zoho Mail MX records for the root domain.
- Zoho SPF, DKIM and DMARC TXT records.
- Domain verification TXT records and other product records.

Keep the root-domain Zoho MX records pointed at Zoho. Email Routing is enabled
only for inbound.tresolv.online, not for tresolv.online itself. Do not delete
the root Zoho MX/SPF/DKIM/DMARC records.

After the nameserver change has propagated:

1. Open Cloudflare Email Routing / Email Service for tresolv.online.
2. Enable Email Routing for the subdomain inbound.tresolv.online in the
   Email Routing Settings > Subdomains section.
3. Enable subaddressing in Email Routing Settings.
4. The Worker config declares one exact routing address:
   support@inbound.tresolv.online. Wrangler 4.113.0 or later creates and
   manages that rule during deployment and routes it to this Worker.
5. Do not create a catch-all rule. Cloudflare only provides catch-all routing
   at the apex domain, not on a subdomain.

Official docs:
- https://developers.cloudflare.com/email-service/
- https://developers.cloudflare.com/email-service/configuration/email-routing-addresses/
- https://developers.cloudflare.com/email-service/configuration/subdomains/
- https://developers.cloudflare.com/dns/zone-setups/full-setup/setup/

## Backend configuration

Run backend/migrations/064_email_forwarding.sql and backend/migrations/065_email_forwarding_verification_link.sql once in the Supabase SQL Editor.

Set these backend environment variables:

- INBOUND_EMAIL_DOMAIN=inbound.tresolv.online
- INBOUND_EMAIL_LOCAL_PART=support
- INBOUND_EMAIL_SHARED_SECRET=(random secret of at least 32 bytes)

Do not commit the secret. Use the same value for the Cloudflare Worker secret.

The backend endpoint is:

POST https://YOUR-API-HOST/api/email/inbound

## Worker configuration

1. Replace YOUR-API-HOST in wrangler.toml with the deployed tResolv API hostname.
2. Set the Worker secret:

   npx wrangler secret put TRESOLV_INBOUND_SHARED_SECRET

3. Paste the exact same value used for INBOUND_EMAIL_SHARED_SECRET.
4. Deploy from this directory:

   npx wrangler deploy

5. Wrangler reads the top-level addresses entry in wrangler.toml and creates
   the single support@inbound.tresolv.online rule. It does not create a
   catch-all rule.

The Worker forwards the original RFC822 message and the SMTP envelope recipient.
The backend extracts the plus-address token and maps it to the owning brand.

## Address format

Each brand gets an opaque address such as:

support+0123456789abcdef0123456789abcdef01234567@inbound.tresolv.online

There is only one Cloudflare rule: support@inbound.tresolv.online.
With subaddressing enabled, Cloudflare routes support+anything@inbound... to
that same rule and preserves the complete recipient in message.to. tResolv
extracts the 40-character token after the plus sign and resolves the brand.

The address is a routing alias, not a mailbox. No Zoho or Gmail destination
address is required.

## Gmail send-only OAuth project

Keep the existing Gmail OAuth project and its gmail.modify client untouched.
Create a separate, free Google Cloud project and OAuth client for this path so
the new project's Data Access configuration does not include the legacy
restricted scope.

In the new Google Cloud project:

1. Enable the Gmail API.
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
   https://mail.google.com/, so it avoids the restricted-scope CASA assessment.
   Google can still require standard OAuth verification. An unverified app
   requesting gmail.send remains subject to Google's warning/user-cap rules.

Set these backend environment variables:

- GMAIL_SEND_CLIENT_ID
- GMAIL_SEND_CLIENT_SECRET

Keep the existing GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET unchanged for the
legacy full-access Gmail integration.

## Merchant setup in tResolv

1. Open Brands and expand the store.
2. Under Free email forwarding, create the private forwarding address.
3. In the merchant's Gmail settings, add that address under Forwarding.
4. Google sends a forwarding confirmation email to the Worker. Return to
   tResolv and refresh the confirmation status. Depending on Google's email
   template, tResolv shows either the numeric code (enter it into Gmail) or a
   Google confirmation link (open it and complete Google's confirmation page).
5. Click the confirmation button in tResolv after completing Google's step.
6. Connect Gmail for sending only.
7. In Gmail filters, forward only the merchant's support mail to the tResolv
   address. Keep the merchant's normal inbox as the mailbox of record.

Forwarding starts with new messages. There is no historical inbox import.

## First self-test

Use an inbox you control before enabling automatic replies:

1. Send an email from a separate test address to the merchant's support inbox.
2. Confirm a ticket appears in the correct tResolv store.
3. Confirm the original sender and message body are correct.
4. Reply to the ticket in tResolv and verify it is sent from the merchant's
   connected Gmail account.
5. Reply to that message from the test customer account.
6. Confirm the follow-up is appended to the same tResolv ticket.
7. Repeat with HTML-only content and an attachment. The initial release does
   not yet store or surface attachments to Luna.

## Known limits before production

- No historical inbox import.
- Attachments are not yet stored or surfaced to Luna.
- Forwarding does not provide Gmail's private threadId. Conversation continuity
  uses RFC Message-ID, In-Reply-To and References headers. Verify Gmail and
  customer-mail-client behavior with the self-test above.
- The initial Worker delivers directly to the backend and has no durable queue.
  Monitor Cloudflare Email Routing activity and backend logs. Do not claim
  production-grade delivery recovery until queue/replay behavior is implemented
  and tested.
- The verification parser recognizes Google's known forwarding confirmation
  senders and 8-10 digit code pattern. If Google changes its email template,
  the code may not appear until the parser is updated.
