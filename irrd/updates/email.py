# flake8: noqa: W293
import email
import logging
import socket
import textwrap
from email.mime.text import MIMEText
from smtplib import SMTP
from typing import Optional

from irrd import __version__
from irrd.conf import get_setting
from irrd.updates.handler import UpdateRequestHandler
from irrd.utils.pgp import validate_pgp_signature

logger = logging.getLogger(__name__)


class EmailUpdateParser:
    """
    Parse a raw email.
    """
    body: Optional[str] = None
    pgp_fingerprint: Optional[str] = None
    message_id: Optional[str] = None
    message_from: Optional[str] = None
    message_date: Optional[str] = None
    message_subject: Optional[str] = None
    _default_encoding = 'ascii'
    _raw_body: Optional[str] = None
    _pgp_signature: Optional[str] = None

    def __init__(self, email_txt: str) -> None:
        """
        Extract data from an MIME email message.

        If present, the following fields will be filled:
        - body: the processed message body (decoded, only PGP signed parts included if applicable)
        - pgp_fingerprint: the hex-encoded fingerprint of the PGP key that signed body
        - message_id/message_from/message_date/message_subject: the values of these headers in the message
        """
        message: email.message.Message = email.message_from_string(email_txt)

        if message.is_multipart():
            for part in message.walk():
                charset = part.get_content_charset()
                if not charset:
                    charset = self._default_encoding
                if part.get_content_type() == 'text/plain':
                    self.body = part.get_payload(decode=True).decode(str(charset), 'ignore')  # type: ignore
                    self._raw_body = part.as_string()
                if part.get_content_type() == 'application/pgp-signature':
                    self._pgp_signature = part.get_payload(decode=True).decode(str(charset), 'ignore').strip()  # type: ignore
        else:
            charset = message.get_content_charset()
            if not charset:
                charset = self._default_encoding
            self.body = message.get_payload(decode=True).decode(charset, 'ignore')  # type: ignore
            self._raw_body = message.as_string()

        self.message_id = message['Message-ID']  # type: ignore
        self.message_from = message['From']  # type: ignore
        self.message_date = message['Date']  # type: ignore
        self.message_subject = message['Subject']  # type: ignore

        if not (self.body and self._raw_body):
            return

        new_body, self.pgp_fingerprint = validate_pgp_signature(self._raw_body, self._pgp_signature)
        if new_body:
            self.body = new_body


def handle_email_update(email_txt: str) -> Optional[UpdateRequestHandler]:
    email = EmailUpdateParser(email_txt)
    request_meta = {
        'Message-ID': email.message_id,
        'From': email.message_from,
        'Date': email.message_date,
        'Subject': email.message_subject,
    }
    if not email.body:
        handler = None
        subject = f'FAILED: {email.message_subject}'
        reply_content = textwrap.dedent(f"""
        Unfortunately, your message with ID {email.message_id}
        could not be processed, as no text/plain part could be found.
        
        Please try to resend your message as plain text email.
        """)
    else:
        handler = UpdateRequestHandler(email.body, email.pgp_fingerprint, request_meta)
        subject = f'{handler.status()}: {email.message_subject}'
        reply_content = handler.user_report()

    send_email(email.message_from, subject, reply_content)
    return handler


def send_email(recipient, subject, body) -> None:
    body += get_setting('email.footer')
    hostname = socket.gethostname()
    body += f'\n\nGenerated by IRRDv4 version {__version__} on {hostname}'

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = get_setting('email.from')
    msg['To'] = recipient

    s = SMTP(get_setting('email.smtp'))
    s.send_message(msg)
    s.quit()