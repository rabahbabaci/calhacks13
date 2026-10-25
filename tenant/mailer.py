"""Email transport. Tests use MemoryMailer, which records the message and sends nothing.

Gmail is a separate transport and is not constructed unless GMAIL_SEND=1. That path is not enabled.
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
