from . import models
from . import controllers


def pre_init_check_barcode_dependencies(cr):
    """Ensure server barcode decoder dependencies are ready before install/upgrade."""
    import ctypes.util
    import sys

    missing = []
    detail_errors = []

    # System runtime dependency for pyzbar.
    if not ctypes.util.find_library("zbar"):
        missing.append("system library: zbar (libzbar)")

    try:
        import PIL  # noqa: F401
    except Exception as exc:
        missing.append("python package: Pillow")
        detail_errors.append(f"Pillow: {exc}")

    try:
        import pyzbar  # noqa: F401
    except Exception as exc:
        missing.append("python package: pyzbar")
        detail_errors.append(f"pyzbar: {exc}")

    try:
        import qrcode  # noqa: F401
    except Exception as exc:
        missing.append("python package: qrcode")
        detail_errors.append(f"qrcode: {exc}")

    try:
        import reportlab  # noqa: F401
    except Exception as exc:
        missing.append("python package: reportlab")
        detail_errors.append(f"reportlab: {exc}")

    try:
        import xlsxwriter  # noqa: F401
    except Exception as exc:
        missing.append("python package: xlsxwriter")
        detail_errors.append(f"xlsxwriter: {exc}")

    if missing:
        python_cmd = f"{sys.executable} -m pip install --upgrade Pillow pyzbar qrcode reportlab xlsxwriter"
        raise Exception(
            "mobile_form_builder dependency check failed.\n"
            "Missing dependencies:\n"
            + "\n".join(f"- {item}" for item in sorted(set(missing)))
            + "\n\nInstall on Ubuntu/Debian server:\n"
            + "1) apt-get update -y && apt-get install -y libzbar0\n"
            + f"2) {python_cmd}\n"
            + "\nIf your Odoo venv uses another Python path, run step (2) with that interpreter.\n"
            + ("\nOriginal import errors:\n" + "\n".join(f"- {e}" for e in detail_errors) if detail_errors else "")
        )
