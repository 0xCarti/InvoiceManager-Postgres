# Routes Reference

This reference documents the Flask blueprints defined in `app/routes/`. Each
section lists the URL prefix that is applied when the blueprint is registered
in `app/__init__.py`, the primary responsibilities/endpoints, notable forms,
models, or utility helpers used by the module, and any cross-cutting behaviors
that developers should keep in mind when working on or extending the routes.

## Quick reference table

| Module | Blueprint(s) | URL prefix | Highlights |
| --- | --- | --- | --- |
| `auth_routes` | `auth`, `admin` | `/auth`, none | Authentication flows, user/admin control panel, backups, imports |
| `main_routes` | `main` | none | Root dashboard for transfers |
| `location_routes` | `locations` | none | Manage locations, stand items, and product assignments |
| `item_routes` | `item` | none | Inventory item catalog, detail views, imports |
| `transfer_routes` | `transfer` | none | Transfer dashboard, CRUD, reports, notifications |
| `spoilage_routes` | `spoilage` | none | Spoilage reporting filtered by transfers |
| `customer_routes` | `customer` | none | Customer directory CRUD |
| `vendor_routes` | `vendor` | none | Vendor directory CRUD |
| `product_routes` | `product` | none | Product catalog, recipes, pricing |
| `invoice_routes` | `invoice` | none | Sales invoice creation, viewing, filtering |
| `purchase_routes` | `purchase` | none | Purchase orders & invoices lifecycle |
| `report_routes` | `report` | none | Reporting forms for sales and purchasing |
| `event_routes` | `event` | none | Event scheduling, inventory, terminal sales |
| `glcode_routes` | `glcode` | none | General ledger code maintenance |

---

### `auth_routes`

- **Blueprint name(s) and prefixes:** `auth` (mounted at `/auth`) and `admin`
  (no additional prefix, used for control panel tooling). Registration happens
  in `app/__init__.py`.
- **Primary endpoints:**
  - `GET/POST /auth/login` authenticates users with `LoginForm` and applies a
    per-minute rate limit via `limiter`.
  - `GET /auth/logout` ends the current session and records the action in the
    activity log.
  - Password recovery flow via `GET/POST /auth/reset` and
    `GET/POST /auth/reset/<token>` backed by email notifications and
    token verification.
  - Profile management at `GET/POST /auth/profile` for password changes,
    timezone settings, and SMS notification preferences.
  - Admin control panel routes (e.g. `/controlpanel/users`,
    `/controlpanel/backups`, `/controlpanel/imports`, `/controlpanel/settings`)
    handle user administration, database backups/restores, CSV imports, and
    system configuration.
- **Key dependencies:** Extensive use of forms defined in `app.forms`
  (`LoginForm`, `ChangePasswordForm`, `UserForm`, `CreateBackupForm`,
  `ImportForm`, etc.), models such as `User`, `Setting`, `Invoice`, `Customer`,
  `Transfer`, and utilities including `limiter`, `send_email`,
  `log_activity`, and backup helpers from `app.utils.backup`.
- **Cross-cutting behaviors:** Most admin routes are protected with
  `@login_required`, and sensitive operations record audit entries through
  `log_activity`. File uploads for backups/imports enforce explicit size and
  extension restrictions. Rate limiting via `limiter` is applied to login and
  password reset to guard against brute-force attempts.

### `main_routes`

- **Blueprint name and prefix:** `main` (no additional prefix).
- **Primary endpoints:** A single `GET /` route renders the transfers
  dashboard, pre-populating create/edit forms (`TransferForm`).
  - Dashboard trend cards/charts support an `interval` query parameter on `/`
    (for example `/?interval=month`) to control aggregation windows.
  - Supported interval keys are `week`, `month`, `quarter`, `half_year`, and
    `year`.
- **Key dependencies:** Imports `TransferForm` on demand to avoid circular
  dependencies and uses `current_user` from `flask_login` to display the
  authenticated user. Aggregation behavior for the dashboard widgets and trend
  charts is centralized in `app/services/dashboard_metrics.py`.
- **Cross-cutting behaviors:** The home view is guarded by `@login_required`
  and mirrors the transfer blueprint patterns for rendering forms. If the
  interval query parameter is omitted, trend aggregation defaults to `week`
  buckets (6 periods by default in the current trend dataset).

#### Dashboard interval + bucket consistency notes

- Bucket boundaries are derived from normalized interval starts in
  `app/services/dashboard_metrics.py` (`_interval_start`) and then stepped
  forward with `_add_interval`. Weekly buckets align to Monday; month/quarter/
  half-year/year buckets align to day 1 of their period.
