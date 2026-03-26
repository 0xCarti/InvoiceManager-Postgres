import pytest

from app import db
from app.models import GLCode, Item, Product


def test_glcode_relationships_and_uniqueness(app):
    with app.app_context():
        code = GLCode(code="123456", description="Test code")
        db.session.add(code)
        db.session.commit()

        item = Item(name="UniqueItem", base_unit="each", gl_code_id=code.id)
        product = Product(
            name="UniqueProduct", price=1.0, cost=0.5, gl_code_id=code.id
        )
        db.session.add_all([item, product])
        db.session.commit()

        assert item.gl_code_rel == code
        assert product.gl_code_rel == code

        db.session.add(GLCode(code="123456", description="Duplicate"))
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()
