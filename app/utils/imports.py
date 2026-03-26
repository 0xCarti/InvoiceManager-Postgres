"""Data import helper functions used by admin routes and tests."""

import csv
import os

from werkzeug.security import generate_password_hash

from app.models import (
    Customer,
    GLCode,
    Item,
    ItemUnit,
    Location,
    Product,
    ProductRecipeItem,
    User,
    Vendor,
    db,
)


def _import_csv(path, model, mappings):
    """Import generic rows from CSV into the specified model."""
    created = 0
    if not os.path.exists(path):
        return 0
    with open(path, newline="", encoding="utf-8-sig") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            obj_kwargs = {
                field: row[col]
                for field, col in mappings.items()
                if row.get(col) is not None
            }
            if model == User:
                if User.query.filter_by(email=obj_kwargs["email"]).first():
                    continue
                obj_kwargs["password"] = generate_password_hash(
                    obj_kwargs["password"]
                )
                obj_kwargs["is_admin"] = row.get("is_admin", "0") == "1"
                obj_kwargs["active"] = row.get("active", "0") == "1"
            if model == GLCode:
                if GLCode.query.filter_by(code=obj_kwargs["code"]).first():
                    continue
            obj = model(**obj_kwargs)
            db.session.add(obj)
            created += 1
    db.session.commit()
    return created


def _import_items(path):
    """Import items from a CSV or plain text file."""
    if not os.path.exists(path):
        return 0

    created = 0
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        with open(path, newline="", encoding="utf-8-sig") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                name = row.get("name", "").strip()
                if (
                    not name
                    or Item.query.filter_by(name=name, archived=False).first()
                ):
                    continue
                base_unit = (
                    row.get("base_unit", "each").strip().lower() or "each"
                )
                cost = float(row.get("cost") or 0)
                gl_code_id = None
                if row.get("gl_code"):
                    gl = GLCode.query.filter_by(code=row["gl_code"]).first()
                    if gl:
                        gl_code_id = gl.id
                item = Item(
                    name=name,
                    base_unit=base_unit,
                    cost=cost,
                    gl_code_id=gl_code_id,
                )
                db.session.add(item)
                db.session.flush()

                units_spec = row.get("units", "")
                units = []
                if units_spec:
                    for idx, spec in enumerate(units_spec.split(";")):
                        spec = spec.strip()
                        if not spec:
                            continue
                        if ":" in spec:
                            unit_name, factor_str = spec.split(":", 1)
                            try:
                                factor = float(factor_str)
                            except ValueError:
                                factor = 1.0
                        else:
                            unit_name = spec
                            factor = 1.0
                        units.append(
                            ItemUnit(
                                item_id=item.id,
                                name=unit_name.strip(),
                                factor=factor,
                                receiving_default=(idx == 0),
                                transfer_default=(idx == 0),
                            )
                        )
                else:
                    units.append(
                        ItemUnit(
                            item_id=item.id,
                            name=base_unit,
                            factor=1.0,
                            receiving_default=True,
                            transfer_default=True,
                        )
                    )
                db.session.add_all(units)
                created += 1
    else:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                name = line.strip()
                if (
                    name
                    and not Item.query.filter_by(
                        name=name, archived=False
                    ).first()
                ):
                    item = Item(name=name, base_unit="each")
                    db.session.add(item)
                    db.session.flush()
                    db.session.add(
                        ItemUnit(
                            item_id=item.id,
                            name="each",
                            factor=1.0,
                            receiving_default=True,
                            transfer_default=True,
                        )
                    )
                    created += 1
    db.session.commit()
    return created


def _import_locations(path):
    """Import locations with optional product names."""
    if not os.path.exists(path):
        return 0

    pending = []
    rows = []
    product_names = set()
    with open(path, newline="", encoding="utf-8-sig") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            name = row.get("name", "").strip()
            if not name:
                continue
            if Location.query.filter_by(name=name).first():
                continue

            prod_names = []
            prod_field = row.get("products", "")
            if prod_field:
                for pname in prod_field.split(";"):
                    pname = pname.strip()
                    if not pname:
                        continue
                    prod_names.append(pname)
                    product_names.add(pname)

            rows.append((name, prod_names))

    products = {
        p.name: p for p in Product.query.filter(Product.name.in_(product_names)).all()
    }

    for name, prod_names in rows:
        products_list = []
        for pname in prod_names:
            product = products.get(pname)
            if not product:
                db.session.rollback()
                raise ValueError(f"Unknown product: {pname}")
            products_list.append(product)
        pending.append((name, products_list))

    for name, products in pending:
        loc = Location(name=name)
        loc.products = products
        db.session.add(loc)

    db.session.commit()
    return len(pending)


def _import_products(path):
    """Import products with optional recipe items."""
    if not os.path.exists(path):
        return 0

    pending = []
    with open(path, newline="", encoding="utf-8-sig") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            name = row.get("name", "").strip()
            if not name:
                continue
            if Product.query.filter_by(name=name).first():
                continue

            gl_code_id = None
            if row.get("gl_code"):
                gl = GLCode.query.filter_by(code=row["gl_code"]).first()
                if gl:
                    gl_code_id = gl.id

            recipe_items = []
            recipe_field = row.get("recipe", "")
            if recipe_field:
                for spec in recipe_field.split(";"):
                    spec = spec.strip()
                    if not spec:
                        continue
                    parts = spec.split(":")
                    item_name = parts[0]
                    qty = 1.0
                    unit_name = None
                    if len(parts) >= 2 and parts[1] != "":
                        try:
                            qty = float(parts[1])
                        except ValueError:
                            qty = 0.0
                    if len(parts) == 3 and parts[2].strip():
                        unit_name = parts[2].strip()
                    item = Item.query.filter_by(name=item_name.strip()).first()
                    if not item:
                        db.session.rollback()
                        raise ValueError(f"Unknown item: {item_name.strip()}")
                    unit_id = None
                    if unit_name:
                        unit = ItemUnit.query.filter_by(
                            item_id=item.id, name=unit_name
                        ).first()
                        if not unit:
                            db.session.rollback()
                            raise ValueError(
                                f"Unknown unit {unit_name} for item {item_name.strip()}"
                            )
                        unit_id = unit.id
                    recipe_items.append((item.id, qty, unit_id))

            pending.append(
                (
                    name,
                    float(row["price"]),
                    float(row.get("cost", 0) or 0),
                    gl_code_id,
                    recipe_items,
                )
            )

    created = 0
    for name, price, cost, gl_code_id, recipe_items in pending:
        product = Product(
            name=name,
            price=price,
            invoice_sale_price=price,
            cost=cost,
            gl_code_id=gl_code_id,
        )
        db.session.add(product)
        db.session.flush()
        for item_id, qty, unit_id in recipe_items:
            db.session.add(
                ProductRecipeItem(
                    product_id=product.id,
                    item_id=item_id,
                    unit_id=unit_id,
                    quantity=qty,
                    countable=False,
                )
            )
        created += 1

    db.session.commit()
    return created
