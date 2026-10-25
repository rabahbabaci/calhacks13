"""Email transport. Tests use MemoryMailer, which records the message and sends nothing.

The browser sends the landlord email on Send with the Gmail grant from Google sign-in,
then this mailer only records that it went out. If that grant is missing, the browser
opens the tenant's mail app with the same draft.
"""


class MemoryMailer:
    def __init__(self):
        self.sent = []

    def send(self, message):
        record = {
            "to": message["to"],
            "subject": message["subject"],
            "body": message["body"],
            "gmail_id": f"fake-{len(self.sent) + 1}",
            "status": "sent",
        }
        self.sent.append(record)
        return record
