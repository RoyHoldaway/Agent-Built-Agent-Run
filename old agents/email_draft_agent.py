import base64
import os
import re
import time
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

import pandas as pd

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(r"C:\Users\crazy\Documents\agents")

INPUT_FILE = (
    BASE_DIR
    / "processing leads"
    / "businesses_outreach_ready.xlsx"
)

PROCESSING_DIR = (
    BASE_DIR
    / "processing leads txt files"
)

# Automatically finds the Google OAuth client-secret JSON.
CREDENTIALS_FILE = None

TOKEN_FILE = BASE_DIR / "gmail_token.json"

CHECKPOINT_FILE = (
    BASE_DIR
    / "processing leads"
    / "email_draft_checkpoint.xlsx"
)

# None = process all remaining eligible leads.
MAX_DRAFTS_PER_RUN = None

# Update existing Gmail drafts with the new signature.
UPDATE_EXISTING_DRAFTS = True

REQUEST_DELAY = 0.5

VALID_QUALIFICATIONS = {
    "STRONG",
    "GOOD",
}


# ============================================================
# SIGNATURE
# ============================================================

SIGNATURE = """Roy Holdaway
Web Developer"""


# ============================================================
# GMAIL PERMISSION
# ============================================================

# Allows creating and modifying Gmail drafts.
#
# NO SEND PERMISSION IS USED.
#
SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose"
]


# ============================================================
# HELPERS
# ============================================================

def clean(value):
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass

    return str(value).strip()


def valid_email(email):
    email = clean(email)

    if not email:
        return False

    return bool(
        re.match(
            r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$",
            email
        )
    )


def add_signature(body):
    """
    Add:

        Roy Holdaway
        Web Developer

    to the end of the email.

    Prevents duplicate signatures.

    Also replaces common [Your Name] placeholders.
    """

    body = clean(body)

    if not body:
        return SIGNATURE

    # --------------------------------------------------------
    # Replace [Your Name] placeholders if the generated email
    # contains one.
    # --------------------------------------------------------

    body = re.sub(
        r"\[\s*your\s+name\s*\]",
        "Roy Holdaway",
        body,
        flags=re.IGNORECASE
    )

    # --------------------------------------------------------
    # If the signature is already present at the end,
    # don't add another one.
    # --------------------------------------------------------

    signature_pattern = re.compile(
        r"\s*Roy Holdaway\s*\n\s*Web Developer\s*$",
        re.IGNORECASE
    )

    if signature_pattern.search(body):
        return body

    # --------------------------------------------------------
    # Add signature.
    # --------------------------------------------------------

    return body.rstrip() + "\n\n" + SIGNATURE


def find_credentials_file():

    if not PROCESSING_DIR.exists():
        return None

    json_files = list(
        PROCESSING_DIR.glob("*.json")
    )

    if not json_files:
        return None

    if len(json_files) == 1:
        return json_files[0]

    preferred = []

    for path in json_files:

        name = path.name.lower()

        if (
            "client_secret" in name
            or "client-secret" in name
            or "credentials" in name
        ):
            preferred.append(path)

    if len(preferred) == 1:
        return preferred[0]

    print()
    print(
        "ERROR: Multiple JSON files were found in:"
    )
    print(PROCESSING_DIR)

    for path in json_files:
        print(f"  - {path.name}")

    print()
    print(
        "Keep only the Google OAuth client-secret file "
        "in that folder, or rename it to contain "
        "'client_secret' or 'credentials'."
    )

    return None


# ============================================================
# GOOGLE AUTHENTICATION
# ============================================================

