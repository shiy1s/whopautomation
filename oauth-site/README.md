# Whop Automation OAuth site

This is a buildless static site for the Google OAuth homepage, privacy policy, and terms links.

## Deploy with Vercel

From the repository root, choose `source/oauth-site` as the Vercel project root. The site needs no build command and no environment variables. Vercel will serve:

- `/` — public application homepage
- `/privacy` — privacy policy
- `/terms` — terms of service

After deployment, use the final HTTPS URL in Google Cloud OAuth Branding. For production verification, connect a custom domain you control and verify that domain in Google Search Console. A temporary `vercel.app` URL is suitable for staging/testing only.

The site does not contain OAuth credentials, tokens, campaign media, or publishing controls.
