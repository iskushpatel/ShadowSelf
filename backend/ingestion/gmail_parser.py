# backend/app/ingestion/gmail_parser.py
import base64
import datetime
import importlib
from typing import AsyncGenerator
from uuid import UUID

from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from .base_parser import BaseParser
from ..schemas.post import PostCreate


class GmailParser(BaseParser):
    """
    Stage 1 Automated Parser for Gmail using official Google API + LangChain compatible.
    Fetches user's own emails.
    """

    def __init__(self, credentials: Credentials):
        self.credentials = credentials
        self.service = build('gmail', 'v1', credentials=credentials)

    async def parse(
        self, 
        user_id: UUID, 
        since_date: datetime.datetime = None,  # For incremental sync
        max_results: int = 100
    ) -> AsyncGenerator[PostCreate, None]:
        """
        Fetch emails since a date (incremental) or all.
        """
        query = ""
        if since_date:
            query = f"after:{since_date.strftime('%Y/%m/%d')}"

        results = self.service.users().messages().list(
            userId='me', 
            q=query,
            maxResults=max_results
        ).execute()

        messages = results.get('messages', [])

        for msg in messages:
            post = await self._process_message(msg['id'], user_id)
            if post:
                post.content_hash = self.generate_hash(post.content)
                yield post

    async def _process_message(self, msg_id: str, user_id: UUID) -> PostCreate | None:
        """Convert Gmail message to PostCreate."""
        try:
            msg = self.service.users().messages().get(
                userId='me', 
                id=msg_id,
                format='full'
            ).execute()

            # Get headers
            headers = {h['name'].lower(): h['value'] for h in msg['payload']['headers']}
            
            subject = headers.get('subject', '(No Subject)')
            
            # Decode body
            body = self._get_body(msg)
            
            full_content = f"Subject: {subject}\n\n{body}"

            return PostCreate(
                user_id=user_id,
                source="gmail",
                content=self.normalize_text(full_content),
                created_at=datetime.datetime.fromtimestamp(int(msg['internalDate']) / 1000),
                metadata={
                    "platform": "gmail",
                    "message_id": msg_id,
                    "subject": subject,
                    "from": headers.get('from'),
                    "to": headers.get('to'),
                    "thread_id": msg.get('threadId'),
                    "labels": msg.get('labelIds', [])
                }
            )
        except Exception:
            return None

    def _get_body(self, msg) -> str:
        """Extract readable body from Gmail message (handles plain text + HTML)."""
        if 'parts' in msg['payload']:
            for part in msg['payload']['parts']:
                if part['mimeType'] == 'text/plain':
                    data = part['body'].get('data', '')
                    return base64.urlsafe_b64decode(data).decode('utf-8')
        elif 'body' in msg['payload'] and 'data' in msg['payload']['body']:
            data = msg['payload']['body']['data']
            return base64.urlsafe_b64decode(data).decode('utf-8')
        return ""


"""Utility function to get Gmail credentials using OAuth2 flow. This should be called in your main app logic, and the resulting credentials passed to GmailParser."""
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def get_gmail_credentials():
    InstalledAppFlow = importlib.import_module("google_auth_oauthlib.flow").InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
    creds = flow.run_local_server(port=0)
    return creds