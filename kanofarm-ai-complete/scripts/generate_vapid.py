"""Generate the VAPID key pair web push needs. Run once, anywhere with Python and the `cryptography` package:
    python scripts/generate_vapid.py
Put VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY in Vercel (Environment Variables). Keep the private key secret; the public
key is not secret (the browser needs it). Set VAPID_SUBJECT to mailto:you@example.com."""
import base64
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def generate() -> dict:
    key = ec.generate_private_key(ec.SECP256R1())
    private_der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public_raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"VAPID_PRIVATE_KEY": b64url(private_der), "VAPID_PUBLIC_KEY": b64url(public_raw)}


if __name__ == "__main__":
    for k, v in generate().items():
        print(f"{k}={v}")
    print("VAPID_SUBJECT=mailto:you@example.com   # change to your own address")
