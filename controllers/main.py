import base64
import io
import json
import threading
import time
import uuid

from odoo import http
from odoo.exceptions import ValidationError
from odoo.http import request, content_disposition

from ..models.form_builder import build_xlsx_content, build_xlsx_for_submissions


class MobileFormController(http.Controller):
    CLIENT_COOKIE_KEY = "mform_client_id"
    DECODE_RATE_WINDOW_SECONDS = 60
    DECODE_RATE_MAX_REQUESTS = 90
    _decode_rate_lock = threading.Lock()
    _decode_rate_by_ip = {}
    EMPTY_PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9s0l4wAAAABJRU5ErkJggg=="
    )

    def _get_or_create_client_id(self):
        client_id = (request.httprequest.cookies.get(self.CLIENT_COOKIE_KEY) or "").strip()
        created = False
        if not client_id:
            client_id = uuid.uuid4().hex
            created = True
        return client_id, created

    def _set_client_cookie_if_needed(self, response, created, client_id):
        if created and response is not None:
            response.set_cookie(
                self.CLIENT_COOKIE_KEY,
                client_id,
                max_age=60 * 60 * 24 * 365,
                httponly=True,
                samesite="Lax",
            )
        return response

    def _build_posted_values(self, form_data):
        values = {}
        values_multi = {}
        try:
            iterator = form_data.lists()
        except Exception:
            iterator = []
        for key, items in iterator:
            normalized = [((item or "").strip()) for item in items]
            values_multi[key] = normalized
            values[key] = normalized[0] if normalized else ""
        return values, values_multi

    def _allow_decode_request(self):
        forwarded = request.httprequest.headers.get("X-Forwarded-For", "") or ""
        ip = forwarded.split(",")[0].strip() if forwarded else ""
        if not ip:
            ip = (request.httprequest.remote_addr or "").strip() or "unknown"

        now = time.time()
        window_start = now - self.DECODE_RATE_WINDOW_SECONDS
        with self._decode_rate_lock:
            bucket = self._decode_rate_by_ip.get(ip, [])
            bucket = [ts for ts in bucket if ts >= window_start]
            if len(bucket) >= self.DECODE_RATE_MAX_REQUESTS:
                self._decode_rate_by_ip[ip] = bucket
                return False
            bucket.append(now)
            self._decode_rate_by_ip[ip] = bucket
        return True

    def _render_form_page(self, form, error=False, form_values=None, form_values_multi=None, has_form_values=False):
        values = {
            "form": form,
            "components": form.component_ids.sorted("sequence"),
            "error": error or False,
            "form_values": form_values or {},
            "form_values_multi": form_values_multi or {},
            "has_form_values": bool(has_form_values),
        }
        return request.render("mobile_form_builder.mobile_form_page", values)

    def _render_form_closed_page(self, form):
        msg = (form.closed_message or "This form is currently closed.").strip()
        values = {
            "form": form,
            "closed_message": msg,
        }
        return request.render("mobile_form_builder.mobile_form_closed_page", values)

    @http.route(["/mform/<string:token>"], type="http", auth="public", website=True)
    def public_form(self, token, **post):
        form = request.env["x_mobile.form"].sudo().search([("access_token", "=", token)], limit=1)
        if not form:
            return request.not_found()
        if not form.is_enabled:
            response = self._render_form_closed_page(form)
            client_id, cookie_created = self._get_or_create_client_id()
            return self._set_client_cookie_if_needed(response, cookie_created, client_id)
        client_id, cookie_created = self._get_or_create_client_id()

        if request.httprequest.method == "POST":
            posted_values, posted_values_multi = self._build_posted_values(request.httprequest.form)
            if not form.allow_repeat_client_submit:
                existed = request.env["x_mobile.form.submission"].sudo().search_count(
                    [("form_id", "=", form.id), ("client_identifier", "=", client_id)]
                )
                if existed:
                    response = self._render_form_page(
                        form,
                        error="This form does not allow repeated submission from the same client.",
                        form_values=posted_values,
                        form_values_multi=posted_values_multi,
                        has_form_values=True,
                    )
                    return self._set_client_cookie_if_needed(response, cookie_created, client_id)

            submission_vals = []
            answer_payload = {}
            form_data = request.httprequest.form
            files_data = request.httprequest.files

            for component in form.component_ids.sorted("sequence"):
                key = component.key
                value = ""
                value_text = ""
                attachment_id = False
                is_visible = component.is_visible_in_public_form(form_data)
                if is_visible:
                    if component.component_type == "checkbox":
                        selected = form_data.getlist(key)
                        value = ", ".join(selected)
                    elif component.component_type == "file_upload":
                        upload = files_data.get(key)
                        if upload and upload.filename:
                            component.validate_uploaded_file(upload)
                            stream = upload.stream
                            stream.seek(0)
                            content = stream.read()
                            stream.seek(0)
                            attach = request.env["ir.attachment"].sudo().create(
                                {
                                    "name": upload.filename,
                                    "type": "binary",
                                    "datas": base64.b64encode(content),
                                    "mimetype": upload.mimetype or "application/octet-stream",
                                    "res_model": "x_mobile.form.submission.line",
                                    "res_id": 0,
                                }
                            )
                            value = upload.filename
                            attachment_id = attach.id
                        else:
                            value = ""
                    elif component.component_type == "age_auto":
                        linked = component.linked_date_component_id
                        linked_value = (form_data.get(linked.key) or "").strip() if linked and linked.key else ""
                        value = component.compute_age_from_date_string(linked_value)
                    else:
                        value = (form_data.get(key) or "").strip()
                else:
                    value = ""
                    attachment_id = False

                try:
                    value = component.apply_input_rules(value)
                except ValidationError as exc:
                    response = self._render_form_page(
                        form,
                        error=str(exc),
                        form_values=posted_values,
                        form_values_multi=posted_values_multi,
                        has_form_values=True,
                    )
                    return self._set_client_cookie_if_needed(response, cookie_created, client_id)

                # Keep answer_json in raw value, but store formatted date string in line.value_text.
                value_text = value
                if is_visible and component.component_type == "date" and value:
                    try:
                        value_text = component.format_date_value(value)
                    except Exception:
                        value_text = value

                if is_visible and component.component_type == "age_auto":
                    policy = component.evaluate_age_policy(value)
                    if policy.get("block"):
                        response = self._render_form_page(
                            form,
                            error=policy.get("message") or "Age does not meet form requirements.",
                            form_values=posted_values,
                            form_values_multi=posted_values_multi,
                            has_form_values=True,
                        )
                        return self._set_client_cookie_if_needed(response, cookie_created, client_id)

                if is_visible and component.required and not value:
                    response = self._render_form_page(
                        form,
                        error=f"Field '{component.name}' is required.",
                        form_values=posted_values,
                        form_values_multi=posted_values_multi,
                        has_form_values=True,
                    )
                    return self._set_client_cookie_if_needed(response, cookie_created, client_id)

                answer_payload[key] = value
                submission_vals.append(
                    {
                        "component_id": component.id,
                        "sequence_snapshot": component.sequence or 0,
                        "component_type_snapshot": component.component_type or "",
                        "attachment_id": attachment_id or False,
                        "key": key,
                        "label": component.name,
                        "value_text": value_text,
                    }
                )

            confirm_key1 = ""
            confirm_key2 = ""
            try:
                c1 = form.confirm_component_id_1
                c2 = form.confirm_component_id_2
                if c1 and c1.key:
                    confirm_key1 = str(answer_payload.get(c1.key) or "").strip()
                if c2 and c2.key:
                    confirm_key2 = str(answer_payload.get(c2.key) or "").strip()
            except Exception:
                confirm_key1 = ""
                confirm_key2 = ""

            submission = request.env["x_mobile.form.submission"].sudo().create(
                {
                    "form_id": form.id,
                    "client_identifier": client_id,
                    "answer_json": json.dumps(answer_payload, ensure_ascii=False),
                    "line_ids": [(0, 0, line) for line in submission_vals],
                    "confirm_key1_value": confirm_key1,
                    "confirm_key2_value": confirm_key2,
                }
            )
            values = {"form": form, "submission": submission}
            response = request.render("mobile_form_builder.mobile_form_thanks", values)
            return self._set_client_cookie_if_needed(response, cookie_created, client_id)

        response = self._render_form_page(form)
        return self._set_client_cookie_if_needed(response, cookie_created, client_id)

    @http.route(
        ["/mform/qr/<string:token>.png", "/mform/qr/<string:token>.svg", "/mform/qr/<string:token>"],
        type="http",
        auth="public",
    )
    def public_form_qr(self, token, **kwargs):
        try:
            form = request.env["x_mobile.form"].sudo().search([("access_token", "=", token)], limit=1)
            if not form or not form.share_url:
                return request.not_found()
            share_url = form.share_url
        except Exception:
            share_url = ""

        if not share_url:
            headers = [("Content-Type", "image/png"), ("Cache-Control", "no-store")]
            return request.make_response(self.EMPTY_PNG, headers=headers)

        path = request.httprequest.path or ""
        as_svg = path.endswith(".svg")
        if as_svg:
            try:
                from reportlab.graphics.barcode import createBarcodeDrawing

                drawing = createBarcodeDrawing("QR", value=share_url, width=320, height=320)
                svg_bytes = drawing.asString("svg")
                headers = [("Content-Type", "image/svg+xml"), ("Cache-Control", "public, max-age=600")]
                return request.make_response(svg_bytes, headers=headers)
            except Exception:
                pass

        png_bytes = b""
        try:
            import qrcode

            qr = qrcode.QRCode(box_size=6, border=2)
            qr.add_data(share_url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            output = io.BytesIO()
            img.save(output, format="PNG")
            png_bytes = output.getvalue()
        except Exception:
            png_bytes = b""

        if png_bytes:
            headers = [("Content-Type", "image/png"), ("Cache-Control", "public, max-age=600")]
            return request.make_response(png_bytes, headers=headers)

        try:
            from reportlab.graphics.barcode import createBarcodeDrawing

            drawing = createBarcodeDrawing("QR", value=share_url, width=320, height=320)
            png_bytes = drawing.asString("png")
        except Exception:
            try:
                from reportlab.graphics import renderPM
                from reportlab.graphics.shapes import Drawing
                from reportlab.graphics.barcode import qr

                widget = qr.QrCodeWidget(share_url)
                bounds = widget.getBounds()
                width = max(1, bounds[2] - bounds[0])
                height = max(1, bounds[3] - bounds[1])
                drawing = Drawing(width, height, transform=[1, 0, 0, 1, -bounds[0], -bounds[1]])
                drawing.add(widget)
                png_bytes = renderPM.drawToString(drawing, fmt="PNG")
            except Exception:
                png_bytes = b""

        if not png_bytes:
            headers = [("Content-Type", "image/png"), ("Cache-Control", "no-store")]
            return request.make_response(self.EMPTY_PNG, headers=headers)

        headers = [("Content-Type", "image/png"), ("Cache-Control", "public, max-age=600")]
        return request.make_response(png_bytes, headers=headers)

    @http.route(["/mform/export/<int:form_id>"], type="http", auth="user")
    def export_form_xlsx(self, form_id, **kwargs):
        form = request.env["x_mobile.form"].browse(form_id)
        if not form.exists():
            return request.not_found()
        form.check_access_rights("read")
        form.check_access_rule("read")

        content = build_xlsx_content(form)
        headers = [
            ("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("Content-Disposition", content_disposition(f"{form.name}_submissions.xlsx")),
        ]
        return request.make_response(content, headers=headers)

    @http.route(["/mform/export_pdf/<int:form_id>"], type="http", auth="user")
    def export_form_pdf(self, form_id, **kwargs):
        form = request.env["x_mobile.form"].browse(form_id)
        if not form.exists():
            return request.not_found()
        form.check_access_rights("read")
        form.check_access_rule("read")

        submissions = form.submission_ids.sorted("submit_date")
        if not submissions:
            return request.not_found()

        report_ref = "mobile_form_builder.action_report_mobile_form_submission"
        company = request.env.company
        tz = ""
        try:
            tz = (getattr(company, "tz", "") or "").strip()
        except Exception:
            tz = ""
        if not tz:
            try:
                tz = (getattr(company.partner_id, "tz", "") or "").strip()
            except Exception:
                tz = ""
        tz = tz or (request.env.user.tz or "UTC")
        pdf, _ = (
            request.env["ir.actions.report"]
            .with_context(tz=tz)
            .sudo()
            ._render_qweb_pdf(report_ref, submissions.ids)
        )
        headers = [
            ("Content-Type", "application/pdf"),
            ("Content-Length", len(pdf)),
            ("Content-Disposition", content_disposition(f"{form.name}_submissions.pdf")),
        ]
        return request.make_response(pdf, headers=headers)

    @http.route(["/mform/export_selected_xlsx"], type="http", auth="user")
    def export_selected_xlsx(self, ids=None, **kwargs):
        id_list = []
        if ids:
            id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        submissions = request.env["x_mobile.form.submission"].browse(id_list).exists()
        if not submissions:
            return request.not_found()
        submissions.check_access_rights("read")
        submissions.check_access_rule("read")

        content = build_xlsx_for_submissions(submissions)
        headers = [
            ("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("Content-Disposition", content_disposition("selected_submissions.xlsx")),
        ]
        return request.make_response(content, headers=headers)

    @http.route(["/mform/decode_barcode"], type="http", auth="public", methods=["POST"], csrf=False)
    def decode_barcode(self, **kwargs):
        if not self._allow_decode_request():
            return request.make_json_response({"ok": False, "reason": "rate_limited"}, status=429)

        max_body_bytes = 6 * 1024 * 1024  # 6MB safety limit for public endpoint
        content_length = int(request.httprequest.content_length or 0)
        if content_length and content_length > max_body_bytes:
            return request.make_json_response({"ok": False, "reason": "payload_too_large"})

        payload = request.httprequest.get_json(silent=True) or {}
        image_data = (payload.get("image_data") or "").strip()
        use_deep = bool(payload.get("deep"))
        prefer_1d = bool(payload.get("prefer_1d"))
        if image_data.startswith("data:image"):
            parts = image_data.split(",", 1)
            image_data = parts[1] if len(parts) == 2 else ""
        if not image_data:
            return request.make_json_response({"ok": False, "reason": "empty"})
        if len(image_data) > 8 * 1024 * 1024:
            return request.make_json_response({"ok": False, "reason": "payload_too_large"})

        try:
            raw = base64.b64decode(image_data)
        except Exception:
            return request.make_json_response({"ok": False, "reason": "invalid_base64"})

        # 1) Try pyzbar first (fast path).
        try:
            from PIL import Image, ImageOps
            from pyzbar.pyzbar import decode as zbar_decode, ZBarSymbol

            img = Image.open(io.BytesIO(raw)).convert("L")
            candidates = []
            candidates.append(img)
            candidates.append(ImageOps.autocontrast(img))
            symbol_names = ["CODE128", "CODE39", "CODE93", "CODABAR", "EAN13", "EAN8", "UPCA", "UPCE", "I25", "DATABAR"]
            if not prefer_1d:
                symbol_names.extend(["DATABAR_EXP", "PDF417", "QRCODE"])
            symbols = [getattr(ZBarSymbol, name) for name in symbol_names if hasattr(ZBarSymbol, name)]
            for cand in candidates:
                found = zbar_decode(cand, symbols=symbols or None)
                if found:
                    value = (found[0].data or b"").decode("utf-8", errors="ignore").strip()
                    if value:
                        return request.make_json_response({"ok": True, "value": value, "engine": "pyzbar"})

            # Deep path is expensive; run only on selected attempts from frontend.
            if use_deep:
                deep_candidates = []
                deep_candidates.append(img.resize((img.width * 2, img.height * 2)))
                deep_candidates.append(ImageOps.autocontrast(img).resize((img.width * 2, img.height * 2)))
                deep_candidates.append(ImageOps.autocontrast(img).point(lambda p: 255 if p > 140 else 0))
                deep_candidates.append(ImageOps.autocontrast(img).point(lambda p: 255 if p > 170 else 0))
                inv = ImageOps.invert(ImageOps.autocontrast(img))
                deep_candidates.append(inv)
                deep_candidates.append(inv.rotate(90, expand=True))
                deep_candidates.append(inv.rotate(180, expand=True))
                deep_candidates.append(inv.rotate(270, expand=True))
                for cand in deep_candidates:
                    found = zbar_decode(cand, symbols=symbols or None)
                    if found:
                        value = (found[0].data or b"").decode("utf-8", errors="ignore").strip()
                        if value:
                            return request.make_json_response({"ok": True, "value": value, "engine": "pyzbar_deep"})
        except Exception:
            pass

        # 2) Optional zxing-cpp fallback (only on deep attempts to keep fast response).
        if not use_deep:
            return request.make_json_response({"ok": False, "reason": "not_found"})

        try:
            import zxingcpp
            from PIL import Image

            img = Image.open(io.BytesIO(raw)).convert("RGB")
            result = zxingcpp.read_barcode(img)
            if result and getattr(result, "text", ""):
                return request.make_json_response({"ok": True, "value": result.text, "engine": "zxingcpp"})
        except Exception:
            pass

        # If no decoder package is available, return explicit hint.
        try:
            import pyzbar  # noqa: F401
            decoder_available = True
        except Exception:
            decoder_available = False
        if not decoder_available:
            return request.make_json_response(
                {
                    "ok": False,
                    "reason": "decoder_unavailable",
                    "message": "Server barcode decoder is not installed.",
                }
            )
        return request.make_json_response({"ok": False, "reason": "not_found"})
