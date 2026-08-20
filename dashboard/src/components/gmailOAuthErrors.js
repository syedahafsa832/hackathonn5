// Specific, human copy for every Gmail-connection error code the backend can
// send back via the `gmail_error` redirect param (brand_gmail.py's
// /gmail/callback) or a direct API failure. Shared between Settings' Email
// tab and Onboarding's "Connect inbox" step so the wording never drifts
// between the two places a merchant can trigger this OAuth flow — mirrors
// the existing SHOPIFY_OAUTH_ERROR_MESSAGES pattern in Settings.jsx.
//
// Deliberately never a bare "Something went wrong" — each message says what
// happened and what to do next.
export const GMAIL_OAUTH_ERROR_MESSAGES = {
  // Google's own code when the user clicks "Cancel" / denies consent.
  access_denied:
    "You closed or cancelled the Google sign-in before finishing. Nothing was connected — click Connect Gmail whenever you're ready to try again.",
  // The signed OAuth state token (10-minute window) expired, or the link was
  // already used — e.g. the tab sat open too long, or the back button was
  // used after finishing once already.
  invalid_or_expired_state:
    "That connection link expired (Google sign-in windows are only valid for 10 minutes) or was already used. Click Connect Gmail to get a fresh one.",
  // Google's redirect back to tResolv was missing the code/state it needs —
  // usually means the sign-in was interrupted partway through.
  missing_params:
    "Google didn't send back the information tResolv needed to finish connecting. This can happen if the sign-in was interrupted — try again.",
  // Token exchange succeeded but the granted scopes didn't match what was
  // requested.
  scope_mismatch:
    "Google granted different permissions than tResolv asked for, so the connection couldn't be completed. Try again and approve the permissions on Google's screen.",
  // Catch-all for a failure on tResolv's/Google's side during token exchange
  // (network issue, Google API error, or a Google Workspace admin blocking
  // third-party apps for the domain).
  connection_failed:
    "tResolv couldn't complete the connection on Google's end. This is usually temporary — try again in a moment. If your inbox is managed by a Google Workspace admin, they may need to approve this app first.",
};

export function gmailOAuthErrorMessage(code) {
  return GMAIL_OAUTH_ERROR_MESSAGES[code] || GMAIL_OAUTH_ERROR_MESSAGES.connection_failed;
}
