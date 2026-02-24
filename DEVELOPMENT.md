# mobile_form_builder Development Notes

## Scope

This document covers the LIS integration extension in `mobile_form_builder`:
- endpoint configuration
- metadata sync
- form-to-LIS mapping
- push execution and traceability

## Main Models

### `x_mobile.lis.endpoint`

Stores remote LIS endpoint connection and auth settings.

Key fields:
- `base_url`
- `endpoint_code`
- `auth_type` (`none`, `api_key`, `bearer`, `basic`)
- `timeout_seconds`
- `verify_ssl`
- `metadata_sync_time`
- `metadata_sync_message`

Key methods:
- `_base_api_path()`: builds `/lab/api/v1/{endpoint_code}` base URL
- `_build_headers()`: auth headers for all requests
- `_call_jsonrpc(path, payload)`: POST JSON-RPC style call
- `_call_http_json_get(path)`: GET JSON and validate `ok=true`
- `action_sync_metadata()`: sync sample types, services, profiles

### `x_mobile.lis.meta.item`

Caches LIS metadata for mapping UI.

Key fields:
- `endpoint_id`
- `item_type` (`sample_type`, `service`, `profile`)
- `code`
- `name`
- `sample_type_code`
- `is_default`
- `active`

Constraint:
- unique by `(endpoint_id, item_type, code)`

### `x_mobile.lis.mapping`

Stores mapping configuration from one mobile form to one LIS endpoint.

Highlights:
- component mapping for patient/physician/core request fields
- `clinical_note_component_ids` supports multiple bound components
- request-level sample type can be fixed/from field/from metadata
- request lines defined by `line_ids`

### `x_mobile.lis.mapping.line`

Defines service/profile lines to be created on LIS request.

Highlights:
- `line_type`: `service` or `profile`
- service/profile code can come from synced metadata records
- specimen fields support fixed value or source component
- fixed specimen sample type supports metadata-driven selection

## Metadata Sync Behavior

Endpoint sync calls:
- `GET /meta/sample_types`
- `GET /meta/services`
- `GET /meta/profiles`

Upsert strategy:
1. build key `(item_type, code)`
2. update existing rows
3. create missing rows
4. mark non-returned rows as `active=False` (soft stale handling)

This design keeps mapping records stable while allowing LIS catalog changes.

## Push Flow

From `LIS Push Center`, user selects submissions and triggers `action_push_to_lis`.

Expected runtime flow:
1. find active mapping by submission form
2. read mapped submission values
3. assemble LIS payload (`patient`, `physician`, `lines`, etc.)
4. call endpoint `/requests`
5. write back push result to submission:
   - `lis_push_state`
   - `lis_request_no`
   - `lis_push_time`
   - `lis_push_message`

Specimen type rule:
- request-level `sample_type` is intentionally not sent.
- each request line must carry its own `specimen_sample_type`.

## UI/Domain Notes

Many2one component selectors must keep domains complete and valid:
- use expressions like `[('form_id', '=', form_id)]`
- avoid empty/incomplete domain tuples that break web client parsing

## Security and Auditing

- Endpoint/mapping models inherit chatter for operation history.
- Access rights are controlled in `security/ir.model.access.csv`.
- Push-center actions must respect user groups defined in `security/security.xml`.

## Dependency Contract with LIS Module

Requires LIS external API to provide:
- request create/query
- sample result/report
- metadata query endpoints

If metadata endpoints are disabled in LIS endpoint config (`Allow Metadata Query = false`),
sync will fail by design and expose explicit error in `metadata_sync_message`.
