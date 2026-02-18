# Gmail Integration Setup Guide

This guide explains how to set up real Gmail integration for the Customer Success AI Agent system.

## Prerequisites

1. **Gmail Account** - You need a Gmail account to use as your support email address
2. **2-Factor Authentication** - Must be enabled on your Gmail account
3. **App Password** - Required for programmatic access to Gmail

## Step 1: Enable 2-Factor Authentication

1. Go to your [Google Account settings](https://myaccount.google.com/)
2. Navigate to "Security"
3. Under "Signing in to Google," select "2-Step Verification"
4. Follow the prompts to enable 2FA

## Step 2: Generate an App Password

1. In your Google Account, go to "Security"
2. Under "Signing in to Google," select "App passwords"
3. Select the app: "Mail"
4. Select the device: "Windows Computer" (or whatever is appropriate)
5. Click "Generate"
6. Copy the 16-character password that appears

**Important**: This app password will be used instead of your regular Gmail password for the application.

## Step 3: Configure Environment Variables

Add the following to your `.env` file:

```env
SUPPORT_EMAIL_ADDRESS=your_email@gmail.com
EMAIL_PASSWORD=abcd efgh ijkl mnop  # The 16-character app password you generated
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
EMAIL_POLL_INTERVAL=30  # Check for new emails every 30 seconds
```

## Step 4: Update Docker Compose (if using Docker)

The `docker-compose.yml` file should already include the email poller service with the necessary environment variables. Make sure your environment variables are properly set.

## Step 5: Configure Email Polling Interval

The system polls your Gmail inbox every 30 seconds by default. You can adjust this by changing the `EMAIL_POLL_INTERVAL` environment variable:

- Lower values: More frequent checking (e.g., 15 seconds)
- Higher values: Less frequent checking (e.g., 60 seconds)

## Step 6: Test the Integration

1. Make sure your support email is set up and receiving emails
2. Start the application services
3. Send a test email to your support address
4. Verify that:
   - The email is detected by the system
   - The AI processes the email
   - A response is sent back to the sender

## Troubleshooting

### Common Issues:

1. **Authentication Error**: Double-check your app password. Make sure you're not using your regular Gmail password.

2. **IMAP Disabled**: Ensure IMAP is enabled in your Gmail settings:
   - Go to Gmail
   - Click Settings (gear icon) > See all settings
   - Go to "Forwarding and POP/IMAP"
   - Enable "IMAP access"

3. **Firewall/Network Issues**: Ensure your server can connect to Gmail's SMTP/IMAP servers.

4. **Rate Limits**: Gmail has daily sending limits. Monitor your usage if sending many emails.

### Debugging Tips:

1. Check application logs for error messages
2. Verify your app password works by testing SMTP/IMAP access manually
3. Ensure your Gmail account allows "less secure apps" access (though app passwords are preferred)

## Security Considerations

1. **Never commit your app password** to version control
2. **Use environment variables** to store sensitive credentials
3. **Rotate your app password** periodically
4. **Monitor access logs** for unauthorized usage
5. **Use a dedicated support email** rather than your personal email

## Production Recommendations

1. **Use a branded email domain** (e.g., support@yourcompany.com) with Gmail Workspace
2. **Set up proper DNS records** for better email deliverability
3. **Monitor email bounce rates** and delivery status
4. **Implement proper error handling** for email delivery failures
5. **Consider rate limiting** to stay within Gmail's sending limits

## Environment Variables Reference

| Variable | Description | Default |
|----------|-------------|---------|
| SUPPORT_EMAIL_ADDRESS | The email address to monitor | syedahafsa832@gmail.com |
| EMAIL_PASSWORD | Gmail App Password | required |
| SMTP_SERVER | SMTP server address | smtp.gmail.com |
| SMTP_PORT | SMTP port | 587 |
| EMAIL_POLL_INTERVAL | Seconds between email checks | 30 |

## Service Architecture

The email integration consists of:

1. **Email Poller Service** - Continuously checks for new emails
2. **Gmail Handler** - Processes incoming emails and sends responses
3. **Message Processor** - Integrates emails into the AI processing pipeline
4. **Database Layer** - Stores email conversations in the unified customer history

This architecture ensures that email conversations are treated the same as WhatsApp and web form interactions, providing a unified customer experience across all channels.