- Labels are derived from each bucket's computed start/end boundaries in
  `weekly_transfer_purchase_activity`, using the same range format
  (`"%b %d – %b %d"`). Keep boundary derivation and label formatting coupled
  when adding/changing interval keys so charts and tables remain consistent.

### `location_routes`

- **Blueprint name and prefix:** `locations` (no additional prefix).
- **Primary endpoints:**
  - `GET/POST /locations/add` creates new locations and attaches products.
  - `GET/POST /locations/<id>/edit` manages existing location attributes and
    product associations.
  - `GET /locations` lists locations with pagination and filtering helpers.
  - Nested routes manage stand sheet items (e.g. `/locations/items`,
    `/locations/items/delete`) to assign inventory items to stands.
- **Key dependencies:** Uses forms such as `LocationForm`,
  `LocationItemAddForm`, `ItemForm`, and `DeleteForm`; models include `Location`,
  `LocationStandItem`, `Product`, and `Item`. Utilities include
  `log_activity`, `build_pagination_args`, and `get_per_page`, with eager loading
  via SQLAlchemy's `selectinload`.
- **Cross-cutting behaviors:** All routes require authentication, log
  significant changes (create/update/delete) with `log_activity`, and rely on
  shared pagination helpers. Helper functions enforce guardrails when removing
  items that are part of product recipes to prevent inconsistent inventory.

### `item_routes`

- **Blueprint name and prefix:** `item` (no additional prefix).
- **Primary endpoints:**
  - `GET /items` lists inventory items with extensive filtering and persists
    filter state in the session.
  - `GET /items/<id>` shows detailed purchase, sales, and transfer history with
    separate paginated sections.
  - CRUD routes (`/items/add`, `/items/edit/<id>`, `/items/delete/<id>`,
    `/items/bulk_delete`) manage item records.
  - Helper endpoints support AJAX searches, quick adds, unit management, cost
    lookups, and bulk import from text files (`/import_items`).
- **Key dependencies:** Relies on forms (`ItemForm`, `ImportItemsForm`,
  `CSRFOnlyForm`), numerous models (e.g. `Item`, `ItemUnit`, `GLCode`,
  `PurchaseOrder`, `Transfer`, `Vendor`), and utilities such as `log_activity`,
  `build_pagination_args`, `get_per_page`, and secure file handling via
  `secure_filename`.
- **Cross-cutting behaviors:** Every route enforces `@login_required` and uses
  centralized pagination helpers. Mutating actions produce activity log entries,
  and inventory adjustments check for negative quantities. File uploads are
  capped in size and limited to `.txt` extensions.

### `transfer_routes`

- **Blueprint name and prefix:** `transfer` (no additional prefix).
- **Primary endpoints:**
  - `GET /transfers` renders the transfers dashboard with filtering options.
  - Creation and editing flows are exposed via `/transfers/add`,
    `/transfers/ajax_add`, `/transfers/edit/<id>`, and
    `/transfers/ajax_edit/<id>` with both HTML and JSON responses.
  - `/transfers/delete/<id>` removes transfers, while `/transfers/view/<id>` and
    `/transfers/<id>/json` provide detail views.
  - Reporting endpoints such as `/transfers/generate_report` and `/transfers/report`
    export movement summaries.
- **Key dependencies:** Forms (`TransferForm`, `ConfirmForm`, `DateRangeForm`),
  models (`Transfer`, `TransferItem`, `Location`, `Item`, `User`), utilities
  (`log_activity`, `build_pagination_args`, `get_per_page`, `send_sms`), and the
  shared `socketio` instance for real-time notifications.
- **Cross-cutting behaviors:** All operations are authenticated and log notable
  events. Inventory safety checks (e.g. `check_negative_transfer`) run before
  committing changes. The module centralizes pagination/query helper use and
  broadcasts updates through Socket.IO and optional SMS alerts.

### `spoilage_routes`

- **Blueprint name and prefix:** `spoilage` (no additional prefix).
- **Primary endpoints:** Single `GET /spoilage` route that displays transfer
  data destined for spoilage locations with optional filters (date ranges,
  GL codes, item selections).
- **Key dependencies:** Uses `SpoilageFilterForm`, SQLAlchemy queries joining
  `Transfer`, `TransferItem`, `Location`, `LocationStandItem`, `Item`, and
  `GLCode`.
- **Cross-cutting behaviors:** Requires authentication, reuses the global
  database session, and leverages consistent filtering helpers. The view keeps
  pagination disabled but follows the same pattern of form-driven filtering.

### `customer_routes`

