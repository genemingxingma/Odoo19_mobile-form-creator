import base64
import hashlib
import io
import json
import re
import secrets
import threading
import time
import uuid
import zipfile

from odoo import http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request, content_disposition

from ..models.form_builder import build_xlsx_content, build_xlsx_for_submissions


class MobileFormController(http.Controller):
    CLIENT_COOKIE_KEY = "mform_client_id"
    PREFILL_SESSION_KEY = "mform_prefill_store"
    PREFILL_SESSION_TTL_SECONDS = 30 * 60
    PREFILL_SESSION_MAX_ITEMS = 30
    PDF_ZIP_THRESHOLD = 40
    PDF_BATCH_SIZE = 25
    PDF_MODE_MERGED = "merged"
    PDF_MODE_SINGLE = "single"
    DECODE_RATE_WINDOW_SECONDS = 60
    DECODE_RATE_MAX_REQUESTS = 90
    _decode_rate_lock = threading.Lock()
    _decode_rate_by_ip = {}
    _decode_cache_lock = threading.Lock()
    _decode_result_cache = {}
    DECODE_CACHE_TTL_SECONDS = 3
    DECODE_CACHE_MAX_ITEMS = 1200
    EMPTY_PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9s0l4wAAAABJRU5ErkJggg=="
    )

    def _compose_qr_with_description(self, png_bytes, description):
        desc = (description or "").strip()
        if not desc or not png_bytes:
            return png_bytes
        try:
            from PIL import Image, ImageDraw, ImageFont
        except Exception:
            return png_bytes

        try:
            qr_img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            qr_w, qr_h = qr_img.size
            pad = 18
            text_box_width = max(qr_w, 280)
            canvas_w = max(qr_w + pad * 2, text_box_width + pad * 2)
            max_line_w = canvas_w - pad * 2

            font = None
            font_candidates = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                "/Library/Fonts/Arial Unicode.ttf",
            ]
            for font_path in font_candidates:
                try:
                    font = ImageFont.truetype(font_path, 18)
                    break
                except Exception:
                    continue
            if font is None:
                font = ImageFont.load_default()

            draw_probe = ImageDraw.Draw(Image.new("RGB", (10, 10), "white"))

            def _text_width(text):
                try:
                    return int(draw_probe.textlength(text, font=font))
                except Exception:
                    bbox = draw_probe.textbbox((0, 0), text, font=font)
                    return int(bbox[2] - bbox[0])

            def _wrap_line(paragraph):
                words = paragraph.split()
                if len(words) <= 1:
                    raw = paragraph.strip()
                    if not raw:
                        return [""]
                    lines, current = [], ""
                    for ch in raw:
                        cand = f"{current}{ch}"
                        if current and _text_width(cand) > max_line_w:
                            lines.append(current)
                            current = ch
                        else:
                            current = cand
                    if current:
                        lines.append(current)
                    return lines
                if not words:
                    return [""]
                lines, current = [], words[0]
                for word in words[1:]:
                    cand = f"{current} {word}"
                    if _text_width(cand) <= max_line_w:
                        current = cand
                    else:
                        lines.append(current)
                        current = word
                lines.append(current)
                return lines

            wrapped_lines = []
            for para in desc.splitlines():
                text = (para or "").strip()
                if not text:
                    continue
                wrapped_lines.extend(_wrap_line(text))

            if not wrapped_lines:
                return png_bytes

            sample_bbox = draw_probe.textbbox((0, 0), "Ag", font=font)
            line_h = max(18, int(sample_bbox[3] - sample_bbox[1]) + 6)
            text_h = line_h * len(wrapped_lines)
            gap = 12
            canvas_h = pad + qr_h + gap + text_h + pad
            canvas = Image.new("RGB", (canvas_w, canvas_h), "white")
            canvas.paste(qr_img, ((canvas_w - qr_w) // 2, pad))
            draw = ImageDraw.Draw(canvas)
            y = pad + qr_h + gap
            for line in wrapped_lines:
                line_w = _text_width(line)
                x = max(pad, (canvas_w - line_w) // 2)
                draw.text((x, y), line, fill="black", font=font)
                y += line_h

            out = io.BytesIO()
            canvas.save(out, format="PNG")
            return out.getvalue()
        except Exception:
            return png_bytes

    def _parse_browser_name_version(self, ua_text):
        ua = (ua_text or "").strip()
        if not ua:
            return "Unknown", ""
        rules = [
            (r"Edg/([\d\.]+)", "Edge"),
            (r"OPR/([\d\.]+)", "Opera"),
            (r"CriOS/([\d\.]+)", "Chrome"),
            (r"Chrome/([\d\.]+)", "Chrome"),
            (r"FxiOS/([\d\.]+)", "Firefox"),
            (r"Firefox/([\d\.]+)", "Firefox"),
            (r"Version/([\d\.]+).*Safari/", "Safari"),
            (r"Safari/([\d\.]+)", "Safari"),
            (r"MSIE ([\d\.]+)", "Internet Explorer"),
            (r"Trident/.*rv:([\d\.]+)", "Internet Explorer"),
        ]
        for pattern, name in rules:
            m = re.search(pattern, ua, re.IGNORECASE)
            if m:
                return name, (m.group(1) or "").strip()
        return "Unknown", ""

    def _parse_os_name(self, ua_text):
        ua = (ua_text or "").lower()
        if not ua:
            return "Unknown"
        if "android" in ua:
            return "Android"
        if "iphone" in ua or "ipad" in ua or "ipod" in ua:
            return "iOS"
        if "windows" in ua:
            return "Windows"
        if "mac os x" in ua or "macintosh" in ua:
            return "macOS"
        if "linux" in ua:
            return "Linux"
        return "Unknown"

    def _parse_device_type(self, ua_text):
        ua = (ua_text or "").lower()
        if not ua:
            return "unknown"
        if "bot" in ua or "spider" in ua or "crawler" in ua:
            return "bot"
        if "ipad" in ua or "tablet" in ua:
            return "tablet"
        if "mobile" in ua or "iphone" in ua or "android" in ua:
            return "phone"
        if "windows" in ua or "macintosh" in ua or "linux" in ua:
            return "desktop"
        return "unknown"

    def _collect_client_env(self):
        ua = request.httprequest.headers.get("User-Agent", "") or ""
        browser_name, browser_version = self._parse_browser_name_version(ua)
        return {
            "device_type": self._parse_device_type(ua),
            "os_name": self._parse_os_name(ua),
            "browser_name": browser_name,
            "browser_version": browser_version,
        }

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

    def _decode_cache_get(self, cache_key):
        now = time.time()
        with self._decode_cache_lock:
            item = self._decode_result_cache.get(cache_key)
            if not item:
                return None
            if float(item.get("exp", 0.0)) < now:
                self._decode_result_cache.pop(cache_key, None)
                return None
            return item.get("resp")

    def _decode_cache_set(self, cache_key, response_json):
        now = time.time()
        with self._decode_cache_lock:
            # periodic lazy cleanup to keep cache bounded
            if len(self._decode_result_cache) >= self.DECODE_CACHE_MAX_ITEMS:
                dead_keys = [k for k, v in self._decode_result_cache.items() if float(v.get("exp", 0.0)) < now]
                for k in dead_keys[: max(1, len(dead_keys))]:
                    self._decode_result_cache.pop(k, None)
                if len(self._decode_result_cache) >= self.DECODE_CACHE_MAX_ITEMS:
                    # drop oldest-ish key if still full
                    first_key = next(iter(self._decode_result_cache.keys()), None)
                    if first_key:
                        self._decode_result_cache.pop(first_key, None)
            self._decode_result_cache[cache_key] = {
                "exp": now + self.DECODE_CACHE_TTL_SECONDS,
                "resp": response_json,
            }

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

    def _normalize_prefill_payload(self, form_values_multi):
        payload = {}
        for key, vals in (form_values_multi or {}).items():
            if not key:
                continue
            payload[key] = [((v or "").strip()) for v in (vals or []) if str(v).strip()]
        return payload

    def _encode_prefill(self, form_values_multi):
        payload = self._normalize_prefill_payload(form_values_multi)
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii")

    def _store_prefill_ref(self, form_values_multi):
        payload = self._normalize_prefill_payload(form_values_multi)
        if not payload:
            return ""

        now = int(time.time())
        bag = request.session.get(self.PREFILL_SESSION_KEY) or {}
        if not isinstance(bag, dict):
            bag = {}

        min_ts = now - self.PREFILL_SESSION_TTL_SECONDS
        cleaned = {
            key: item
            for key, item in bag.items()
            if isinstance(item, dict) and int(item.get("ts", 0)) >= min_ts and isinstance(item.get("data"), dict)
        }
        ref = secrets.token_urlsafe(12)
        cleaned[ref] = {"ts": now, "data": payload}

        if len(cleaned) > self.PREFILL_SESSION_MAX_ITEMS:
            ordered = sorted(cleaned.items(), key=lambda x: int(x[1].get("ts", 0)), reverse=True)
            cleaned = dict(ordered[: self.PREFILL_SESSION_MAX_ITEMS])

        request.session[self.PREFILL_SESSION_KEY] = cleaned
        return ref

    def _decode_prefill(self, token_text, prefill_ref=None):
        data = None

        ref = (prefill_ref or "").strip()
        if ref:
            bag = request.session.get(self.PREFILL_SESSION_KEY) or {}
            item = bag.get(ref) if isinstance(bag, dict) else None
            if isinstance(item, dict) and isinstance(item.get("data"), dict):
                data = item["data"]

        if data is None:
            token_text = (token_text or "").strip()
            if not token_text:
                return {}, {}, False
            try:
                raw = base64.urlsafe_b64decode(token_text.encode("ascii"))
                parsed = json.loads(raw.decode("utf-8"))
                if not isinstance(parsed, dict):
                    return {}, {}, False
                data = parsed
            except Exception:
                return {}, {}, False

        values_multi = {}
        values = {}
        for key, vals in data.items():
            if not isinstance(key, str) or not key:
                continue
            if not isinstance(vals, list):
                vals = [vals]
            normalized = [((v or "").strip()) for v in vals if str(v).strip()]
            values_multi[key] = normalized
            values[key] = normalized[0] if normalized else ""
        return values, values_multi, bool(values_multi)

    def _render_duplicate_page(self, form, token, posted_values_multi, duplicate_fields=None):
        msg = (form.duplicate_message or "The submitted unique field value already exists.").strip()
        prefill_ref = self._store_prefill_ref(posted_values_multi or {})
        return_url_keep = f"/mform/{token}?prefill_ref={prefill_ref}" if prefill_ref else f"/mform/{token}"
        return_url_clear = f"/mform/{token}"
        values = {
            "form": form,
            "duplicate_message": msg,
            "duplicate_fields": duplicate_fields or [],
            "return_url_keep": return_url_keep,
            "return_url_clear": return_url_clear,
        }
        return request.render("mobile_form_builder.mobile_form_duplicate_page", values)

    def _report_tz(self):
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
        return tz or (request.env.user.tz or "UTC")

    def _render_pdf_for_submissions(self, submissions, tz):
        report_ref = "mobile_form_builder.action_report_mobile_form_submission"
        pdf, _ = (
            request.env["ir.actions.report"]
            .with_context(tz=tz)
            .sudo()
            ._render_qweb_pdf(report_ref, submissions.ids)
        )
        return pdf

    def _build_single_docs_zip(self, submissions, filename_base, tz):
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for sub in submissions:
                pdf = self._render_pdf_for_submissions(sub, tz)
                zf.writestr(f"{filename_base}_{sub.name or sub.id}.pdf", pdf)
        payload = zip_buffer.getvalue()
        headers = [
            ("Content-Type", "application/zip"),
            ("Content-Length", len(payload)),
            ("Content-Disposition", content_disposition(f"{filename_base}.zip")),
        ]
        return request.make_response(payload, headers=headers)

    def _build_merged_zip_with_adaptive_chunks(self, submissions, filename_base, tz):
        chunk_size = min(self.PDF_BATCH_SIZE, len(submissions))
        while chunk_size >= 1:
            zip_buffer = io.BytesIO()
            ok = True
            try:
                with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                    part = 1
                    for offset in range(0, len(submissions), chunk_size):
                        chunk = submissions[offset : offset + chunk_size]
                        pdf = self._render_pdf_for_submissions(chunk, tz)
                        zf.writestr(f"{filename_base}_part_{part:03d}.pdf", pdf)
                        part += 1
            except Exception:
                ok = False
            if ok:
                payload = zip_buffer.getvalue()
                headers = [
                    ("Content-Type", "application/zip"),
                    ("Content-Length", len(payload)),
                    ("Content-Disposition", content_disposition(f"{filename_base}.zip")),
                ]
                return request.make_response(payload, headers=headers)
            chunk_size = chunk_size // 2

        # Last resort: generate one PDF per submission to avoid wkhtmltopdf memory blowups.
        return self._build_single_docs_zip(submissions, filename_base, tz)

    def _export_submissions_pdf_response(self, submissions, filename_base, mode=None):
        submissions = submissions.sorted("submit_date")
        if not submissions:
            return request.not_found()

        mode = (mode or self.PDF_MODE_MERGED).strip().lower()
        if mode not in (self.PDF_MODE_MERGED, self.PDF_MODE_SINGLE):
            mode = self.PDF_MODE_MERGED
        tz = self._report_tz()

        # Single-doc mode: one submission per PDF.
        if mode == self.PDF_MODE_SINGLE:
            if len(submissions) == 1:
                one = submissions[0]
                pdf = self._render_pdf_for_submissions(one, tz)
                headers = [
                    ("Content-Type", "application/pdf"),
                    ("Content-Length", len(pdf)),
                    ("Content-Disposition", content_disposition(f"{filename_base}_{one.name or one.id}.pdf")),
                ]
                return request.make_response(pdf, headers=headers)

            return self._build_single_docs_zip(submissions, filename_base, tz)

        # Merged mode: keep previous batching behavior.
        if len(submissions) <= self.PDF_ZIP_THRESHOLD:
            try:
                pdf = self._render_pdf_for_submissions(submissions, tz)
                headers = [
                    ("Content-Type", "application/pdf"),
                    ("Content-Length", len(pdf)),
                    ("Content-Disposition", content_disposition(f"{filename_base}.pdf")),
                ]
                return request.make_response(pdf, headers=headers)
            except Exception:
                pass

        try:
            return self._build_merged_zip_with_adaptive_chunks(submissions, filename_base, tz)
        except Exception:
            raise UserError(
                "PDF generation failed after fallback. Try 'Export PDF (Single Docs)' or reduce record count."
            )

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
        prefill_values, prefill_values_multi, has_prefill = self._decode_prefill(
            request.httprequest.args.get("prefill"),
            request.httprequest.args.get("prefill_ref"),
        )

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

                # Normalize email values to lowercase before storing.
                if is_visible and component.component_type == "email" and value:
                    value = value.lower()

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

                answer_payload[key] = value_text if (is_visible and component.component_type == "date") else value
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

            unique_key1 = ""
            unique_key2 = ""
            try:
                u1 = form.unique_component_id_1
                u2 = form.unique_component_id_2
                if u1 and u1.key:
                    unique_key1 = str(answer_payload.get(u1.key) or "").strip()
                if u2 and u2.key:
                    unique_key2 = str(answer_payload.get(u2.key) or "").strip()
            except Exception:
                unique_key1 = ""
                unique_key2 = ""

            duplicate_fields = []
            if unique_key1:
                existed = request.env["x_mobile.form.submission"].sudo().search_count(
                    [("form_id", "=", form.id), ("unique_key1_value", "=", unique_key1)]
                )
                if existed:
                    duplicate_fields.append(
                        {
                            "name": (form.unique_component_id_1.name or form.unique_component_id_1.key),
                            "value": unique_key1,
                        }
                    )

            if unique_key2:
                existed = request.env["x_mobile.form.submission"].sudo().search_count(
                    [("form_id", "=", form.id), ("unique_key2_value", "=", unique_key2)]
                )
                if existed:
                    duplicate_fields.append(
                        {
                            "name": (form.unique_component_id_2.name or form.unique_component_id_2.key),
                            "value": unique_key2,
                        }
                    )

            if duplicate_fields:
                response = self._render_duplicate_page(form, token, posted_values_multi, duplicate_fields=duplicate_fields)
                return self._set_client_cookie_if_needed(response, cookie_created, client_id)

            submission = request.env["x_mobile.form.submission"].sudo().create(
                {
                    "form_id": form.id,
                    "client_identifier": client_id,
                    "answer_json": json.dumps(answer_payload, ensure_ascii=False),
                    "line_ids": [(0, 0, line) for line in submission_vals],
                    "confirm_key1_value": confirm_key1,
                    "confirm_key2_value": confirm_key2,
                    "unique_key1_value": unique_key1,
                    "unique_key2_value": unique_key2,
                    **self._collect_client_env(),
                }
            )
            values = {"form": form, "submission": submission}
            response = request.render("mobile_form_builder.mobile_form_thanks", values)
            return self._set_client_cookie_if_needed(response, cookie_created, client_id)

        response = self._render_form_page(
            form,
            form_values=prefill_values,
            form_values_multi=prefill_values_multi,
            has_form_values=has_prefill,
        )
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
            qr_description = form.qr_description or ""
        except Exception:
            share_url = ""
            qr_description = ""

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
            png_bytes = self._compose_qr_with_description(png_bytes, qr_description)
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

        png_bytes = self._compose_qr_with_description(png_bytes, qr_description)
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

        mode = (kwargs.get("mode") or "").strip().lower()
        return self._export_submissions_pdf_response(form.submission_ids, f"{form.name}_submissions", mode=mode)

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

    @http.route(["/mform/export_selected_pdf"], type="http", auth="user")
    def export_selected_pdf(self, ids=None, **kwargs):
        id_list = []
        if ids:
            id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        submissions = request.env["x_mobile.form.submission"].browse(id_list).exists()
        if not submissions:
            return request.not_found()
        submissions.check_access_rights("read")
        submissions.check_access_rule("read")
        mode = (kwargs.get("mode") or "").strip().lower()
        return self._export_submissions_pdf_response(submissions, "selected_submissions", mode=mode)

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
        diag = bool(payload.get("diag"))
        if image_data.startswith("data:image"):
            parts = image_data.split(",", 1)
            image_data = parts[1] if len(parts) == 2 else ""
        if not image_data:
            return request.make_json_response({"ok": False, "reason": "empty"})
        if len(image_data) > 8 * 1024 * 1024:
            return request.make_json_response({"ok": False, "reason": "payload_too_large"})

        digest = hashlib.sha1(image_data.encode("ascii", errors="ignore")).hexdigest()
        cache_key = f"{digest}:{int(use_deep)}:{int(prefer_1d)}"
        cached = self._decode_cache_get(cache_key)
        if cached is not None:
            return request.make_json_response(cached)

        try:
            raw = base64.b64decode(image_data)
        except Exception:
            return request.make_json_response({"ok": False, "reason": "invalid_base64"})

        pyzbar_runtime_error = None
        pyzbar_symbol_count = 0
        zxing_runtime_error = None

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
                symbol_names.extend(["DATABAR_EXP", "PDF417", "QRCODE", "DATAMATRIX", "AZTEC", "MAXICODE"])
            symbols = [getattr(ZBarSymbol, name) for name in symbol_names if hasattr(ZBarSymbol, name)]
            pyzbar_symbol_count = len(symbols)
            for cand in candidates:
                found = zbar_decode(cand, symbols=symbols or None)
                if found:
                    value = (found[0].data or b"").decode("utf-8", errors="ignore").strip()
                    if value:
                        resp = {"ok": True, "value": value, "engine": "pyzbar"}
                        self._decode_cache_set(cache_key, resp)
                        return request.make_json_response(resp)

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
                            resp = {"ok": True, "value": value, "engine": "pyzbar_deep"}
                            self._decode_cache_set(cache_key, resp)
                            return request.make_json_response(resp)
        except Exception as exc:
            pyzbar_runtime_error = str(exc or "")

        # 2) Optional zxing-cpp fallback (only on deep attempts to keep fast response).
        if not use_deep:
            resp = {"ok": False, "reason": "not_found"}
            self._decode_cache_set(cache_key, resp)
            return request.make_json_response(resp)

        try:
            import zxingcpp
            from PIL import Image

            img = Image.open(io.BytesIO(raw)).convert("RGB")
            result = zxingcpp.read_barcode(img)
            if result and getattr(result, "text", ""):
                resp = {"ok": True, "value": result.text, "engine": "zxingcpp"}
                self._decode_cache_set(cache_key, resp)
                return request.make_json_response(resp)
        except Exception as exc:
            zxing_runtime_error = str(exc or "")

        # If decoder package/runtime is unavailable, return explicit hint.
        if pyzbar_runtime_error and ("zbar" in pyzbar_runtime_error.lower() or "pyzbar" in pyzbar_runtime_error.lower()):
            resp = {
                "ok": False,
                "reason": "decoder_unavailable",
                "message": pyzbar_runtime_error[:220],
            }
            self._decode_cache_set(cache_key, resp)
            return request.make_json_response(resp)

        try:
            import pyzbar  # noqa: F401
            decoder_available = True
        except Exception:
            decoder_available = False
        if not decoder_available:
            resp = {
                "ok": False,
                "reason": "decoder_unavailable",
                "message": "Server barcode decoder is not installed.",
            }
            self._decode_cache_set(cache_key, resp)
            return request.make_json_response(resp)
        if diag:
            resp = {
                "ok": False,
                "reason": "not_found",
                "diag": {
                    "pyzbar_error": pyzbar_runtime_error,
                    "pyzbar_symbol_count": pyzbar_symbol_count,
                    "zxing_error": zxing_runtime_error,
                    "use_deep": use_deep,
                    "prefer_1d": prefer_1d,
                },
            }
            self._decode_cache_set(cache_key, resp)
            return request.make_json_response(resp)
        resp = {"ok": False, "reason": "not_found"}
        self._decode_cache_set(cache_key, resp)
        return request.make_json_response(resp)
