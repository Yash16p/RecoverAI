import hmac
import hashlib
import razorpay
from app.config import settings

# Singleton Razorpay client (Test Mode)
client = razorpay.Client(
    auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
)
client.set_app_details({"title": "RecoverAI", "version": "1.0.0"})


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    """
    Validate the X-Razorpay-Signature header against the raw request body.
    Razorpay uses HMAC-SHA256 with the webhook secret.
    """
    expected = hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── Thin wrappers around Razorpay Test Mode APIs ──────────────────────────────

def create_order(amount_paise: int, currency: str = "INR", receipt: str = "") -> dict:
    """Create a Razorpay order. amount_paise is in the smallest currency unit."""
    return client.order.create(
        {
            "amount": amount_paise,
            "currency": currency,
            "receipt": receipt,
            "payment_capture": 1,
        }
    )


def fetch_payment(payment_id: str) -> dict:
    return client.payment.fetch(payment_id)


def fetch_order(order_id: str) -> dict:
    return client.order.fetch(order_id)


def generate_payment_link(
    amount_paise: int,
    description: str,
    customer: dict,
    reference_id: str,
    currency: str = "INR",
) -> dict:
    """
    Create a Razorpay Payment Link.
    customer = {"name": ..., "email": ..., "contact": ...}
    """
    return client.payment_link.create(
        {
            "amount": amount_paise,
            "currency": currency,
            "description": description,
            "reference_id": reference_id,
            "customer": customer,
            "notify": {"sms": True, "email": True},
            "reminder_enable": True,
        }
    )


def retry_payment(payment_id: str) -> dict:
    """
    Razorpay does not expose a direct 'retry' endpoint.
    The standard recovery flow is to create a new order and send a payment link.
    This helper fetches the original payment so the caller can re-create the order.
    """
    return fetch_payment(payment_id)
