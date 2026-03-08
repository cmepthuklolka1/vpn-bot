import qrcode
import io
import logging

logger = logging.getLogger(__name__)


def generate_qr(data: str) -> io.BytesIO:
    """Generate QR code image as bytes buffer."""
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