def get_gmail_service():

    global CREDENTIALS_FILE

    CREDENTIALS_FILE = find_credentials_file()

    if CREDENTIALS_FILE is None:

        print()
        print(
            "ERROR: Google client-secret JSON file not found."
        )
        print()
        print("Expected location:")
        print(PROCESSING_DIR)
        print()

        return None

    print()
    print("Using Google credentials:")
    print(CREDENTIALS_FILE)

    creds = None

    # --------------------------------------------------------
    # Load existing authorization
    # --------------------------------------------------------

    if TOKEN_FILE.exists():

        try:

            creds = Credentials.from_authorized_user_file(
                str(TOKEN_FILE),
                SCOPES
            )

        except Exception as e:

            print()
            print(
                f"Existing Gmail token could not be loaded: {e}"
            )

            creds = None

    # --------------------------------------------------------
    # Refresh authorization
    # --------------------------------------------------------

    if creds and creds.expired and creds.refresh_token:

        try:

            print(
                "Refreshing Gmail authorization..."
            )

            creds.refresh(Request())

        except Exception as e:

            print(
                f"Token refresh failed: {e}"
            )

            creds = None

    # --------------------------------------------------------
    # OAuth authorization
    # --------------------------------------------------------

    if not creds or not creds.valid:

        print()
        print(
            "Gmail authorization is required."
        )
        print(
            "A browser window will open."
        )
        print(
            "Sign into the Gmail account where the drafts "
            "should be created."
        )
        print()

        try:

            flow = (
                InstalledAppFlow
                .from_client_secrets_file(
                    str(CREDENTIALS_FILE),
                    SCOPES
                )
            )

            creds = flow.run_local_server(
                port=0
            )

        except Exception as e:

            print()
            print(
                "ERROR during Google authorization:"
            )
            print(e)

            return None

        try:

            TOKEN_FILE.write_text(
                creds.to_json(),
                encoding="utf-8"
            )

            print()
            print(
                "Gmail authorization saved to:"
            )
            print(TOKEN_FILE)

        except Exception as e:

            print()
            print(
                f"WARNING: Could not save Gmail token: {e}"
            )

    # --------------------------------------------------------
    # Build Gmail API client
    # --------------------------------------------------------

    try:

        service = build(
            "gmail",
            "v1",
            credentials=creds
        )

        profile = (
            service.users()
            .getProfile(
                userId="me"
            )
            .execute()
        )

        email_address = profile.get(
            "emailAddress",
            ""
        )

        print()
        print(
            "Gmail authenticated successfully."
        )
        print(
            f"Account: {email_address}"
        )

        return service

    except Exception as e:

        print()
        print(
            f"ERROR connecting to Gmail API: {e}"
        )

        return None


# ============================================================
# CHECKPOINT
# ============================================================

def load_checkpoint():

    if not CHECKPOINT_FILE.exists():
        return {}

    try:

        df = pd.read_excel(
            CHECKPOINT_FILE,
            dtype=object
        )

    except Exception as e:

        print()
        print(
            f"WARNING: Could not load checkpoint: {e}"
        )

        return {}

    checkpoint = {}

    for _, row in df.iterrows():

        lead_number = clean(
            row.get(
                "#",
                ""
            )
        )

        if not lead_number:
            continue

        checkpoint[lead_number] = {
            "status": clean(
                row.get(
                    "Draft Status",
                    ""
                )
            ),
            "draft_id": clean(
                row.get(
                    "Gmail Draft ID",
                    ""
                )
            ),
            "business": clean(
                row.get(
                    "Business",
                    ""
                )
            ),
            "email": clean(
                row.get(
                    "Email",
                    ""
                )
            ),
        }

    return checkpoint


def save_checkpoint(checkpoint):

    rows = []

    for lead_number, data in checkpoint.items():

        rows.append({
            "#": lead_number,
            "Business": data.get(
                "business",
                ""
            ),
            "Email": data.get(
                "email",
                ""
            ),
            "Draft Status": data.get(
                "status",
                ""
            ),
            "Gmail Draft ID": data.get(
                "draft_id",
                ""
            ),
        })

    df = pd.DataFrame(
        rows,
        columns=[
            "#",
            "Business",
            "Email",
            "Draft Status",
            "Gmail Draft ID",
        ]
    )

    try:

        df.to_excel(
            CHECKPOINT_FILE,
            index=False
        )

        return True

    except Exception as e:

        print()
        print(
            f"WARNING: Could not save checkpoint: {e}"
        )

        return False


# ============================================================
# CREATE NEW GMAIL DRAFT
# ============================================================

