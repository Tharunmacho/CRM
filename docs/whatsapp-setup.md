# WhatsApp setup and go-live guide

A step-by-step guide for connecting this CRM to WhatsApp through Meta's **WhatsApp Cloud API**. It is written for the person who will do the setup, not for a developer. Allow half a day the first time; business verification and template approval are done by Meta and can take longer.

Throughout this guide, anything in capitals and angle brackets (for example `<YOUR_CRM_DOMAIN>`) is a placeholder. **Never write real tokens or secrets into this document, chat, email or git.**

Related reading: [Operations: WhatsApp Business](operations.md#whatsapp-business), [Communications and split intake](communications-and-split-intake.md), [WhatsApp workflow](workflows/09-whatsapp.md).

## 1. What the integration does and does not do

**It does:**

| Feature | Behaviour |
| --- | --- |
| Candidate intake | A person (or an agent) sends a CV to the business number as a **photo, PDF or Word document**. The CRM downloads the original, reads it, and creates or enriches a candidate. Limit: 25 MB per file. |
| Review and resolve | Files that cannot be read, or that match more than one candidate, go to review. An admin can **Retry**, or **Resolve** by linking the message to a candidate with a written reason. Multi-person PDFs are split in review. |
| Chat from the CRM | **WhatsApp > Conversations**: send text and attachments to a contact, see sent / delivered / read / failed. Admin and Manager can send; a Staff user can send only for the linked candidate they currently own. |
| Templates | Approved message templates are synced from Meta and can be sent when the 24-hour window has closed. |
| Automatic acknowledgements | Optional (off by default, enabled per channel by an admin after previewing the text). Sends a thank-you after a CV is received. Can use an approved template, with plain text as the fallback. |
| 24-hour window | WhatsApp only allows free-form messages for 24 hours after the contact last wrote to you. After that, only an approved template can be sent. |

**It does not:**

- Read **WhatsApp groups**. Only direct one-to-one messages to the business number reach the CRM.
- Hold a **bot conversation**. It does not ask questions or answer candidates by itself. The only automatic message is the optional acknowledgement.
- Send an automatic acknowledgement to **internal staff numbers** (so test files from staff are not thanked), and it sends **at most one acknowledgement per sender number every 24 hours** (an agent who sends six CVs gets one thank-you, not six).
- Treat the sender's number as the candidate's identity. A sender may be an agent sending many people; the candidate is identified from the document (phone, email, passport).
- Store messages that arrive while the channel is switched off or disconnected (see section 7).

## 2. Meta side: step by step

Do this in a browser, signed in with the personal Facebook account of the person who owns the business. Meta's screens change often; if a label differs slightly, look for the nearest equivalent.

### 2.1 Developer account and business portfolio

1. Go to developers.facebook.com and register as a developer (verify email and phone).
2. Make sure the company has a **Meta Business portfolio** (business.facebook.com). Note who its admins are.

### 2.2 Create the app

1. In the developer dashboard choose **Create app**.
2. For the use case choose **Connect with customers through WhatsApp**.
3. Name the app, attach it to your business portfolio, and create it.
4. Open **App settings > Basic** and note the **App ID**, and keep the page open: you will need the **App secret** (click **Show**) and will add the privacy policy URL here later.

### 2.3 WhatsApp Business Account and the phone number

1. In the app, open **WhatsApp > API Setup**. Create or choose the **WhatsApp Business Account (WABA)**.
2. Add the business phone number.
   - **The number must not be registered in the WhatsApp or WhatsApp Business mobile app.** A number can be used for the Cloud API or for the phone app, not both. If it is, delete that WhatsApp account from the phone first (this loses its chat history on the phone).
   - It must be able to receive an SMS or a voice call for the verification code.
3. Choose the **display name** (it should match your business name; Meta reviews it).
4. Enter the verification code. The number now shows as connected.
5. Meta also gives a free **test number** on this page. It can only message numbers you add to its allowed list, and is useful for a first try. Do not mix it up with your real number when you fill in the Phone number ID below.

### 2.4 Business verification

For a real go-live (messaging anyone, higher limits, a trusted display name) Meta requires **business verification**: Business Settings > Security Center > Start verification. Have company registration documents and a matching address, phone or website ready. It can take several days.

### 2.5 System user and permanent token

A normal token from the API Setup page expires within about 24 hours. For production use a **system user** token that does not expire.

1. Business Settings > Users > **System users** > Add. Name it (for example "CRM WhatsApp") and give it the **Admin** role (so it can hold full access to the assets below).
2. Select the system user, **Add assets**:
   - **Apps**: your CRM app, with full control.
   - **WhatsApp accounts**: your WhatsApp Business Account, with full control.
3. **Generate token**: choose your app, set **Token expiration: Never**, and tick the two permissions:
   - `whatsapp_business_messaging`
   - `whatsapp_business_management`
4. Copy the token **once** and put it straight into the server settings (section 3). Meta will not show it again. This is your `WHATSAPP_ACCESS_TOKEN`.

### 2.6 Where to find each value

| Value | Where it is |
| --- | --- |
| **Phone number ID** | App > WhatsApp > **API Setup** > under "From", the **Phone number ID**. Digits only. It is **not** the phone number itself. Make sure it is the ID of your real number, not the test number. |
| **WhatsApp Business Account ID** | The same page, "WhatsApp Business Account ID" (also in WhatsApp Manager). Digits only. Needed for templates. |
| **App secret** | App settings > **Basic** > App secret > Show. Used to check that webhook calls really come from Meta. |
| **Verify token** | You make it up. Any long random string, for example from `openssl rand -hex 32` or a password manager (at least 32 characters, letters and digits). You type the same value in the CRM server settings and in Meta's webhook form. |

### 2.7 Privacy policy, category, and going Live

1. App settings > Basic: enter your **Privacy policy URL** (a public page describing how you handle candidate data; required to go Live) and choose an **App category** (for example "Business and pages").
2. Switch the app mode from **Development** to **Live** (toggle at the top of the dashboard). While the app is in Development mode, Meta sends webhooks only for test activity, so real candidate messages never arrive.

### 2.8 Webhook (do this later, in step 5 of the safe order in section 3)

In App > WhatsApp > **Configuration** > Webhook > **Edit**:

- **Callback URL**: `https://<YOUR_CRM_DOMAIN>/api/whatsapp/webhook`
- **Verify token**: the same value as `WHATSAPP_VERIFY_TOKEN`.
- Click **Verify and save**, then under **Webhook fields** subscribe to **`messages`**. That one field carries both incoming messages and delivery statuses.

**If you already run another WhatsApp bot on the same Meta app or WABA:** Meta lets you set the callback URL per phone number, which overrides the app-level one. Use that for the CRM's number (configured through the Graph API on the phone number, with `webhook_configuration.override_callback_uri` and the verify token), so the existing bot's webhook is not replaced. Do not edit the app-level Callback URL in that case. Ask your developer to do the override call; keep the verify token and access token out of chat when doing so.

## 3. Server settings

The CRM reads seven variables. Only the backend reads them.

| Variable | Value | Secret? |
| --- | --- | --- |
| `WHATSAPP_ENABLED` | `false` (default) or `true`. Master switch. | no |
| `WHATSAPP_PHONE_NUMBER_ID` | The Phone number ID (digits). | no |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | The WhatsApp Business Account ID (digits). Needed only for templates; without it chat still works but templates cannot be loaded or sent. | no |
| `WHATSAPP_GRAPH_VERSION` | Format `vN.N`. Built-in default is `v24.0`; leave it unless Meta asks you to change. | no |
| `WHATSAPP_ACCESS_TOKEN` | The permanent system-user token. | **yes** |
| `WHATSAPP_APP_SECRET` | The Meta App secret. | **yes** |
| `WHATSAPP_VERIFY_TOKEN` | Your random string. | **yes** |

**Where to put them**

- **Production (Dokploy):** the backend service's **Environment** tab.
- **Local development:** `backend/.env` (start from `backend/.env.example`).
- The **repository-root `.env` is not read by the backend.** Values placed there have no effect.

**Safe order (follow it exactly)**

1. Deploy the current version with `WHATSAPP_ENABLED=false`.
2. Set the other six values on the server. Redeploy.
3. Set `WHATSAPP_ENABLED=true`. Redeploy. (The webhook handshake is refused while the switch is off, so this must come before the next step.)
4. In Meta, enter the callback URL and verify token, **Verify and save**, subscribe to `messages` (section 2.8).
5. In the CRM, open **WhatsApp** as an admin and **Connect** the channel. Messages are stored and processed only after this.

If the switch is on but a required value is missing or the version is malformed, the CRM still starts, but the WhatsApp worker does not. See section 6.

## 4. Go-live acceptance checklist

Use your own phone and a few test CVs first. Tick each box before announcing the number to agents or candidates.

- [ ] The channel shows **connected** with your display number, and the WhatsApp screen lists no missing settings.
- [ ] Send a CV as a **photo** to the business number. A receipt appears with sender, time and the original file; it becomes a candidate (or lands in review with a clear reason).
- [ ] Send a CV as a **PDF**. Same result.
- [ ] Send a CV as a **Word** document (.doc or .docx). Same result.
- [ ] Send the **same file again**. There is still only one candidate and no second task; both receipts are kept.
- [ ] Send a message from the CRM: **Conversations**, text to your own number. It moves through sent, delivered and read (the history updates from Meta's callbacks).
- [ ] Reply from your phone, then answer from the CRM **inside 24 hours**: free text works.
- [ ] After the window has closed (or with a number that has not written for over 24 hours), the CRM asks for an **approved template**; send one and confirm delivery.
- [ ] If you enabled acknowledgements: a CV from a non-staff number gets one thank-you; a staff number does not; a second CV within 24 hours does not get another.
- [ ] **Disconnect** the channel, send a CV from your phone: it is not stored. **Reconnect** and send again: it is stored. Earlier receipts and originals stayed available throughout.
- [ ] A file that cannot be read (a blurry photo) goes to review, and **Retry** or **Resolve** works.

## 5. Templates

Templates are the only way to start a conversation or write after the 24-hour window.

1. In Meta **WhatsApp Manager > Message templates**, create the template (category **Utility** for ordinary notices; **Marketing** is costlier and stricter), write the body with numbered placeholders such as `{{1}}`, and submit it. Wait for status **Approved**.
2. In the CRM open **WhatsApp > Conversations** and click **Sync templates** (admin). Needs `WHATSAPP_BUSINESS_ACCOUNT_ID`.
3. Pick the template, fill each parameter, review the preview and send. Supported templates are text templates with positional parameters, with optional static content. Media or dynamic-button templates are shown as unavailable.

**Parameter rules (Meta rejects these):**

- No **line breaks** and no **tab characters** inside a value.
- No **four or more spaces in a row**.
- Every placeholder must be filled; the CRM caps each value at 1024 characters.

A template that is not approved, is paused, or was deleted cannot be sent (sync again and pick an approved one). The same template list can be chosen for the automatic acknowledgement under the channel settings.

## 6. Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| Meta says **webhook verification failed** (the CRM answered **403**) | One of: `WHATSAPP_ENABLED` is not `true` (the most common: redeploy after switching it on); `WHATSAPP_VERIFY_TOKEN` is empty on the server; the token typed in Meta differs from the server value by even one character or space; the request was not a subscribe handshake. Also check the URL is exactly `https://<YOUR_CRM_DOMAIN>/api/whatsapp/webhook` and reachable over HTTPS. |
| Webhook calls answered **401** "signature is invalid" | `WHATSAPP_APP_SECRET` is wrong, or belongs to a different app than the one sending. Copy it again from App settings > Basic. Calls not from Meta are rightly refused. |
| Webhook calls answered **503** "WhatsApp is not set up on the server yet" | `WHATSAPP_PHONE_NUMBER_ID` or `WHATSAPP_APP_SECRET` is missing. Set them and redeploy. |
| **413** on the webhook | Body over 4 MiB. Not normal for Meta; investigate what is calling it. |
| WhatsApp worker does not start | Check the backend log (stderr) for `[crm] WhatsApp is enabled but incomplete (NAMES...); its worker is not started. The API keeps running.` The listed names (never the values) are the settings that are missing or malformed; the required ones are `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_GRAPH_VERSION` (format `vN.N`), `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`. Fix and redeploy. The WhatsApp screen shows the same list to an admin, and **Connect** is refused until it is empty. |
| Messages arrive in Meta but **nothing happens** in the CRM | Check in order: is `WHATSAPP_ENABLED=true`; is the channel **connected** in the CRM (disconnected means received but not stored); is the webhook subscribed to **`messages`**; is the app **Live**; does the Phone number ID on the server match the number that received the message (a test number ID is a common mistake); are **automatic import** and the worker running (message status stays `received` or `pending`); does the message have a photo/PDF/Word file (plain text and groups never make candidates). |
| A file lands in **review** | Unsupported type (only PDF, Word, JPEG, PNG, WebP are read for candidates), over 25 MB, damaged download, or ambiguous match. Use Retry or Resolve. |
| **131047** | The 24-hour window has closed. Send an approved template instead. |
| **131030** | The recipient is not on the allowed list of the **test** number. Add them in Meta, or use the live number. |
| **132xxx** (132000 to 132016) | Template problem: not approved, paused, wrong language, or wrong number of parameters. Sync templates and choose an approved one. |
| **132018** | A template value has a line break, tab, or four spaces in a row. Remove them. |
| **190** | The access token has expired or is invalid. Create a new system-user token (expiry Never), update `WHATSAPP_ACCESS_TOKEN`, redeploy. |
| **133010** | The business number is not registered. Register it in Meta (WhatsApp Manager > Phone numbers). |
| **130429** (also 80007, 131056, 429) | Sending too fast. Nothing was sent; wait a few minutes and send again. |
| **131026** | WhatsApp could not deliver: the number is wrong or not on WhatsApp. |
| "We could not tell if WhatsApp sent this message" | The connection dropped mid-send. The CRM never re-sends these automatically; wait for the delivery status before sending again. |

The CRM shows the plain-language version of these codes in the message history, with the code in brackets for support. Meta's own error text is never stored.

## 6a. Delivery rules built into the CRM

| Rule | What it means for you |
| --- | --- |
| Reply window | A free-form message is accepted and sent only until 5 minutes before the 24 hours end. After that, use an approved template. |
| Retries | Rate-limit answers (HTTP 429, Meta codes 130429, 80007, 131056) and a failed connection before anything was sent are retried twice (after 1 and 3 seconds). A timeout or a server error is never retried: the message may have been sent, so the status stays "unknown" until Meta reports it. |
| Old queued messages | A template or automatic reply that waited more than one hour in the queue (for example while the channel was disconnected) is cancelled with a reason, never sent late. |
| Acknowledgements | Staff numbers are never acknowledged. Every other number gets at most one automatic reply in 24 hours. A reply that was paused, failed or cancelled does not count. |
| Linking a chat | A chat is linked automatically only to the candidate whose own phone or WhatsApp number it is, and never when another candidate has the same number. Agents who forward many CVs stay unlinked for a supervisor to decide. |
| Webhook size and errors | Signed bodies up to 4 MiB are accepted. A malformed item is skipped and the rest kept (still answered 200). A transient database error returns a 5xx so Meta sends the delivery again. |
| Worker crashes | The WhatsApp worker is restarted automatically if it crashes. After repeated crashes the supervisor stops the whole service, so watch the logs. |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | Not needed to start the worker. Without it, templates cannot be synced or sent and the CRM says so. |
| `WHATSAPP_GRAPH_VERSION` | An empty value means the default `v24.0`. |

## 6b. Numbers to ignore (staff, agents, clients, spam)

Staff, agents, clients and spammers share the business number with candidates, and every image or PDF a number sends is read as a possible CV. An administrator (Admin or MD) can list numbers the CRM must never work on: **WhatsApp > Number settings > Numbers to ignore**.

| Behaviour | Detail |
| --- | --- |
| A file from a listed number | Kept as a receipt with the status **Skipped** and the note "Sender is on the ignore list". It is never downloaded for reading and never becomes a candidate. |
| A file that arrived before the number was added | The background reader skips it too (it is marked Skipped when it reaches it). |
| Automatic replies | A listed number is never acknowledged. |
| Writing to them yourself | Still possible from the chat: the list only governs what arrives. |
| Number formats | `+91 98765 43210`, `919876543210`, `09876543210` and `9876543210` are the same number. A number from another country keeps its country code and is never mixed up with an Indian one. |
| Removing a number | Future files are read again. Files already skipped stay skipped (use **Link** or **Try again** on the message if one is wanted). |

The list needs migration `0059_whatsapp_ignored_numbers` (see `docs/operations.md`). Every addition and removal is recorded in the audit log.

## 7. Safety notes

- **Tokens and secrets** (`WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`) belong only in the server environment. Never in git, screenshots, chat or email, and never in this documentation. The `.env.example` files hold blanks only.
- **If a secret may have been exposed**, rotate it now: generate a new system-user token and revoke the old one; reset the App secret in Meta; choose a new verify token and update both sides. Then redeploy.
- **While the channel is disabled or disconnected**, incoming messages are acknowledged to Meta (so it does not keep retrying) but are **not stored**. Candidates who write during that time must send again. Plan switch-over times accordingly.
- **Test with your own number first**, and with the test number before the live one if you like. Do not announce the number to agents until section 4 is complete.
- Keep the Meta system user's access limited to this app and WABA. Remove people from the Meta business portfolio when they leave.
- Candidate documents are personal data. Keep the privacy policy URL accurate and limit who in the CRM can read WhatsApp history.
