# mobile_form_builder Installation Guide (Odoo 19 Community)

## 1) Module Location

Place module folder at:

- `/Users/mingxingmac/Documents/Codex/mobile_form_builder`

Your Odoo `addons_path` must include:

- Odoo core addons path
- `/Users/mingxingmac/Documents/Codex`

## 2) Required Dependencies

### Python packages (inside Odoo venv)

```bash
source /Users/mingxingmac/Documents/Codex/.local/venv-odoo19/bin/activate
pip install --upgrade Pillow pyzbar qrcode reportlab xlsxwriter
```

### System packages

On macOS (Homebrew):

```bash
brew install zbar
```

## 3) PDF Export Requirement

`Export PDF` requires `wkhtmltopdf` with patched qt.

In this local setup, already configured paths are:

- `/Users/mingxingmac/Documents/Codex/.local/bin/wkhtmltopdf`
- `/Users/mingxingmac/Documents/Codex/.local/bin/wkhtmltoimage`

Check:

```bash
/Users/mingxingmac/Documents/Codex/.local/bin/wkhtmltopdf --version
```

Expected string contains: `with patched qt`.

## 4) Install Module

```bash
source /Users/mingxingmac/Documents/Codex/.local/venv-odoo19/bin/activate
export PATH="/Users/mingxingmac/Documents/Codex/.local/bin:/opt/homebrew/opt/postgresql@16/bin:/opt/homebrew/opt/libpq/bin:/opt/homebrew/opt/openssl@3/bin:$PATH"
export DYLD_LIBRARY_PATH="/Users/mingxingmac/Documents/Codex/.local/wkhtmltox/lib:/opt/homebrew/opt/openssl@3/lib:/opt/homebrew/opt/libpq/lib:${DYLD_LIBRARY_PATH:-}"
python /Users/mingxingmac/Documents/Codex/.local/odoo19/odoo-bin \
  -c /Users/mingxingmac/Documents/Codex/.local/odoo19.conf \
  -d odoo19_dev \
  -i mobile_form_builder \
  --stop-after-init
```

## 5) Upgrade Module

```bash
source /Users/mingxingmac/Documents/Codex/.local/venv-odoo19/bin/activate
export PATH="/Users/mingxingmac/Documents/Codex/.local/bin:/opt/homebrew/opt/postgresql@16/bin:/opt/homebrew/opt/libpq/bin:/opt/homebrew/opt/openssl@3/bin:$PATH"
export DYLD_LIBRARY_PATH="/Users/mingxingmac/Documents/Codex/.local/wkhtmltox/lib:/opt/homebrew/opt/openssl@3/lib:/opt/homebrew/opt/libpq/lib:${DYLD_LIBRARY_PATH:-}"
python /Users/mingxingmac/Documents/Codex/.local/odoo19/odoo-bin \
  -c /Users/mingxingmac/Documents/Codex/.local/odoo19.conf \
  -d odoo19_dev \
  -u mobile_form_builder \
  --stop-after-init
```

## 6) Run Odoo

```bash
/Users/mingxingmac/Documents/Codex/.local/start_odoo19_bg.sh
```

Open:

- `http://127.0.0.1:8069/web/login?db=odoo19_dev`

Login:

- Username: `admin`
- Password: `admin`

## 7) Quick Validation

1. Install/upgrade module successfully.
2. Open app `Mobile Forms`.
3. Create one form and add at least one input component.
4. Open public form link and submit one record.
5. Verify submission exists in backend.
6. Test `Export Excel` and `Export PDF`.

## 8) Common Errors

### Error: `No module named pyzbar`

Install in venv:

```bash
source /Users/mingxingmac/Documents/Codex/.local/venv-odoo19/bin/activate
pip install pyzbar
```

And ensure `zbar` installed:

```bash
brew install zbar
```

### Error: `Unable to find Wkhtmltopdf on this system`

Ensure Odoo is started with `/Users/mingxingmac/Documents/Codex/.local/start_odoo19.sh` so PATH includes local wrapper.

Then restart Odoo:

```bash
/Users/mingxingmac/Documents/Codex/.local/restart_odoo19.sh
```
