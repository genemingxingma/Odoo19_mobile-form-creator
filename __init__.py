from . import models
from . import controllers


def pre_init_check_barcode_dependencies(cr):
    """Ensure server barcode decoder dependencies are ready before install/upgrade."""
    try:
        from PIL import Image  # noqa: F401
        from pyzbar.pyzbar import decode as zbar_decode  # noqa: F401
    except Exception as exc:
        raise Exception(
            "mobile_form_builder requires barcode decoder dependencies.\n"
            "Please install on server first:\n"
            "1) apt-get install -y libzbar0\n"
            "2) /opt/odoo/venv/bin/pip install --upgrade Pillow pyzbar qrcode\n"
            f"Original error: {exc}"
        ) from exc