- **Blueprint name and prefix:** `customer` (no additional prefix).
- **Primary endpoints:**
  - `GET /customers` renders paginated customer lists with GST/PST filter
    controls.
  - `GET/POST /customers/create` and `/customers/<id>/edit` manage customer
    records.
  - Modal-optimized `/customers/create-modal` supports AJAX creation, and
    `/customers/<id>/delete` archives a customer.
- **Key dependencies:** Utilizes `CustomerForm` and `DeleteForm`, interacts with
  the `Customer` model, and integrates pagination helpers.
- **Cross-cutting behaviors:** All routes are guarded by `@login_required` and
  log create/update/delete operations. Pagination helpers ensure consistent list
  rendering, and server-side validation enforces uniqueness and tax settings.

### `vendor_routes`

- **Blueprint name and prefix:** `vendor` (no additional prefix).
- **Primary endpoints:**
  - `GET /vendors` shows paginated vendor listings.
  - `GET/POST /vendors/create` and `/vendors/<id>/edit` manage vendor records,
    supporting AJAX responses.
  - `/vendors/<id>/delete` archives vendors.
- **Key dependencies:** Shares `CustomerForm` for input handling, uses
  `DeleteForm`, and interacts with the `Vendor` model. Pagination helpers mirror
  the customer module.
- **Cross-cutting behaviors:** Login protection plus `log_activity` for
  mutating actions. AJAX handlers return partial templates consistent with other
  blueprints.

### `product_routes`

- **Blueprint name and prefix:** `product` (no additional prefix).
- **Primary endpoints:**
  - `GET /products` lists products with filter persistence in the session.
  - CRUD routes cover `/products/create`, AJAX creation/validation endpoints,
    `/products/<id>/edit`, and `/products/<id>/delete`.
  - Recipe management lives under `/products/<id>/recipe` and cost helpers
    (`/products/<id>/calculate_cost`, `/products/bulk_set_cost_from_recipe`).
    The cost calculator divides the batch total by the recipe yield (override via
    `?yield_quantity=`) and returns both batch and per-unit values so pricing can
    be aligned with output volume.
  - `/search_products` powers autocomplete searches.
- **Key dependencies:** Forms include `ProductWithRecipeForm`,
  `ProductRecipeForm`, `BulkProductCostForm`, and `DeleteForm`. Models span
  `Product`, `ProductRecipeItem`, `Item`, `ItemUnit`, `Invoice`, `InvoiceProduct`,
  `Customer`, and `TerminalSale`. Utilities cover `log_activity`, session-based
  filter persistence, and pagination helpers.
- **Cross-cutting behaviors:** Every view requires authentication. Mutations
  log activities and synchronize recipe-driven cost calculations. Extensive use
  of SQLAlchemy query composition keeps filtering performant.

### `invoice_routes`

- **Blueprint name and prefix:** `invoice` (no additional prefix).
- **Primary endpoints:**
  - `GET/POST /create_invoice` builds invoices from form submissions, creating
    `Invoice` and `InvoiceProduct` records and adjusting inventory.
  - `POST /delete_invoice/<id>` removes invoices via `DeleteForm` confirmation.
  - `GET /view_invoice/<id>` renders printable invoice details.
  - `GET /view_invoices` lists invoices with filters and pagination, while the
    `/api/*` endpoints (`/api/filter_invoices`, `/api/create_invoice`) expose
    JSON for AJAX workflows.
  - `GET /get_customer_tax_status/<id>` provides tax exemption metadata to the
    UI.
- **Key dependencies:** Forms (`InvoiceForm`, `InvoiceFilterForm`, `DeleteForm`),
  models (`Invoice`, `InvoiceProduct`, `Customer`, `Product`), and utilities
  (`log_activity`, `build_pagination_args`, `get_per_page`). Pulls GST settings
  from `app` configuration.
- **Cross-cutting behaviors:** All routes enforce login. Activity logging tracks
  creation/deletion. Helper `_create_invoice_from_form` centralizes invoice
  assembly and ensures inventory decrement logic stays consistent between HTML
  and API handlers.

### `purchase_routes`

- **Blueprint name and prefix:** `purchase` (no additional prefix).
- **Primary endpoints:**
  - `GET /purchase_orders` lists purchase orders with filtering.
  - Creation/editing via `/purchase_orders/create` and
    `/purchase_orders/edit/<id>` manage orders and items.
  - `/purchase_orders/<id>/delete` archives orders, while
    `/purchase_orders/<id>/pdf` (AJAX) produces printable documents.
  - Purchase invoices are handled through `/purchase_invoices`,
    `/purchase_invoices/<id>`, and related reporting endpoints.
