(function () {
    "use strict";

    function getOrCreateErrorEl(field) {
        const holder = field.closest(".mform-field") || field.parentElement;
        if (!holder) {
            return null;
        }
        let errorEl = holder.querySelector(".mform-field-error");
        if (!errorEl) {
            errorEl = document.createElement("div");
            errorEl.className = "mform-field-error";
            holder.appendChild(errorEl);
        }
        return errorEl;
    }

    function updateFieldErrorUI(field) {
        const errorEl = getOrCreateErrorEl(field);
        const valid = field.checkValidity();
        if (valid) {
            field.classList.remove("mform-invalid");
            if (errorEl) {
                errorEl.textContent = "";
                errorEl.style.display = "none";
            }
            return true;
        }
        field.classList.add("mform-invalid");
        if (errorEl) {
            errorEl.textContent = field.validationMessage || "Invalid input.";
            errorEl.style.display = "block";
        }
        return false;
    }

    function initInputRules() {
        const fields = document.querySelectorAll("input[data-case-mode], textarea[data-case-mode]");
        fields.forEach((field) => {
            field.addEventListener("input", () => {
                let value = field.value || "";
                const caseMode = field.dataset.caseMode || "none";
                const onlyDigits = field.dataset.onlyDigits === "1";
                const mode = field.dataset.validationMode || "none";
                const customRegex = field.dataset.customRegex || "";

                if (caseMode === "upper") {
                    value = value.toUpperCase();
                } else if (caseMode === "lower") {
                    value = value.toLowerCase();
                }
                if (onlyDigits) {
                    value = value.replace(/\D+/g, "");
                }
                if (value !== field.value) {
                    field.value = value;
                }

                let isValid = true;
                if (value) {
                    if (mode === "alpha") {
                        isValid = /^[A-Za-z]+$/.test(value);
                    } else if (mode === "alnum") {
                        isValid = /^[A-Za-z0-9]+$/.test(value);
                    } else if (mode === "phone") {
                        isValid = /^1[3-9]\d{9}$/.test(value);
                    } else if (mode === "email") {
                        isValid = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(value);
                    } else if (mode === "custom_regex" && customRegex) {
                        try {
                            isValid = new RegExp(`^(?:${customRegex})$`).test(value);
                        } catch (e) {
                            isValid = true;
                        }
                    }
                }
                field.setCustomValidity(isValid ? "" : "Input format is invalid.");
                updateFieldErrorUI(field);
            });
            field.addEventListener("blur", () => updateFieldErrorUI(field));
        });

        // Generic validation UI for fields not covered by data-case-mode
        const allFields = document.querySelectorAll(".mform-form input, .mform-form select, .mform-form textarea");
        allFields.forEach((field) => {
            if (field.type === "hidden" || field.type === "button" || field.type === "submit") {
                return;
            }
            field.addEventListener("input", () => updateFieldErrorUI(field));
            field.addEventListener("change", () => updateFieldErrorUI(field));
            field.addEventListener("blur", () => updateFieldErrorUI(field));
        });

        const form = document.querySelector(".mform-form");
        if (form) {
            form.addEventListener("submit", (evt) => {
                const candidates = form.querySelectorAll("input, select, textarea");
                let firstInvalid = null;
                candidates.forEach((field) => {
                    if (field.type === "hidden" || field.type === "button" || field.type === "submit") {
                        return;
                    }
                    const ok = updateFieldErrorUI(field);
                    if (!ok && !firstInvalid) {
                        firstInvalid = field;
                    }
                });
                if (firstInvalid) {
                    evt.preventDefault();
                    firstInvalid.focus();
                    if (typeof firstInvalid.reportValidity === "function") {
                        firstInvalid.reportValidity();
                    }
                    return;
                }

                const ageFields = form.querySelectorAll("input.mform-age-auto");
                const warnings = [];
                ageFields.forEach((field) => {
                    const policy = evaluateAgePolicy(field);
                    field.dataset.ageWarnMessage = policy.warn || "";
                    if (policy.block) {
                        field.setCustomValidity(policy.message || "Age is not allowed.");
                        if (!firstInvalid) {
                            firstInvalid = field;
                        }
                    } else {
                        field.setCustomValidity("");
                        if (policy.warn) {
                            warnings.push(policy.warn);
                        }
                    }
                    updateFieldErrorUI(field);
                });
                if (firstInvalid) {
                    evt.preventDefault();
                    firstInvalid.focus();
                    if (typeof firstInvalid.reportValidity === "function") {
                        firstInvalid.reportValidity();
                    }
                    return;
                }
                if (warnings.length) {
                    const uniqueWarnings = Array.from(new Set(warnings));
                    const ok = window.confirm(uniqueWarnings.join("\n") + "\n\nContinue submission?");
                    if (!ok) {
                        evt.preventDefault();
                    }
                }
                if (evt.defaultPrevented) {
                    return;
                }
                const cascades = form.querySelectorAll(".mform-cascade");
                let cascadeInvalid = null;
                cascades.forEach((wrap) => {
                    const required = (wrap.dataset.required || "0") === "1";
                    if (!required) {
                        return;
                    }
                    const hidden = wrap.querySelector(".mform-cascade-value");
                    if (hidden && (hidden.value || "").trim()) {
                        return;
                    }
                    const firstSelect = wrap.querySelector("select.mform-cascade-select");
                    if (!firstSelect) {
                        return;
                    }
                    firstSelect.setCustomValidity("Please finish the cascading selection.");
                    updateFieldErrorUI(firstSelect);
                    if (!cascadeInvalid) {
                        cascadeInvalid = firstSelect;
                    }
                });
                if (cascadeInvalid) {
                    evt.preventDefault();
                    cascadeInvalid.focus();
                    if (typeof cascadeInvalid.reportValidity === "function") {
                        cascadeInvalid.reportValidity();
                    }
                }
            });
        }
    }

    function initFormattedNumberInputs() {
        const fields = document.querySelectorAll("input[data-number-format]");
        fields.forEach((field) => {
            const formatValue = () => {
                const pattern = (field.dataset.numberFormat || "").trim();
                if (!pattern) {
                    return;
                }
                const digits = (field.value || "").replace(/\D+/g, "");
                const needed = (pattern.match(/0/g) || []).length;
                let clipped = digits.slice(0, needed);
                let out = "";
                let idx = 0;
                for (let i = 0; i < pattern.length; i += 1) {
                    const ch = pattern[i];
                    if (ch === "0") {
                        if (idx < clipped.length) {
                            out += clipped[idx];
                            idx += 1;
                        } else {
                            break;
                        }
                    } else if (idx > 0 && idx <= clipped.length) {
                        out += ch;
                    }
                }
                field.value = out;
                const valid = clipped.length === 0 || clipped.length === needed;
                field.setCustomValidity(valid ? "" : `Format must match ${pattern}`);
                updateFieldErrorUI(field);
            };
            field.addEventListener("input", formatValue);
            field.addEventListener("blur", formatValue);
            formatValue();
        });
    }

    function initDatePickerOnly() {
        const fields = document.querySelectorAll("input[type='date'][data-date-picker-only='1']");
        fields.forEach((field) => {
            const blockTyping = (evt) => {
                evt.preventDefault();
            };
            ["keydown", "keypress", "beforeinput", "paste", "drop"].forEach((evtName) => {
                field.addEventListener(evtName, blockTyping);
            });
            const openPicker = () => {
                if (typeof field.showPicker === "function") {
                    try {
                        field.showPicker();
                    } catch (e) {
                        // ignore unsupported runtime errors
                    }
                }
            };
            field.addEventListener("focus", openPicker);
            field.addEventListener("click", openPicker);
        });
    }

    function initCascadingOptions() {
        const wrappers = document.querySelectorAll(".mform-cascade[data-options-json]");
        wrappers.forEach((wrapper) => {
            const hidden = wrapper.querySelector(".mform-cascade-value");
            const levelsHost = wrapper.querySelector(".mform-cascade-levels");
            if (!hidden || !levelsHost) {
                return;
            }
            let rawOptions = [];
            try {
                rawOptions = JSON.parse(wrapper.dataset.optionsJson || "[]");
            } catch (e) {
                rawOptions = [];
            }
            if (!Array.isArray(rawOptions) || !rawOptions.length) {
                hidden.value = "";
                return;
            }

            const byParent = new Map();
            const byId = new Map();
            rawOptions.forEach((item) => {
                const id = Number(item.id) || 0;
                const parentId = Number(item.parent_id) || 0;
                const name = (item.name || "").toString();
                if (!id || !name) {
                    return;
                }
                byId.set(id, { id, parentId, name });
                const current = byParent.get(parentId) || [];
                current.push({ id, parentId, name });
                byParent.set(parentId, current);
            });

            const childrenOf = (parentId) => (byParent.get(parentId) || []).slice();
            const clearFromLevel = (level) => {
                levelsHost.querySelectorAll(".mform-cascade-level").forEach((node) => {
                    const nodeLevel = Number(node.dataset.level || 0);
                    if (nodeLevel >= level) {
                        node.remove();
                    }
                });
            };

            const renderLevel = (parentId, level) => {
                const children = childrenOf(parentId);
                if (!children.length) {
                    return;
                }
                const levelWrap = document.createElement("div");
                levelWrap.className = "mform-cascade-level";
                levelWrap.dataset.level = String(level);

                const select = document.createElement("select");
                select.className = "form-select mform-cascade-select";
                select.dataset.level = String(level);
                const blank = document.createElement("option");
                blank.value = "";
                blank.textContent = "Please select";
                select.appendChild(blank);
                children.forEach((opt) => {
                    const optEl = document.createElement("option");
                    optEl.value = String(opt.id);
                    optEl.textContent = opt.name;
                    select.appendChild(optEl);
                });

                select.addEventListener("change", () => {
                    select.setCustomValidity("");
                    clearFromLevel(level + 1);
                    hidden.value = "";
                    hidden.dispatchEvent(new Event("change", { bubbles: true }));
                    const selectedId = Number(select.value || 0);
                    if (!selectedId) {
                        return;
                    }
                    const next = childrenOf(selectedId);
                    if (next.length) {
                        renderLevel(selectedId, level + 1);
                    } else {
                        const chosen = byId.get(selectedId);
                        hidden.value = chosen ? chosen.name : "";
                        hidden.dispatchEvent(new Event("change", { bubbles: true }));
                    }
                });
                levelWrap.appendChild(select);
                levelsHost.appendChild(levelWrap);
            };

            levelsHost.innerHTML = "";
            hidden.value = "";
            renderLevel(0, 0);
        });
    }

    function parseVisibilityValues(rawValue) {
        const text = (rawValue || "").toString().replace(/\r\n/g, "\n");
        const chunks = [];
        text.split("\n").forEach((line) => {
            line.split(/[,，;；|]+/).forEach((part) => chunks.push(part));
        });
        return chunks.map((x) => x.trim()).filter(Boolean);
    }

    function clearFieldWrapperValues(wrapper) {
        const fields = wrapper.querySelectorAll("input, select, textarea");
        fields.forEach((field) => {
            if (field.type === "button" || field.type === "submit") {
                return;
            }
            if (field.type === "radio" || field.type === "checkbox") {
                field.checked = false;
            } else if (field.tagName === "SELECT") {
                field.selectedIndex = 0;
            } else if (field.type === "file") {
                field.value = "";
            } else {
                field.value = "";
            }
            field.setCustomValidity("");
            updateFieldErrorUI(field);
        });
        const signWrap = wrapper.querySelector(".mform-sign-wrap");
        if (signWrap) {
            const canvas = signWrap.querySelector(".mform-sign-canvas");
            const hidden = signWrap.querySelector(".mform-sign-input");
            if (canvas) {
                const ctx = canvas.getContext("2d");
                if (ctx) {
                    ctx.clearRect(0, 0, canvas.width, canvas.height);
                }
            }
            if (hidden) {
                hidden.value = "";
                hidden.dispatchEvent(new Event("change", { bubbles: true }));
            }
        }
    }

    function initVisibilityRules() {
        const wrappers = document.querySelectorAll(".mform-field[data-vis-enabled='1']");
        if (!wrappers.length) {
            return;
        }

        const dependencyMap = new Map();
        wrappers.forEach((wrapper) => {
            const sourceKey = (wrapper.dataset.visSourceKey || "").trim();
            if (!sourceKey) {
                return;
            }
            const list = dependencyMap.get(sourceKey) || [];
            list.push(wrapper);
            dependencyMap.set(sourceKey, list);
        });

        const readSourceValues = (sourceKey) => {
            const fields = document.querySelectorAll(`.mform-form [name="${sourceKey}"]`);
            const values = [];
            fields.forEach((field) => {
                if (field.type === "checkbox" || field.type === "radio") {
                    if (field.checked) {
                        values.push((field.value || "").trim());
                    }
                } else {
                    const v = (field.value || "").trim();
                    if (v) {
                        values.push(v);
                    }
                }
            });
            return values;
        };

        const setWrapperVisible = (wrapper, isVisible) => {
            wrapper.style.display = isVisible ? "" : "none";
            const fields = wrapper.querySelectorAll("input, select, textarea");
            fields.forEach((field) => {
                if (field.type === "button" || field.type === "submit") {
                    return;
                }
                if (!Object.prototype.hasOwnProperty.call(field.dataset, "requiredOriginal")) {
                    field.dataset.requiredOriginal = field.required ? "1" : "0";
                }
                if (isVisible) {
                    if (field.dataset.requiredOriginal === "1") {
                        field.required = true;
                    }
                } else {
                    field.required = false;
                }
                field.setCustomValidity("");
                updateFieldErrorUI(field);
            });
            if (!isVisible) {
                clearFieldWrapperValues(wrapper);
            }
        };

        const evaluateWrapper = (wrapper) => {
            const sourceKey = (wrapper.dataset.visSourceKey || "").trim();
            const mode = (wrapper.dataset.visMode || "show_if_match").trim();
            const expected = new Set(parseVisibilityValues(wrapper.dataset.visValues || ""));
            if (!sourceKey || !expected.size) {
                setWrapperVisible(wrapper, true);
                return;
            }
            const sourceValues = readSourceValues(sourceKey);
            const matched = sourceValues.some((v) => expected.has(v));
            const visible = mode === "hide_if_match" ? !matched : matched;
            setWrapperVisible(wrapper, visible);
        };

        const refreshBySource = (sourceKey) => {
            const list = dependencyMap.get(sourceKey) || [];
            list.forEach((wrapper) => evaluateWrapper(wrapper));
        };

        dependencyMap.forEach((_, sourceKey) => {
            const fields = document.querySelectorAll(`.mform-form [name="${sourceKey}"]`);
            fields.forEach((field) => {
                ["change", "input"].forEach((evtName) =>
                    field.addEventListener(evtName, () => refreshBySource(sourceKey))
                );
            });
            refreshBySource(sourceKey);
        });
    }

    function toOptionalInt(rawValue) {
        const txt = (rawValue || "").toString().trim();
        if (!txt) {
            return null;
        }
        const n = Number(txt);
        if (!Number.isFinite(n)) {
            return null;
        }
        return Math.trunc(n);
    }

    function evaluateAgePolicy(field) {
        const ageNum = Number.parseInt((field.value || "0").trim(), 10) || 0;
        const min = toOptionalInt(field.dataset.ageMin);
        const minAction = (field.dataset.ageMinAction || "none").trim();
        const minMessage = (field.dataset.ageMinMessage || "").trim() || "Age is below the minimum requirement.";
        const max = toOptionalInt(field.dataset.ageMax);
        const maxAction = (field.dataset.ageMaxAction || "none").trim();
        const maxMessage = (field.dataset.ageMaxMessage || "").trim() || "Age is above the maximum requirement.";

        if (min !== null && minAction !== "none" && ageNum < min) {
            if (minAction === "block") {
                return { block: true, message: minMessage, warn: "" };
            }
            return { block: false, message: "", warn: minMessage };
        }
        if (max !== null && maxAction !== "none" && ageNum > max) {
            if (maxAction === "block") {
                return { block: true, message: maxMessage, warn: "" };
            }
            return { block: false, message: "", warn: maxMessage };
        }
        return { block: false, message: "", warn: "" };
    }

    function computeAgeFromDateString(dateValue) {
        if (!dateValue) {
            return "0";
        }
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateValue);
        if (!m) {
            return "0";
        }
        const year = Number(m[1]);
        const month = Number(m[2]);
        const day = Number(m[3]);
        if (!year || !month || !day) {
            return "0";
        }
        const today = new Date();
        let age = today.getFullYear() - year;
        const currentMonth = today.getMonth() + 1;
        const currentDay = today.getDate();
        if (currentMonth < month || (currentMonth === month && currentDay < day)) {
            age -= 1;
        }
        if (age < 0) {
            age = 0;
        }
        return String(age);
    }

    function initAgeAutoFields() {
        const fields = document.querySelectorAll("input.mform-age-auto[data-age-source-key]");
        fields.forEach((field) => {
            const sourceKey = (field.dataset.ageSourceKey || "").trim();
            if (!sourceKey) {
                field.value = "0";
                return;
            }
            const source = document.querySelector(`.mform-form input[name="${sourceKey}"]`);
            if (!source) {
                field.value = "0";
                return;
            }
            const syncAge = () => {
                field.value = computeAgeFromDateString(source.value || "");
                const policy = evaluateAgePolicy(field);
                field.dataset.ageWarnMessage = policy.warn || "";
                field.setCustomValidity(policy.block ? policy.message : "");
                updateFieldErrorUI(field);
            };
            ["input", "change", "blur"].forEach((evtName) => source.addEventListener(evtName, syncAge));
            ["keydown", "paste", "drop", "beforeinput"].forEach((evtName) => {
                field.addEventListener(evtName, (evt) => evt.preventDefault());
            });
            syncAge();
        });
    }

    function initSignaturePads() {
        const wraps = document.querySelectorAll(".mform-sign-wrap");
        wraps.forEach((wrap) => {
            const canvas = wrap.querySelector(".mform-sign-canvas");
            const input = wrap.querySelector(".mform-sign-input");
            const clearBtn = wrap.querySelector(".mform-sign-clear");
            const ctx = canvas.getContext("2d");
            if (!ctx) {
                return;
            }
            ctx.lineWidth = 2.2;
            ctx.lineCap = "round";
            ctx.lineJoin = "round";
            ctx.strokeStyle = "#1f2d3d";
            const resizeCanvas = () => {
                const ratio = Math.max(window.devicePixelRatio || 1, 1);
                const width = Math.max(canvas.clientWidth, 280);
                const height = 160;
                canvas.width = Math.floor(width * ratio);
                canvas.height = Math.floor(height * ratio);
                ctx.setTransform(1, 0, 0, 1, 0, 0);
                ctx.scale(ratio, ratio);
                ctx.lineWidth = 2.2;
                ctx.lineCap = "round";
                ctx.lineJoin = "round";
            };
            resizeCanvas();
            window.addEventListener("resize", resizeCanvas);

            let drawing = false;
            let dirty = false;
            let activePointerId = null;
            let usingPointer = false;

            const toPoint = (evt, touch) => {
                const rect = canvas.getBoundingClientRect();
                const point = touch || evt;
                const clientX = point.clientX;
                const clientY = point.clientY;
                return { x: clientX - rect.left, y: clientY - rect.top };
            };

            const start = (evt, touch) => {
                drawing = true;
                dirty = true;
                const p = toPoint(evt, touch);
                ctx.beginPath();
                ctx.moveTo(p.x, p.y);
                if (evt && evt.cancelable) {
                    evt.preventDefault();
                }
            };

            const move = (evt, touch) => {
                if (!drawing) {
                    return;
                }
                const p = toPoint(evt, touch);
                ctx.lineTo(p.x, p.y);
                ctx.stroke();
                if (evt && evt.cancelable) {
                    evt.preventDefault();
                }
            };

            const end = () => {
                drawing = false;
                if (dirty) {
                    input.value = canvas.toDataURL("image/png");
                }
            };

            if (window.PointerEvent) {
                canvas.addEventListener("pointerdown", (evt) => {
                    usingPointer = true;
                    activePointerId = evt.pointerId;
                    try {
                        canvas.setPointerCapture(evt.pointerId);
                    } catch (e) {
                        // ignore
                    }
                    start(evt);
                });
                canvas.addEventListener("pointermove", (evt) => {
                    if (activePointerId !== null && evt.pointerId !== activePointerId) {
                        return;
                    }
                    move(evt);
                });
                const pointerEnd = (evt) => {
                    if (activePointerId !== null && evt.pointerId !== activePointerId) {
                        return;
                    }
                    end();
                    activePointerId = null;
                };
                canvas.addEventListener("pointerup", pointerEnd);
                canvas.addEventListener("pointercancel", pointerEnd);
                canvas.addEventListener("pointerleave", pointerEnd);
            }

            // Touch fallback for webviews where pointer events are incomplete.
            canvas.addEventListener(
                "touchstart",
                (evt) => {
                    if (usingPointer) {
                        return;
                    }
                    if (evt.touches && evt.touches[0]) {
                        start(evt, evt.touches[0]);
                    }
                },
                { passive: false }
            );
            canvas.addEventListener(
                "touchmove",
                (evt) => {
                    if (usingPointer) {
                        return;
                    }
                    if (evt.touches && evt.touches[0]) {
                        move(evt, evt.touches[0]);
                    }
                },
                { passive: false }
            );
            window.addEventListener(
                "touchend",
                () => {
                    if (usingPointer) {
                        return;
                    }
                    end();
                },
                { passive: false }
            );

            // Mouse fallback
            canvas.addEventListener("mousedown", (evt) => {
                if (usingPointer) {
                    return;
                }
                start(evt);
            });
            canvas.addEventListener("mousemove", (evt) => {
                if (usingPointer) {
                    return;
                }
                move(evt);
            });
            window.addEventListener("mouseup", () => {
                if (usingPointer) {
                    return;
                }
                end();
            });

            clearBtn.addEventListener("click", () => {
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                input.value = "";
                dirty = false;
            });
        });
    }

    async function initBarcodeScanner() {
        const wrappers = document.querySelectorAll(".mform-barcode");
        const canUseCamera =
            !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) &&
            (window.isSecureContext || location.hostname === "localhost" || location.hostname === "127.0.0.1");
        const i18n = {
            starting: "Starting camera...",
            openCamera: "Camera opened. Align barcode/QR with the red lines.",
            decoding: "Decoding on server...",
            success: "Scan successful.",
            timeout: "No barcode detected in 30s. Reposition and try again.",
            noCameraApi: "Camera API is not available in this browser.",
            cameraFail: "Unable to access camera. Check permission settings.",
            decoderMissing: "Server decoder unavailable. Install pyzbar/zbar on server.",
            payloadLarge: "Capture frame too large for server decode.",
            placeholder: "Scan required (manual input disabled)",
            button: "Scan Barcode or QR",
            tip: "Manual barcode entry is disabled. If a permission dialog appears, tap Allow and align with the red lines.",
        };

        wrappers.forEach((wrapper) => {
            const button = wrapper.querySelector(".mform-start-scan");
            const input = wrapper.querySelector(".mform-barcode-input");
            const cameraTip = wrapper.querySelector(".mform-camera-tip");
            const statusEl = wrapper.querySelector(".mform-scan-status");
            const modal = document.getElementById("mform-scan-modal");
            const modalVideo = modal ? modal.querySelector(".mform-scan-video") : null;
            const modalAimLine = modal ? modal.querySelector(".mform-aim-line") : null;
            const modalAimLineVert = modal ? modal.querySelector(".mform-aim-line-vert") : null;
            const modalTip = modal ? modal.querySelector(".mform-scan-tip") : null;
            const modalStatus = modal ? modal.querySelector(".mform-scan-status-modal") : null;
            const modalClose = modal ? modal.querySelector(".mform-scan-close") : null;
            const msg = {
                starting: i18n.starting,
                openCamera: i18n.openCamera,
                decoding: i18n.decoding,
                success: i18n.success,
                timeout: i18n.timeout,
                noCameraApi: i18n.noCameraApi,
                cameraFail: i18n.cameraFail,
                decoderMissing: i18n.decoderMissing,
                payloadLarge: i18n.payloadLarge,
            };
            let stream = null;
            let scanning = false;
            let lastStatus = "";
            let startedAt = 0;
            let lastServerTryAt = 0;
            let tryIndex = 0;
            let serverPhaseStartedAt = 0;

            if (!button || !modal || !modalVideo || !modalClose) {
                return;
            }

            input.setAttribute("readonly", "readonly");
            input.setAttribute("inputmode", "none");
            input.setAttribute("autocomplete", "off");
            input.setAttribute("placeholder", i18n.placeholder);
            button.textContent = i18n.button;
            if (cameraTip) {
                cameraTip.textContent = i18n.tip;
            }
            if (modalTip) {
                modalTip.textContent = i18n.tip;
            }
            ["keydown", "paste", "drop", "beforeinput"].forEach((evtName) => {
                input.addEventListener(evtName, (evt) => evt.preventDefault());
            });

            const setStatus = (text) => {
                if (text === lastStatus) {
                    return;
                }
                lastStatus = text;
                if (statusEl) {
                    statusEl.textContent = text || "";
                }
                if (modalStatus) {
                    modalStatus.textContent = text || "";
                }
            };

            const stopScan = () => {
                if (stream) {
                    stream.getTracks().forEach((t) => t.stop());
                    stream = null;
                }
                modalVideo.srcObject = null;
                if (modalAimLine) {
                    modalAimLine.style.display = "none";
                }
                if (modalAimLineVert) {
                    modalAimLineVert.style.display = "none";
                }
                scanning = false;
                startedAt = 0;
                lastServerTryAt = 0;
                tryIndex = 0;
                serverPhaseStartedAt = 0;
                if (cameraTip) {
                    cameraTip.classList.remove("is-active");
                }
                modal.classList.remove("is-open");
            };

            const requestCameraStream = async () => {
                try {
                    return await navigator.mediaDevices.getUserMedia({
                        video: {
                            facingMode: { ideal: "environment" },
                            width: { ideal: 1920 },
                            height: { ideal: 1080 },
                        },
                        audio: false,
                    });
                } catch (e) {
                    return navigator.mediaDevices.getUserMedia({
                        video: { width: { ideal: 1280 }, height: { ideal: 720 } },
                        audio: false,
                    });
                }
            };

            const decodeOnServer = async (scanCanvas, scanCtx, prefer1d) => {
                const now = Date.now();
                if (now - lastServerTryAt < 140) {
                    return "";
                }
                lastServerTryAt = now;
                const vw = modalVideo.videoWidth || 0;
                const vh = modalVideo.videoHeight || 0;
                if (!vw || !vh || !scanCtx) {
                    return "";
                }

                const useFullFrame = tryIndex % 4 === 3;
                const useDeep = tryIndex % 6 === 5;
                tryIndex += 1;
                scanCtx.imageSmoothingEnabled = false;

                if (useFullFrame) {
                    const outW = Math.min(1600, Math.max(1100, vw));
                    const outH = Math.min(900, Math.max(620, Math.floor((outW * vh) / vw)));
                    scanCanvas.width = outW;
                    scanCanvas.height = outH;
                    scanCtx.drawImage(modalVideo, 0, 0, vw, vh, 0, 0, outW, outH);
                } else {
                    // 2x zoom crop path for better barcode recognition on mobile.
                    const srcW = Math.max(480, Math.floor(vw * 0.5));
                    const srcX = Math.max(0, Math.floor((vw - srcW) * 0.5));
                    const bandY = Math.floor(vh * 0.28);
                    const bandH = Math.max(120, Math.floor(vh * 0.44));
                    const outW = Math.min(1800, Math.max(1200, srcW * 2));
                    const outH = Math.min(820, Math.max(280, Math.floor((outW * bandH) / srcW)));
                    scanCanvas.width = outW;
                    scanCanvas.height = outH;
                    scanCtx.drawImage(modalVideo, srcX, bandY, srcW, bandH, 0, 0, outW, outH);
                }

                const imageData = scanCanvas.toDataURL("image/jpeg", 0.82);
                setStatus(msg.decoding);
                try {
                    const resp = await fetch("/mform/decode_barcode", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            image_data: imageData,
                            deep: useDeep,
                            // Prefer 1D first for speed, but allow QR/2D after a short time.
                            prefer_1d: !!prefer1d,
                        }),
                        credentials: "same-origin",
                    });
                    if (!resp.ok) {
                        return "";
                    }
                    const data = await resp.json();
                    if (data && data.ok && data.value) {
                        return data.value;
                    }
                    if (data && data.reason === "decoder_unavailable") {
                        setStatus(msg.decoderMissing);
                        stopScan();
                    } else if (data && data.reason === "payload_too_large") {
                        setStatus(msg.payloadLarge);
                    }
                } catch (e) {
                    // network/transient errors, keep trying
                }
                return "";
            };

            const openModal = () => {
                modal.classList.add("is-open");
                modal.setAttribute("aria-hidden", "false");
                if (modalAimLine) {
                    modalAimLine.style.display = "block";
                }
                if (modalAimLineVert) {
                    modalAimLineVert.style.display = "block";
                }
            };

            const closeModal = () => {
                stopScan();
                modal.setAttribute("aria-hidden", "true");
            };

            modalClose.addEventListener("click", closeModal);
            modal.addEventListener("click", (evt) => {
                if (evt.target === modal) {
                    closeModal();
                }
            });

            button.addEventListener("click", async () => {
                const oldText = button.textContent;
                button.textContent = msg.starting;
                try {
                    if (cameraTip) {
                        cameraTip.classList.add("is-active");
                    }
                    if (!canUseCamera) {
                        setStatus(msg.noCameraApi);
                        button.textContent = oldText;
                        return;
                    }
                    stopScan();
                    openModal();

                    stream = await requestCameraStream();
                    const track = stream.getVideoTracks()[0];
                    if (track && typeof track.getCapabilities === "function") {
                        try {
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
                        } catch (e) {
                            // ignore tuning failures
                        }
                    }

                    modalVideo.srcObject = stream;
                    modalVideo.setAttribute("playsinline", "true");
                    modalVideo.muted = true;
                    await modalVideo.play();
                    setStatus(msg.openCamera);

                    scanning = true;
                    startedAt = Date.now();
                    serverPhaseStartedAt = startedAt;
                    button.textContent = oldText;

                    const scanCanvas = document.createElement("canvas");
                    const scanCtx = scanCanvas.getContext("2d", { willReadFrequently: true });

                    const scan = async () => {
                        if (!scanning || !stream || !modalVideo.srcObject) {
                            return;
                        }
                        const now = Date.now();
                        const prefer1d = now - serverPhaseStartedAt < 2500;
                        const value = await decodeOnServer(scanCanvas, scanCtx, prefer1d);
                        if (value) {
                            input.value = value;
                            updateFieldErrorUI(input);
                            setStatus(msg.success);
                            stopScan();
                            button.textContent = oldText;
                            return;
                        }
                        if (startedAt && Date.now() - startedAt > 30000) {
                            setStatus(msg.timeout);
                            stopScan();
                            button.textContent = oldText;
                            updateFieldErrorUI(input);
                            input.focus();
                            return;
                        }
                        window.setTimeout(scan, 180);
                    };
                    window.setTimeout(scan, 180);
                } catch (e) {
                    stopScan();
                    setStatus(msg.cameraFail);
                    button.textContent = oldText;
                }
            });
        });
    }

    const boot = () => {
        initInputRules();
        initDatePickerOnly();
        initFormattedNumberInputs();
        initCascadingOptions();
        initVisibilityRules();
        initAgeAutoFields();
        initSignaturePads();
        initBarcodeScanner();
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot, { once: true });
    } else {
        boot();
    }
})();