def create_gmail_draft(
    service,
    recipient,
    subject,
    body
):

    recipient = clean(recipient)
    subject = clean(subject)
    body = add_signature(body)

    if not valid_email(recipient):
        raise ValueError(
            f"Invalid recipient email: {recipient}"
        )

    if not subject:
        raise ValueError(
            "Email subject is empty."
        )

    if not body:
        raise ValueError(
            "Email body is empty."
        )

    message = EmailMessage()

    message["To"] = recipient
    message["Subject"] = subject

    message.set_content(body)

    encoded_message = base64.urlsafe_b64encode(
        message.as_bytes()
    ).decode()

    draft_body = {
        "message": {
            "raw": encoded_message
        }
    }

    # CREATE ONLY.
    # There is intentionally NO send() call.

    draft = (
        service.users()
        .drafts()
        .create(
            userId="me",
            body=draft_body
        )
        .execute()
    )

    return draft.get(
        "id",
        ""
    )


# ============================================================
# UPDATE EXISTING GMAIL DRAFT
# ============================================================

def update_existing_gmail_draft(
    service,
    draft_id
):
    """
    Retrieve an existing Gmail draft, add the Roy Holdaway
    signature, and update that SAME draft.

    This does NOT create a second draft.
    """

    if not draft_id:
        raise ValueError(
            "Missing Gmail Draft ID."
        )

    # --------------------------------------------------------
    # Retrieve existing draft.
    # --------------------------------------------------------

    draft = (
        service.users()
        .drafts()
        .get(
            userId="me",
            id=draft_id,
            format="raw"
        )
        .execute()
    )

    message_data = draft.get(
        "message",
        {}
    )

    raw_message = message_data.get(
        "raw"
    )

    if not raw_message:
        raise ValueError(
            "Existing Gmail draft did not contain raw message data."
        )

    # Gmail uses URL-safe base64.
    decoded = base64.urlsafe_b64decode(
        raw_message.encode()
    )

    original_message = BytesParser(
        policy=policy.default
    ).parsebytes(decoded)

    # --------------------------------------------------------
    # Extract headers.
    # --------------------------------------------------------

    to_address = original_message.get(
        "To",
        ""
    )

    subject = original_message.get(
        "Subject",
        ""
    )

    # --------------------------------------------------------
    # Extract body.
    #
    # These drafts are generated as text/plain, but this
    # handles multipart messages too.
    # --------------------------------------------------------

    body = ""

    if original_message.is_multipart():

        plain_parts = []

        for part in original_message.walk():

            if (
                part.get_content_type()
                == "text/plain"
                and part.get_content_disposition()
                != "attachment"
            ):

                try:
                    plain_parts.append(
                        part.get_content()
                    )
                except Exception:
                    payload = part.get_payload(
                        decode=True
                    )

                    if payload:
                        plain_parts.append(
                            payload.decode(
                                "utf-8",
                                errors="replace"
                            )
                        )

        body = "\n".join(
            plain_parts
        )

    else:

        try:
            body = original_message.get_content()
        except Exception:

            payload = original_message.get_payload(
                decode=True
            )

            if payload:
                body = payload.decode(
                    "utf-8",
                    errors="replace"
                )

    body = add_signature(body)

    # --------------------------------------------------------
    # Rebuild the email.
    # --------------------------------------------------------

    new_message = EmailMessage()

    if to_address:
        new_message["To"] = to_address

    if subject:
        new_message["Subject"] = subject

    new_message.set_content(
        body
    )

    encoded_message = base64.urlsafe_b64encode(
        new_message.as_bytes()
    ).decode()

    update_body = {
        "id": draft_id,
        "message": {
            "raw": encoded_message
        }
    }

    # --------------------------------------------------------
    # UPDATE the existing draft.
    #
    # This is NOT drafts.create().
    # This is NOT drafts.send().
    # --------------------------------------------------------

    updated = (
        service.users()
        .drafts()
        .update(
            userId="me",
            id=draft_id,
            body=update_body
        )
        .execute()
    )

    return updated.get(
        "id",
        draft_id
    )


# ============================================================
# UPDATE ALL PREVIOUSLY CREATED DRAFTS
# ============================================================