- **Key dependencies:** Forms such as `PurchaseOrderForm`, `ReceiveInvoiceForm`,
  `ConfirmForm`, and `DeleteForm`; models like `PurchaseOrder`, `PurchaseOrderItem`,
  `PurchaseInvoice`, `Item`, `ItemUnit`, `Vendor`, `Location`, and `GLCode`.
  Utilities include `log_activity`, `build_pagination_args`, `get_per_page`, and
  helper functions to validate negative inventory risk.
- **Cross-cutting behaviors:** Routes require authentication, log significant
  state changes, and reuse pagination helpers. Validation routines ensure that
  reversing invoices or receiving goods does not yield negative inventory.

### `report_routes`

- **Blueprint name and prefix:** `report` (no additional prefix).
- **Primary endpoints:**
  - `/reports/vendor-invoices`, `/reports/received-invoices`,
    `/reports/purchase-inventory-summary`, `/reports/product-sales`,
    `/reports/product-recipes`, and `/reports/product-location-sales` each
    expose a GET/POST form for selecting
    filters and redirect to result views when submitted.
  - Result routes (e.g. `/reports/vendor-invoices/results`) compute aggregates
    based on the selected criteria.
- **Key dependencies:** Uses specialized report forms
  (`VendorInvoiceReportForm`, `ReceivedInvoiceReportForm`, `PurchaseInventorySummaryForm`,
  `ProductSalesReportForm`, `ProductRecipeReportForm`) and queries models including `Invoice`,
  `InvoiceProduct`, `Product`, `PurchaseInvoice`, `PurchaseOrder`, `Customer`,
  `TerminalSale`, `User`, `EventLocation`, and `Location`.
- **Cross-cutting behaviors:** All routes require authentication. The forms are
  responsible for validating date ranges and selections before the report query
  executes. Reporting endpoints share logic for redirect-after-post patterns.
  The department sales forecast workflow hides automatically linked POS rows
  from the mapping form while listing them in the "Current Mappings" summary so
  that staff can focus on unresolved products.

### `event_routes`

- **Blueprint name and prefix:** `event` (no additional prefix).
- **Primary endpoints:**
  - `GET /events` and `/events/create` manage event listings and creation.
  - `/events/<id>/edit`, `/events/<id>/delete`, and `/events/<id>` cover update
    and detail views.
  - Location management (`/events/<id>/add_location`, `/events/<id>/confirm_location`)
    coordinates inventory for event stands.
  - Terminal sales uploads (`/events/<id>/sales/upload`) and reconciliation
    routes generate revenue and inventory reports, stand/count sheets, and
    closing workflows.
- **Key dependencies:** Forms include `EventForm`, `EventLocationForm`,
  `EventLocationConfirmForm`, `TerminalSalesUploadForm`; models span `Event`,
  `EventLocation`, `EventStandSheetItem`, `Location`, `LocationStandItem`,
  `Product`, and `TerminalSale`. Utilities rely on `secure_filename`,
  SQLAlchemy sessions, and `log_activity` for auditing changes.
- **Cross-cutting behaviors:** All endpoints require login. File uploads have
  controlled extensions and size, and helper routines ensure that assigning
  inventory to events respects product recipe constraints. Many views share
  modal/AJAX patterns with other blueprints for consistency.

### `glcode_routes`

- **Blueprint name and prefix:** `glcode` (no additional prefix).
- **Primary endpoints:**
  - `GET /gl_codes` lists GL codes with optional filters.
  - `GET/POST /gl_codes/create` and `/gl_codes/<id>/edit` manage GL code
    lifecycle, with `/gl_codes/<id>/delete` handling removal.
- **Key dependencies:** Uses `GLCodeForm` and `DeleteForm` with the `GLCode`
  model, alongside pagination helpers.
- **Cross-cutting behaviors:** Routes are login-protected and follow the shared
  pattern of render-on-GET/redirect-on-POST with flash messaging on success.

---

### Shared behaviors and utilities

While the sections above highlight blueprint-specific traits, several patterns
are shared across modules:

- **Authentication:** With the exception of the public login/reset views, every
  route is decorated with `@login_required` to enforce session-based access
  control.
- **Activity logging:** Modules that mutate domain entities (customers, vendors,
  inventory, events, invoices, transfers, etc.) call `log_activity` to provide
  an audit trail visible from the admin control panel.
- **Pagination helpers:** List views consistently call `get_per_page()` and
  `build_pagination_args()` to keep pagination behaviour uniform in templates.
- **AJAX & forms:** Many blueprints expose AJAX-friendly endpoints that render
  partial templates or return JSON alongside the traditional HTML forms, making
  it easier to enhance the UI without duplicating business logic.

Consult the sections above when adding new routes to reuse these existing
patterns and utilities.
