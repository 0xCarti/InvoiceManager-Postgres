# Key Data Models

This document summarises the primary SQLAlchemy models defined in
[`app/models.py`](../app/models.py). Understanding these entities and their
relationships will make it easier to work with routes, templates, and
background tasks.

## Core Entities

### User
* **Table**: `user`
* **Purpose**: Authenticated application user. Supports admin flag, favourites,
  timezone preferences, and notification toggles.
* **Key Relationships**: `transfers` and `invoices` backrefs expose the records
  a user created; `activity_logs` captures audit events.

### Location
* **Table**: `location`
* **Purpose**: Physical or logical storage site. Tracks whether the location is
  archived or used for spoilage.
* **Key Relationships**: Many-to-many with `Product` via `location_products` to
  describe availability; one-to-many with `LocationStandItem` for expected
  inventory; links to events through `EventLocation`.

### Item
* **Table**: `item`
* **Purpose**: Stock-keeping unit used in inventory counts and recipes. Stores
  base unit, GL codes, quantity on hand, and cost.
* **Key Relationships**: Many-to-many with `Transfer` through `transfer_items`;
  one-to-many with `ItemUnit` (conversion factors), `ProductRecipeItem`
  (ingredient usage), and `PurchaseInvoiceItem`. Helper methods resolve GL codes
  per location.

### ItemUnit
* **Table**: `item_unit`
* **Purpose**: Alternative units of measure for an item (e.g. case, bottle).
* **Key Relationships**: Belongs to an `Item`; referenced by recipe and purchase
  items to keep quantities consistent.

### Transfer and TransferItem
* **Tables**: `transfer`, `transfer_item`
* **Purpose**: Track inventory moved between locations. `Transfer` stores the
  origin, destination, creator, and completion state. `TransferItem` stores the
  per-item quantities and retains the item name for history.
* **Key Relationships**: Each transfer references `Location` twice (`from` and
  `to`) and has many `TransferItem` records. Users who initiate transfers are
  linked via `user_id`.

### Product
* **Table**: `product`
* **Purpose**: Sellable item with separate terminal/event `price`, dedicated
  `invoice_sale_price` for 3rd-party customer invoices, cost, and GL codes for
  accounting.
* **Key Relationships**: Connected to `InvoiceProduct` (sales history),
  `ProductRecipeItem` (ingredients), `TerminalSale` (event sales), and optional
  `GLCode` entries for accounting mappings.

### ProductRecipeItem
* **Table**: `product_recipe_item`
* **Purpose**: Defines how much of each `Item` is required to produce a
  `Product`.
* **Key Relationships**: Links a product, an item, and an optional `ItemUnit`.

### Invoice and InvoiceProduct
* **Tables**: `invoice`, `invoice_product`
* **Purpose**: Customer-facing sales documents. `Invoice` stores the creator,
  customer, and creation timestamp while `InvoiceProduct` captures line details,
  tax overrides, and totals.
* **Key Relationships**: `Invoice` belongs to a `User` and a `Customer`; has
  many `InvoiceProduct` children. Products sold can be null (archived or deleted
  products) thanks to `SET NULL` foreign keys.

### Customer and Vendor
* **Tables**: `customer`, `vendor`
* **Purpose**: Business contacts for sales and purchasing respectively. Include
  tax exemption flags and archived states.
* **Key Relationships**: `Customer` has many invoices; `Vendor` has many
  `PurchaseOrder` records.

### PurchaseOrder and PurchaseOrderItem
* **Tables**: `purchase_order`, `purchase_order_item`
* **Purpose**: Records orders issued to vendors, including expected delivery
  dates and line items.
* **Key Relationships**: Each order belongs to a `Vendor` and `User` and has
  many `PurchaseOrderItem` rows. Line items optionally reference `Product`,
  `Item`, and `ItemUnit`.

### PurchaseInvoice and PurchaseInvoiceItem
* **Tables**: `purchase_invoice`, `purchase_invoice_item`
* **Purpose**: Capture received goods against purchase orders, including taxes,
  delivery charges, and per-item costs.
* **Key Relationships**: Purchase invoices belong to a `PurchaseOrder`, `User`,
  and `Location`. Line items reference `Item` and `ItemUnit` when available and
  resolve GL codes dynamically through helper methods.

### Event, EventLocation, TerminalSale, and EventStandSheetItem
* **Tables**: `event`, `event_location`, `terminal_sale`, `event_stand_sheet_item`
* **Purpose**: Manage temporary events (e.g. festivals). `Event` defines the
  schedule; `EventLocation` links events to locations and tracks opening/closing
  counts; `TerminalSale` records product sales at a specific event location;
  `EventStandSheetItem` logs detailed inventory movements for stands.
* **Key Relationships**: These models bridge inventory (`Item`, `Product`) and
  locations within the context of an event.

### GLCode and Setting
* **Tables**: `gl_code`, `setting`
* **Purpose**: Support accounting and configuration. `GLCode` entities are
  referenced by items, products, and purchase invoice lines. `Setting` stores
  key-value configuration entries (e.g., GST amount, default timezone,
  automated backup preferences) read during application start-up.

## Auxiliary Tables

* **`transfer_items`**: Association table linking `Transfer` and `Item` with a
  quantity column for many-to-many relationships.
* **`location_products`**: Association table mapping which `Product` entries are
  available at which `Location` instances.
* **`location_stand_item`**: Materialises expected counts and purchase GL codes
  for items at a specific location.
* **`purchase_order_item_archive`**: Keeps historical snapshots of purchase
  order line items when they are archived.

These models form the backbone of InvoiceManager. Routes query and mutate them,
templates render their fields, and background tasks (backups, imports) rely on
consistent relationships to operate correctly.