def update_previous_drafts(
    service,
    checkpoint
):

    drafted = []

    for lead_number, data in checkpoint.items():

        status = clean(
            data.get(
                "status",
                ""
            )
        ).upper()

        draft_id = clean(
            data.get(
                "draft_id",
                ""
            )
        )

        if status == "DRAFTED" and draft_id:

            drafted.append(
                (
                    lead_number,
                    data
                )
            )

    if not drafted:

        print()
        print(
            "No existing Gmail drafts found in checkpoint."
        )

        return 0, 0

    print()
    print("=" * 70)
    print("UPDATING EXISTING GMAIL DRAFTS")
    print("=" * 70)

    print(
        f"Existing drafts to update: {len(drafted)}"
    )

    successful = 0
    failed = 0

    for position, (lead_number, data) in enumerate(
        drafted,
        start=1
    ):

        business = clean(
            data.get(
                "business",
                ""
            )
        )

        draft_id = clean(
            data.get(
                "draft_id",
                ""
            )
        )

        print()
        print(
            f"[{position}/{len(drafted)}] "
            f"{business}"
        )
        print(
            f"Draft ID: {draft_id}"
        )

        try:

            updated_id = update_existing_gmail_draft(
                service,
                draft_id
            )

            checkpoint[
                lead_number
            ]["draft_id"] = updated_id

            checkpoint[
                lead_number
            ]["status"] = "DRAFTED"

            save_checkpoint(
                checkpoint
            )

            successful += 1

            print(
                "UPDATED: Roy Holdaway / Web Developer "
                "signature added."
            )

        except Exception as e:

            failed += 1

            print(
                f"ERROR updating draft: {e}"
            )

        time.sleep(
            REQUEST_DELAY
        )

    print()
    print(
        f"Existing drafts updated: {successful}"
    )
    print(
        f"Existing drafts failed: {failed}"
    )

    return successful, failed


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 70)
    print("GMAIL EMAIL DRAFT AGENT")
    print("=" * 70)

    print()
    print(
        "Signature:"
    )
    print(
        "Roy Holdaway"
    )
    print(
        "Web Developer"
    )

    # --------------------------------------------------------
    # Input validation
    # --------------------------------------------------------

    if not INPUT_FILE.exists():

        print()
        print(
            "ERROR: Outreach Excel file not found:"
        )
        print(INPUT_FILE)

        return

    # --------------------------------------------------------
    # Load Excel
    # --------------------------------------------------------

    try:

        df = pd.read_excel(
            INPUT_FILE,
            dtype=object
        )

    except Exception as e:

        print()
        print(
            f"ERROR reading outreach Excel: {e}"
        )

        return

    print()
    print(
        f"Loaded {len(df)} rows."
    )

    # --------------------------------------------------------
    # Required columns
    # --------------------------------------------------------

    required_columns = [
        "#",
        "Business",
        "Email",
        "Qualification",
        "Email Subject",
        "Personalized Email",
        "Outreach Score",
    ]

    missing = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing:

        print()
        print(
            "ERROR: The Excel file is missing:"
        )

        for column in missing:
            print(
                f"  - {column}"
            )

        return

    # --------------------------------------------------------
    # Load checkpoint
    # --------------------------------------------------------

    checkpoint = load_checkpoint()

    print()
    print(
        f"Loaded {len(checkpoint)} checkpoint records."
    )

    # --------------------------------------------------------
    # Authenticate BEFORE modifying existing drafts.
    # --------------------------------------------------------

    service = get_gmail_service()

    if service is None:
        return

    # --------------------------------------------------------
    # Update existing drafts FIRST.
    # --------------------------------------------------------

    if UPDATE_EXISTING_DRAFTS:

        update_previous_drafts(
            service,
            checkpoint
        )

    # --------------------------------------------------------
    # Determine new eligible leads.
    # --------------------------------------------------------

    eligible = []

    for index, row in df.iterrows():

        lead_number = clean(
            row.get(
                "#",
                ""
            )
        )

        business = clean(
            row.get(
                "Business",
                ""
            )
        )

        email = clean(
            row.get(
                "Email",
                ""
            )
        )

        qualification = (
            clean(
                row.get(
                    "Qualification",
                    ""
                )
            )
            .upper()
        )

        subject = clean(
            row.get(
                "Email Subject",
                ""
            )
        )

        body = clean(
            row.get(
                "Personalized Email",
                ""
            )
        )

        # ----------------------------------------------------
        # Only STRONG / GOOD leads.
        # ----------------------------------------------------

        if qualification not in VALID_QUALIFICATIONS:
            continue

        if not valid_email(email):
            continue

        if not subject or not body:
            continue

        # ----------------------------------------------------
        # NEVER recreate an existing draft.
        # ----------------------------------------------------

        existing = checkpoint.get(
            lead_number,
            {}
        )

        existing_status = (
            clean(
                existing.get(
                    "status",
                    ""
                )
            )
            .upper()
        )

        if existing_status == "DRAFTED":
            continue

        eligible.append(
            (
                index,
                row
            )
        )

    # --------------------------------------------------------
    # Sort by Outreach Score.
    # --------------------------------------------------------

    def score_for_sort(item):

        _, row = item

        try:

            return float(
                row.get(
                    "Outreach Score",
                    0
                )
            )

        except (
            TypeError,
            ValueError
        ):

            return 0

    eligible.sort(
        key=score_for_sort,
        reverse=True
    )

    print()
    print(
        "=" * 70
    )
    print(
        "NEW DRAFTS"
    )
    print(
        "=" * 70
    )

    print()
    print(
        f"New eligible leads remaining: {len(eligible)}"
    )

    if not eligible:

        print()
        print(
            "No new eligible leads need drafts."
        )

        print()
        print(
            "All existing drafts were checked/updated."
        )

        print()
        print(
            "NO EMAILS WERE SENT."
        )

        return

    # --------------------------------------------------------
    # Apply run limit.
    # --------------------------------------------------------

    if MAX_DRAFTS_PER_RUN is None:

        batch = eligible

    else:

        batch = eligible[
            :MAX_DRAFTS_PER_RUN
        ]

    print(
        f"New drafts this run: {len(batch)}"
    )

    print()
    print(
        "IMPORTANT: This program creates/updates "
        "Gmail DRAFTS only."
    )
    print(
        "It does NOT send emails."
    )

    # --------------------------------------------------------
    # Create new drafts.
    # --------------------------------------------------------

    successful = 0
    failed = 0

    for position, (index, row) in enumerate(
        batch,
        start=1
    ):

        lead_number = clean(
            row.get(
                "#",
                ""
            )
        )

        business = clean(
            row.get(
                "Business",
                ""
            )
        )

        email = clean(
            row.get(
                "Email",
                ""
            )
        )

        subject = clean(
            row.get(
                "Email Subject",
                ""
            )
        )

        body = clean(
            row.get(
                "Personalized Email",
                ""
            )
        )

        score = clean(
            row.get(
                "Outreach Score",
                ""
            )
        )

        print()
        print(
            "-" * 70
        )

        print(
            f"[{position}/{len(batch)}] "
            f"{business}"
        )

        print(
            f"Email: {email}"
        )

        print(
            f"Outreach Score: {score}"
        )

        print(
            f"Subject: {subject}"
        )

        try:

            draft_id = create_gmail_draft(
                service,
                email,
                subject,
                body
            )

            checkpoint[
                lead_number
            ] = {
                "status": "DRAFTED",
                "draft_id": draft_id,
                "business": business,
                "email": email,
            }

            save_checkpoint(
                checkpoint
            )

            successful += 1

            print(
                f"CREATED DRAFT: {draft_id}"
            )

        except Exception as e:

            failed += 1

            checkpoint[
                lead_number
            ] = {
                "status": "ERROR",
                "draft_id": "",
                "business": business,
                "email": email,
            }

            save_checkpoint(
                checkpoint
            )

            print(
                f"ERROR: {e}"
            )

        time.sleep(
            REQUEST_DELAY
        )

    # --------------------------------------------------------
    # Final summary.
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("DRAFT RUN COMPLETE")
    print("=" * 70)

    print(
        f"New drafts created: {successful}"
    )

    print(
        f"New drafts failed: {failed}"
    )

    print()
    print(
        "Existing drafts were updated with:"
    )

    print(
        "Roy Holdaway"
    )

    print(
        "Web Developer"
    )

    print()
    print(
        "Checkpoint:"
    )

    print(
        CHECKPOINT_FILE
    )

    print()
    print(
        "Open Gmail → Drafts to review."
    )

    print()
    print(
        "NO EMAILS WERE SENT."
    )

    print()


if __name__ == "__main__":
    main()