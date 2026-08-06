from app.models import GLCode, Item, ItemUnit
from app.utils.imports import _import_items


def test_import_items_with_cost_and_units(tmp_path, app):
    csv_path = tmp_path / "items.csv"
    csv_path.write_text(
        'name,base_unit,purchase_gl_code,cost,units\n'
        'Buns,each,5000,0.25,"each:1;case:12"\n'
    )

    with app.app_context():
        count = _import_items(str(csv_path))
        assert count == 1
        item = Item.query.filter_by(name="Buns").first()
        gl = GLCode.query.filter_by(code="5000").first()
        assert item is not None
        assert item.base_unit == "each"
        assert item.cost == 0.25
        assert item.purchase_gl_code_id == gl.id
        assert item.gl_code_id is None
        assert item.gl_code is None
        assert len(item.units) == 2
        names = {u.name for u in item.units}
        assert names == {"each", "case"}
        defaults = [
            u for u in item.units if u.receiving_default and u.transfer_default
        ]
        assert len(defaults) == 1
        assert defaults[0].name == "each"


def test_import_items_maps_legacy_gl_code_alias_to_purchase_gl(tmp_path, app):
    csv_path = tmp_path / "legacy_items.csv"
    csv_path.write_text(
        "name,base_unit,gl_code,cost\nLegacy Buns,each,5000,0.35\n"
    )

    with app.app_context():
        count = _import_items(str(csv_path))
        assert count == 1
        item = Item.query.filter_by(name="Legacy Buns").first()
        gl = GLCode.query.filter_by(code="5000").first()
        assert item is not None
        assert item.purchase_gl_code_id == gl.id
        assert item.gl_code_id is None
        assert item.gl_code is None


def test_import_items_txt(tmp_path, app):
    txt_path = tmp_path / "items.txt"
    txt_path.write_text("Widget\n")

    with app.app_context():
        count = _import_items(str(txt_path))
        assert count == 1
        item = Item.query.filter_by(name="Widget").first()
        assert item is not None
        assert item.base_unit == "each"
        assert len(item.units) == 1
        assert item.units[0].name == "each"


def test_import_items_csv_with_bom(tmp_path, app):
    csv_path = tmp_path / "items_bom.csv"
    csv_content = "\ufeffname,base_unit,cost\nWidget,each,0.5\n"
    csv_path.write_bytes(csv_content.encode("utf-8"))

    with app.app_context():
        count = _import_items(str(csv_path))
        assert count == 1
        item = Item.query.filter_by(name="Widget").first()
        assert item is not None
        assert item.base_unit == "each"
