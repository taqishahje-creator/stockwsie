
from pathlib import Path

path = Path("app.py")
source = path.read_text()

def replace_once(old, new, label):
    global source
    count = source.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected exactly one match, found {count}. "
            "No changes have been written."
        )
    source = source.replace(old, new, 1)

# Products page
replace_once(
    "    query = Product.query\n",
    '    query = Product.query.filter_by(user_id=session["user_id"])\n',
    "Product listing",
)

replace_once(
    "row[0] for row in db.session.query(Product.category).distinct().all()",
    'row[0] for row in db.session.query(Product.category).filter_by(user_id=session["user_id"]).distinct().all()',
    "Product categories",
)

# Product creation and initial movement
replace_once(
    "            product = Product(\n                sku=sku, name=name, category=category,",
    '            product = Product(\n                user_id=session["user_id"],\n                sku=sku, name=name, category=category,',
    "Product owner on creation",
)

replace_once(
    "            db.session.add(StockMovement(\n                product_id=product.id, product_name=name,\n                movement_type=\"Initial stock\",",
    '            db.session.add(StockMovement(\n                user_id=session["user_id"],\n                product_id=product.id, product_name=name,\n                movement_type="Initial stock",',
    "Initial stock ownership",
)

# Edit/delete must only locate the current user's product
replace_once(
    "    product = db.get_or_404(Product, product_id)",
    '    product = Product.query.filter_by(\n        id=product_id, user_id=session["user_id"]\n    ).first_or_404()',
    "Product edit ownership",
)

replace_once(
    "                db.session.add(StockMovement(\n                    product_id=product.id, product_name=name,\n                    movement_type=\"Stock adjustment\",",
    '                db.session.add(StockMovement(\n                    user_id=session["user_id"],\n                    product_id=product.id, product_name=name,\n                    movement_type="Stock adjustment",',
    "Stock adjustment ownership",
)

replace_once(
    "    if Sale.query.filter_by(product_id=product.id).first():",
    '    if Sale.query.filter_by(\n        product_id=product.id, user_id=session["user_id"]\n    ).first():',
    "Delete sale-history check",
)

# Sales: select only owned products and assign ownership to new records
replace_once(
    "            product = db.session.get(Product, product_id)",
    '            product = Product.query.filter_by(\n                id=product_id, user_id=session["user_id"]\n            ).first()',
    "Sale product lookup",
)

replace_once(
    "            sale = Sale(\n                product_id=product.id,",
    '            sale = Sale(\n                user_id=session["user_id"],\n                product_id=product.id,',
    "Sale ownership",
)

replace_once(
    "            db.session.add(StockMovement(\n                product_id=product.id,\n                product_name=product.name,\n                movement_type=\"Sale\",",
    '            db.session.add(StockMovement(\n                user_id=session["user_id"],\n                product_id=product.id,\n                product_name=product.name,\n                movement_type="Sale",',
    "Sale movement ownership",
)

replace_once(
    "    history = Sale.query.order_by(Sale.sold_at.desc()).limit(300).all()",
    '    history = Sale.query.filter_by(user_id=session["user_id"]).order_by(Sale.sold_at.desc()).limit(300).all()',
    "Sales history",
)

replace_once(
    "        products=Product.query.filter(\n            Product.quantity > 0\n        ).order_by(Product.name).all(),",
    '        products=Product.query.filter(\n            Product.user_id == session["user_id"],\n            Product.quantity > 0\n        ).order_by(Product.name).all(),',
    "Products available for sale",
)

# Receipts
replace_once(
    "    sale = db.get_or_404(Sale, sale_id)",
    '    sale = Sale.query.filter_by(\n        id=sale_id, user_id=session["user_id"]\n    ).first_or_404()',
    "Receipt ownership",
)

# Reports
replace_once(
    "    query = Sale.query.filter(Sale.sold_at >= start)",
    '    query = Sale.query.filter(\n        Sale.user_id == session["user_id"], Sale.sold_at >= start\n    )',
    "Report sales",
)

replace_once(
    "    out_of_stock = Product.query.filter(Product.quantity <= 0).order_by(Product.name).all()",
    '    out_of_stock = Product.query.filter(\n        Product.user_id == session["user_id"], Product.quantity <= 0\n    ).order_by(Product.name).all()',
    "Report out-of-stock products",
)

replace_once(
    "    low_stock = Product.query.filter(\n        Product.quantity > 0,\n        Product.quantity <= Product.reorder_level\n    ).order_by(Product.quantity.asc()).all()",
    '    low_stock = Product.query.filter(\n        Product.user_id == session["user_id"],\n        Product.quantity > 0,\n        Product.quantity <= Product.reorder_level\n    ).order_by(Product.quantity.asc()).all()',
    "Report low-stock products",
)

replace_once(
    "    movements = StockMovement.query.order_by(\n        StockMovement.created_at.desc()\n    ).limit(300).all()",
    '    movements = StockMovement.query.filter_by(\n        user_id=session["user_id"]\n    ).order_by(StockMovement.created_at.desc()).limit(300).all()',
    "Report stock movements",
)

# Both AI endpoints
replace_once(
    "def ai_insights():\n    products = Product.query.all()\n    sales_rows = Sale.query.all()",
    'def ai_insights():\n    user_id = session["user_id"]\n    products = Product.query.filter_by(user_id=user_id).all()\n    sales_rows = Sale.query.filter_by(user_id=user_id).all()',
    "AI insights data",
)

replace_once(
    "    products = Product.query.all()\n    sales_rows = Sale.query.all()\n\n    low_stock = [",
    '    user_id = session["user_id"]\n    products = Product.query.filter_by(user_id=user_id).all()\n    sales_rows = Sale.query.filter_by(user_id=user_id).all()\n\n    low_stock = [',
    "AI chat data",
)

path.write_text(source)
print("Ownership patch applied successfully.")
