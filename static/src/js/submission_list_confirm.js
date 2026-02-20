/** @odoo-module **/

import { listView } from "@web/views/list/list_view";
import { ListController } from "@web/views/list/list_controller";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { useService } from "@web/core/utils/hooks";
import { onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";

class MFormSubmissionListController extends ListController {
    setup() {
        super.setup();
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.codeInputRef = useRef("mformCodeInput");
        this.scanInputRef = useRef("mformScanInput");
        this.scanVideoRef = useRef("mformScanVideo");
        this.scanCanvasRef = useRef("mformScanCanvas");
        this.state = useState({
            code: "",
            scannerOpen: false,
        });
        this._scanStream = null;
        this._scanLoopRaf = null;
        this._scanTimer = null;
        this._scanBusy = false;
        this._scanAttemptCount = 0;
        this._decoderUnavailableNotified = false;
        this._autoConfirming = false;

        onMounted(() => {
            // Best-effort focus for scanner workflows.
            // Note: browsers may ignore autofocus depending on user gesture policy.
            try {
                this.codeInputRef.el && this.codeInputRef.el.focus();
            } catch {
                // ignore
            }
        });

        onWillUnmount(() => {
            this._stopScanner();
        });
    }

    _beep({ type }) {
        // WebAudio beep: no external assets, works in most modern browsers after a user gesture.
        // type: "success" | "error"
        try {
            const Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) {
                return;
            }
            const ctx = new Ctx();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = "sine";
            const now = ctx.currentTime;

            if (type === "success") {
                osc.frequency.setValueAtTime(880, now);
                osc.frequency.setValueAtTime(1320, now + 0.08);
                gain.gain.setValueAtTime(0.0001, now);
                gain.gain.exponentialRampToValueAtTime(0.12, now + 0.01);
                gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.16);
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.start(now);
                osc.stop(now + 0.18);
            } else {
                osc.frequency.setValueAtTime(220, now);
                gain.gain.setValueAtTime(0.0001, now);
                gain.gain.exponentialRampToValueAtTime(0.16, now + 0.01);
                gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.22);
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.start(now);
                osc.stop(now + 0.24);
            }

            // Ensure the context is closed soon after.
            window.setTimeout(() => {
                try {
                    ctx.close();
                } catch {
                    // ignore
                }
            }, 400);
        } catch {
            // ignore audio errors
        }
    }

    _getWizardContext() {
        // If the list is opened from a specific form, Odoo usually passes default_form_id.
        // Keep it generic otherwise (no form restriction).
        const ctx = { ...(this.props.context || {}) };
        return ctx;
    }

    async _applyWizard(action) {
        const code = (this.state.code || "").trim();
        if (!code) {
            this.notification.add("Please input a code.", { type: "warning" });
            return;
        }
        const ctx = this._getWizardContext();
        try {
            const [wizardId] = await this.orm.create(
                "x_mobile.form.submission.confirm.wizard",
                [
                    {
                        code,
                        action,
                    },
                ],
                { context: ctx }
            );
            const result = await this.orm.call(
                "x_mobile.form.submission.confirm.wizard",
                "action_apply_json",
                [[wizardId]],
                { context: ctx }
            );
            if (result && result.ok) {
                // Success: side notification only (no popup).
                this.notification.add(result.message || _t("Done."), {
                    title: result.title || _t("Done"),
                    type: result.type || "success",
                    sticky: false,
                });
                this._beep({ type: "success" });
                this.state.code = "";
                await this.actionService.doAction({ type: "ir.actions.client", tag: "reload" });
            } else {
                // Failure: popup dialog + error beep.
                const title = (result && result.title) || _t("Confirm Failed");
                const message = (result && result.message) || _t("Please check the code and try again.");
                this.dialog.add(AlertDialog, {
                    title,
                    body: message,
                });
                this._beep({ type: "error" });
            }
        } catch (e) {
            this._beep({ type: "error" });
            const msg =
                (e && e.message) ||
                (e && e.data && e.data.message) ||
                "Confirm failed.";
            this.dialog.add(AlertDialog, {
                title: _t("Confirm Failed"),
                body: msg,
            });
        } finally {
            try {
                this.codeInputRef.el && this.codeInputRef.el.focus();
            } catch {
                // ignore
            }
        }
    }

    async onMformConfirmByCode() {
        await this._applyWizard("confirm");
    }

    async onMformUnconfirmByCode() {
        await this._applyWizard("unconfirm");
    }

    async _decodeCodeFromImageFile(file) {
        // Server-side decode only.
        const dataUrl = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result || "");
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
        if (!dataUrl) {
            return "";
        }
        const payload = {
            image_data: dataUrl,
            deep: true,
            prefer_1d: false,
        };
        const res = await fetch("/mform/decode_barcode", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const json = await res.json();
        if (json && json.ok && json.value) {
            return { code: String(json.value).trim(), reason: "" };
        }
        if (json && json.reason === "decoder_unavailable") {
            throw new Error("decoder_unavailable");
        }
        return { code: "", reason: (json && json.reason) || "not_found" };
    }

    async _decodeCodeFromDataUrl(dataUrl, deep = false, prefer1d = false) {
        if (!dataUrl) {
            return { code: "", reason: "empty" };
        }
        const payload = {
            image_data: dataUrl,
            deep: !!deep,
            prefer_1d: !!prefer1d,
        };
        const res = await fetch("/mform/decode_barcode", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const json = await res.json();
        if (json && json.ok && json.value) {
            return { code: String(json.value).trim(), reason: "" };
        }
        if (json && json.reason === "decoder_unavailable") {
            throw new Error("decoder_unavailable");
        }
        return { code: "", reason: (json && json.reason) || "not_found" };
    }

    _stopScanner() {
        try {
            if (this._scanLoopRaf) {
                window.cancelAnimationFrame(this._scanLoopRaf);
            }
        } catch {
            // ignore
        }
        this._scanLoopRaf = null;
        if (this._scanTimer) {
            try {
                window.clearInterval(this._scanTimer);
            } catch {
                // ignore
            }
        }
        this._scanTimer = null;
        this._scanBusy = false;
        this._scanAttemptCount = 0;
        this._decoderUnavailableNotified = false;
        if (this._scanStream) {
            try {
                for (const track of this._scanStream.getTracks()) {
                    track.stop();
                }
            } catch {
                // ignore
            }
        }
        this._scanStream = null;
        try {
            if (this.scanVideoRef.el) {
                this.scanVideoRef.el.srcObject = null;
            }
        } catch {
            // ignore
        }
    }

    async _onCodeDetected(code) {
        const text = (code || "").trim();
        if (!text) {
            return;
        }
        this.state.code = text;
        this.notification.add(_t("Code scanned."), { type: "success" });
        this._beep({ type: "success" });
        this.state.scannerOpen = false;
        this._stopScanner();
        try {
            this.codeInputRef.el && this.codeInputRef.el.focus();
        } catch {
            // ignore
        }
        await this._autoConfirmScannedCode();
    }

    async _autoConfirmScannedCode() {
        if (this._autoConfirming) {
            return;
        }
        const code = (this.state.code || "").trim();
        if (!code) {
            return;
        }
        this._autoConfirming = true;
        try {
            await this.onMformConfirmByCode();
        } finally {
            this._autoConfirming = false;
        }
    }

    async onMformScanFileChange(ev) {
        const file = ev && ev.target && ev.target.files && ev.target.files[0];
        if (!file) {
            return;
        }
        try {
            const result = await this._decodeCodeFromImageFile(file);
            const code = result && result.code ? result.code : "";
            if (!code) {
                this.notification.add(_t("No barcode/QR code detected."), { type: "warning" });
                this._beep({ type: "error" });
                return;
            }
            this.state.code = code;
            this.notification.add(_t("Code scanned."), { type: "success" });
            this._beep({ type: "success" });
            try {
                this.codeInputRef.el && this.codeInputRef.el.focus();
            } catch {
                // ignore
            }
            await this._autoConfirmScannedCode();
        } catch {
            this.notification.add(_t("Scan failed. Please try again."), { type: "danger" });
            this._beep({ type: "error" });
        }
    }

    async onMformCloseScanner() {
        this.state.scannerOpen = false;
        this._stopScanner();
    }

    async _scanOnceViaServer() {
        if (!this.scanVideoRef.el || !this.scanCanvasRef.el) {
            return;
        }
        if (this._scanBusy) {
            return;
        }
        this._scanBusy = true;
        try {
            const video = this.scanVideoRef.el;
            const canvas = this.scanCanvasRef.el;
            if (!video.videoWidth || !video.videoHeight) {
                return;
            }
            const w = video.videoWidth || 1280;
            const h = video.videoHeight || 720;
            this._scanAttemptCount += 1;
            const profile = this._scanAttemptCount % 4;
            let srcX = 0;
            let srcY = 0;
            let srcW = w;
            let srcH = h;
            if (profile === 1) {
                const zoom = 2.0;
                srcW = Math.max(1, Math.floor(w / zoom));
                srcH = Math.max(1, Math.floor(h / zoom));
                srcX = Math.max(0, Math.floor((w - srcW) / 2));
                srcY = Math.max(0, Math.floor((h - srcH) / 2));
            } else if (profile === 2) {
                srcW = w;
                srcH = Math.max(1, Math.floor(h * 0.46));
                srcX = 0;
                srcY = Math.max(0, Math.floor((h - srcH) / 2));
            } else if (profile === 3) {
                srcW = Math.max(1, Math.floor(w * 0.72));
                srcH = h;
                srcX = Math.max(0, Math.floor((w - srcW) / 2));
                srcY = 0;
            }
            const outW = Math.max(900, Math.min(1920, srcW));
            const outH = Math.max(320, Math.min(1080, srcH));
            canvas.width = outW;
            canvas.height = outH;
            const ctx = canvas.getContext("2d");
            ctx.imageSmoothingEnabled = false;
            ctx.drawImage(video, srcX, srcY, srcW, srcH, 0, 0, outW, outH);
            const dataUrl = canvas.toDataURL("image/jpeg", 0.82);
            const useDeep = this._scanAttemptCount % 4 === 0;
            const prefer1d = this._scanAttemptCount < 12;
            const result = await this._decodeCodeFromDataUrl(dataUrl, useDeep, prefer1d);
            const code = result && result.code ? result.code : "";
            if (code) {
                await this._onCodeDetected(code);
                return;
            }
            if (result && result.reason === "payload_too_large") {
                this.notification.add(_t("Captured image too large. Move closer and keep barcode centered."), {
                    type: "warning",
                });
            }
        } catch (err) {
            if (err && err.message === "decoder_unavailable" && !this._decoderUnavailableNotified) {
                this._decoderUnavailableNotified = true;
                this.notification.add(_t("Server decoder unavailable. Please install pyzbar/zbar on server."), {
                    type: "danger",
                });
                this._beep({ type: "error" });
                this.state.scannerOpen = false;
                this._stopScanner();
                return;
            }
            // ignore per-frame decode errors while auto scanning
        } finally {
            this._scanBusy = false;
        }
    }

    async onMformScanClick() {
        // Prefer live rear-camera scanning on mobile; fallback to file capture.
        try {
            this.state.code = "";
            try {
                if (this.codeInputRef.el) {
                    this.codeInputRef.el.value = "";
                }
            } catch {
                // ignore
            }
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                throw new Error("getUserMedia unavailable");
            }
            this.state.scannerOpen = true;
            await Promise.resolve();
            const stream = await navigator.mediaDevices.getUserMedia({
                video: {
                    facingMode: { ideal: "environment" },
                    width: { ideal: 1920 },
                    height: { ideal: 1080 },
                },
                audio: false,
            });
            this._scanStream = stream;
            this._decoderUnavailableNotified = false;
            try {
                const track = stream.getVideoTracks()[0];
                if (track && typeof track.getCapabilities === "function") {
                    const caps = track.getCapabilities() || {};
                    const advanced = {};
                    if (Array.isArray(caps.focusMode) && caps.focusMode.includes("continuous")) {
                        advanced.focusMode = "continuous";
                    }
                    if (caps.zoom && typeof caps.zoom === "object") {
                        const minZoom = Number(caps.zoom.min);
                        const maxZoom = Number(caps.zoom.max);
                        if (Number.isFinite(minZoom) && Number.isFinite(maxZoom) && maxZoom >= minZoom) {
                            advanced.zoom = Math.max(minZoom, Math.min(maxZoom, 2.0));
                        }
                    }
                    if (Object.keys(advanced).length) {
                        await track.applyConstraints({ advanced: [advanced] });
                    }
                }
            } catch {
                // ignore capability tuning failures
            }
            if (this.scanVideoRef.el) {
                this.scanVideoRef.el.srcObject = stream;
                await this.scanVideoRef.el.play();
            }
            this._scanTimer = window.setInterval(() => {
                if (!this.state.scannerOpen) {
                    return;
                }
                this._scanOnceViaServer();
            }, 220);
        } catch {
            this.state.scannerOpen = false;
            this._stopScanner();
            // Fallback: trigger native camera/photo picker.
            try {
                if (this.scanInputRef.el) {
                    this.scanInputRef.el.value = "";
                    this.scanInputRef.el.click();
                }
            } catch {
                // ignore
            }
        }
    }

    onMformCodeKeydown(ev) {
        // Owl in Odoo 19 doesn't support `t-on-keydown.enter`, so handle Enter manually.
        if (!ev || ev.isComposing) {
            return;
        }
        if (ev.key === "Enter") {
            ev.preventDefault();
            ev.stopPropagation();
            this.onMformConfirmByCode();
        }
    }

    onMformCodeInput(ev) {
        if (!ev || !ev.target) {
            return;
        }
        this.state.code = ev.target.value || "";
    }
}

export const mformSubmissionListView = {
    ...listView,
    Controller: MFormSubmissionListController,
    // Don't render custom buttons in the left control-panel button area.
    // We'll inject them next to the SearchBar via our custom controller template.
    buttonTemplate: "web.ListView.Buttons",
};

// Use a custom template so we can position the confirm box on the right side (near SearchBar).
MFormSubmissionListController.template = "mobile_form_builder.MFormSubmissionListView";

registry.category("views").add("mform_submission_list", mformSubmissionListView);
