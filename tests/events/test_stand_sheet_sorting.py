from __future__ import annotations

from flask_login import login_user

from app import db
from app.models import Item, Location, Product, ProductRecipeItem, User
from app.routes import event_routes, location_routes


def test_stand_sheet_sorting_normalizes_leading_whitespace(app, monkeypatch):
    """Stand sheet views should ignore leading Unicode whitespace when sorting."""

    with app.app_context():
        location = Location(name="Whitespace Stand")
        db.session.add(location)

        product = Product(name="Whitespace Combo", price=5.0, cost=0.0)
        db.session.add(product)

        items = [
            Item(name="\u00A010 Lemonade", base_unit="each"),
            Item(name=" 2 Pretzels", base_unit="each"),
            Item(name="Apple Slices", base_unit="each"),
        ]
        db.session.add_all(items)
        db.session.flush()

        recipes = [
            ProductRecipeItem(
                product=product, item=item, quantity=1.0, countable=True
            )
            for item in items
        ]
        db.session.add_all(recipes)

        location.products.append(product)
        db.session.commit()

        location_id = location.id
        nb_space_name, spaced_name, alpha_name = [item.name for item in items]

        user = User(
            email="standsheet@example.com",
            password="test-password",
            is_admin=True,
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    captured_context: dict[str, object] = {}

    def fake_render(template_name, **context):
        captured_context.update(context)
        return "OK"

    monkeypatch.setattr(location_routes, "render_template", fake_render)

    with app.test_request_context():
        user = db.session.get(User, user_id)
        login_user(user)
        response = location_routes.view_stand_sheet(location_id)
    assert response == "OK"

    stand_items_location = captured_context.get("stand_items")
    assert stand_items_location, "stand sheet context should include stand items"
    location_names = [entry["item"].name for entry in stand_items_location]

    assert location_names == [nb_space_name, spaced_name, alpha_name]

    with app.app_context():
        _, stand_items_event = event_routes._get_stand_items(location_id)

    event_names = [entry["item"].name for entry in stand_items_event]
    assert event_names == [nb_space_name, spaced_name, alpha_name]
