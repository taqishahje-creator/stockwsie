import os
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import uuid
from datetime import date, datetime, timedelta
from functools import wraps
from flask import (
    Flask, render_template, render_template_string, request, redirect,
    url_for, session, flash, jsonify
)
from flask_jwt_extended import (
    JWTManager, create_access_token, get_jwt_identity,
    set_access_cookies, unset_jwt_cookies, verify_jwt_in_request,
    get_csrf_token
)
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from email_validator import validate_email, EmailNotValidError
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from sqlalchemy.engine import make_url
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]
app.config["JWT_SECRET_KEY"] = os.environ["JWT_SECRET_KEY"]

app.config["JWT_TOKEN_LOCATION"] = ["cookies"]
app.config["JWT_ACCESS_COOKIE_NAME"] = "stockwise_access_token"
app.config["JWT_COOKIE_SECURE"] = (
    os.environ.get("JWT_COOKIE_SECURE", "False").lower() == "true"
)
app.config["JWT_COOKIE_CSRF_PROTECT"] = True
app.config["JWT_CSRF_CHECK_FORM"] = True
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=1)

app.config["MAIL_SERVER"] = os.environ["MAIL_SERVER"]
app.config["MAIL_PORT"] = int(os.environ["MAIL_PORT"])
app.config["MAIL_USE_TLS"] = (
    os.environ.get("MAIL_USE_TLS", "True").lower() == "true"
)
app.config["MAIL_USERNAME"] = os.environ["MAIL_USERNAME"]
app.config["MAIL_PASSWORD"] = os.environ["MAIL_PASSWORD"]
app.config["MAIL_DEFAULT_SENDER"] = os.environ["MAIL_DEFAULT_SENDER"]

jwt = JWTManager(app)
mail = Mail(app)
serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])
def generate_verification_token(email):
    """Create a signed token tied to one normalized email address."""
    return serializer.dumps(email.strip().lower(), salt="stockwise-email-verification")


def verify_email_token(token, max_age=3600):
    """Return the email from a valid, unexpired token, or None."""
    try:
        return serializer.loads(
            token,
            salt="stockwise-email-verification",
            max_age=max_age,
        )
    except SignatureExpired:
        app.logger.warning("Email verification token failure: expired")
        return None
    except BadSignature:
        app.logger.warning("Email verification token failure: invalid signature")
        return None

def send_verification_email(user):
    """Send the user a time-limited email verification link."""
    token = generate_verification_token(user.email)
    verification_url = url_for("verify_email", token=token, _external=True)
    message = Message(
        subject="Verify your StockWise email",
        recipients=[user.email],
        body=(
            f"Hello {user.username},\n\n"
            "Welcome to StockWise!\n\n"
            "Verify your email by opening this link:\n"
            f"{verification_url}\n\n"
            "This link expires in one hour. If you did not create this account, "
            "you can ignore this email.\n\n"
            "StockWise Team"
        ),
    )
    mail.send(message)


@app.context_processor
def inject_auth_template_values():
    """Expose the CSRF value for protected POST forms in templates."""
    return {"jwt_csrf_token": session.get("jwt_csrf_token", "")}

app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024



# Use a writable temporary folder on Vercel.
# Keep the original upload folder for local development.
if os.environ.get("VERCEL"):
    app.config["UPLOAD_FOLDER"] = "/tmp/stockwise_uploads"
else:
    app.config["UPLOAD_FOLDER"] = os.path.join(
        app.root_path, "static", "uploads"
    )

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


database_url = os.environ.get(
    "DATABASE_URL",
    "postgresql://inventory:inventorypass@db:5432/inventory"
)
parsed_url = make_url(database_url)
if parsed_url.drivername == "postgresql":
    database_url = parsed_url.set(
        drivername="postgresql+psycopg2"
    ).render_as_string(hide_password=False)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

ALLOWED_IMAGES = {"png", "jpg", "jpeg", "webp"}


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(254), unique=True, nullable=True)
    is_verified = db.Column(db.Boolean, nullable=False, default=False)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    sku = db.Column(db.String(40), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(80), nullable=False, default="General")
    description = db.Column(db.String(500), default="")
    quantity = db.Column(db.Integer, nullable=False, default=0)
    reorder_level = db.Column(db.Integer, nullable=False, default=5)
    cost_price = db.Column(db.Float, nullable=False, default=0)
    selling_price = db.Column(db.Float, nullable=False, default=0)
    image = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def availability(self):
        if self.quantity <= 0:
            return "Out of stock"
        if self.quantity <= self.reorder_level:
            return "Low stock"
        return "In stock"


class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    receipt_no = db.Column(
        db.String(40), unique=True, nullable=False,
        default=lambda: "SW-" + uuid.uuid4().hex[:10].upper()
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("product.id"), nullable=True
    )
    product_name = db.Column(db.String(120), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    unit_cost = db.Column(db.Float, nullable=False)
    sold_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    sold_by = db.Column(db.String(80), nullable=False)

    product = db.relationship("Product", backref="sales")

    @property
    def revenue(self):
        return self.quantity * self.unit_price

    @property
    def profit(self):
        return self.quantity * (self.unit_price - self.unit_cost)


class StockMovement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    product_id = db.Column(
        db.Integer, db.ForeignKey("product.id"), nullable=True
    )
    product_name = db.Column(db.String(120), nullable=False)
    movement_type = db.Column(db.String(30), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    note = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    username = db.Column(db.String(80), nullable=False)


def login_required(view):
    """Protect a route using the JWT access cookie (including CSRF checks)."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            verify_jwt_in_request()
            identity = get_jwt_identity()
            user = db.session.get(User, int(identity))
            if user is None or not user.is_verified:
                response = redirect(url_for("login"))
                unset_jwt_cookies(response)
                session.clear()
                return response
            session["user_id"] = user.id
            session["username"] = user.username
        except Exception:
            app.logger.exception("Authentication failed in login_required")
            session.clear()
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def save_product_image(file):
    if not file or not file.filename:
        return None
    filename = secure_filename(file.filename)
    if "." not in filename or filename.rsplit(".", 1)[1].lower() not in ALLOWED_IMAGES:
        raise ValueError("Use a PNG, JPG, JPEG, or WEBP image.")
    ext = filename.rsplit(".", 1)[1].lower()
    new_name = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(app.config["UPLOAD_FOLDER"], new_name))
    return new_name


def parse_number(field, cast, default=0):
    value = request.form.get(field, str(default)).strip()
    return cast(value)


def report_start(period):
    now = datetime.now()
    if period == "daily":
        return datetime.combine(date.today(), datetime.min.time())
    if period == "weekly":
        return datetime.combine(
            date.today() - timedelta(days=date.today().weekday()),
            datetime.min.time()
        )
    if period == "monthly":
        return datetime(now.year, now.month, 1)
    if period == "yearly":
        return datetime(now.year, 1, 1)
    return datetime(2000, 1, 1)


@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify(status="ok"), 200


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not username or not email or len(password) < 8:
            flash("Enter a username, a valid email address, and a password of at least 8 characters.", "error")
            return render_template("login.html", signup=True)

        try:
            email = validate_email(email, check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            flash("Enter a valid email address.", "error")
            return render_template("login.html", signup=True)

        if User.query.filter_by(username=username).first():
            flash("That username already exists.", "error")
            return render_template("login.html", signup=True)
        if User.query.filter_by(email=email).first():
            flash("That email address is already registered.", "error")
            return render_template("login.html", signup=True)

        user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            is_verified=False,
        )
        db.session.add(user)
        try:
            db.session.commit()
            send_verification_email(user)
        except Exception:
            db.session.rollback()
            app.logger.exception("StockWise signup/email delivery failed")
            # Keep the created account if mail sending failed after commit.
            user = User.query.filter_by(username=username).first()
            if user:
                flash("Your account was created, but the verification email could not be sent. Check the mail settings and try again.", "error")
                return render_template("login.html", signup=True)
            flash("We could not create your account. Please try again.", "error")
            return render_template("login.html", signup=True)

        flash("Account created! Check your inbox for the StockWise verification link. It expires in one hour.", "success")
        return redirect(url_for("login"))
    return render_template("login.html", signup=True)


@app.route("/verify-email/<token>", methods=["GET"])
def verify_email(token):
    email = verify_email_token(token)
    if not email:
        flash("This verification link is invalid or has expired. Please register again or request a new link.", "error")
        return redirect(url_for("login"))

    user = User.query.filter_by(email=email).first()
    if user is None:
        flash("No account was found for this verification link.", "error")
        return redirect(url_for("signup"))
    if user.is_verified:
        flash("Your email is already verified. Please log in.", "success")
        return redirect(url_for("login"))

    user.is_verified = True
    db.session.commit()
    flash("Email verified successfully! You can now log in.", "success")
    return redirect(url_for("login"))


@app.route("/resend-verification", methods=["GET", "POST"])
def resend_verification():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        # Use a generic message to avoid revealing registered email addresses.
        if user and not user.is_verified:
            try:
                send_verification_email(user)
            except Exception:
                app.logger.exception("StockWise verification email resend failed")
        flash("If that address belongs to an unverified account, a verification email has been sent.", "success")
        return redirect(url_for("login"))
    return render_template_string(
        """
        <!doctype html>
        <html lang="en">
        <head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Resend verification - StockWise</title></head>
        <body style="font-family:Arial,sans-serif;max-width:480px;margin:60px auto;padding:24px">
          <h1>StockWise</h1><h2>Resend verification email</h2>
          <form method="post">
            <label for="email">Email address</label><br>
            <input id="email" name="email" type="email" required style="width:100%;padding:10px;margin:10px 0 18px">
            <button type="submit">Send verification link</button>
          </form>
          <p><a href="/login">Back to login</a></p>
        </body></html>
        """
    )

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()

        # Always show the same message to protect account privacy.
        if user and user.is_verified and user.email:
            try:
                token = serializer.dumps(
                    {
                        "user_id": user.id,
                        "password_hash": user.password_hash,
                    },
                    salt="stockwise-password-reset",
                )

                reset_url = url_for(
                    "reset_password",
                    token=token,
                    _external=True,
                )

                message = Message(
                    subject="Reset your StockWise password",
                    recipients=[user.email],
                    body=(
                        "Hello,\n\n"
                        "We received a request to reset your StockWise password.\n\n"
                        f"Open this link to choose a new password:\n{reset_url}\n\n"
                        "This link expires in 30 minutes. If you did not "
                        "request a reset, you can ignore this email.\n"
                    ),
                )
                mail.send(message)

            except Exception:
                app.logger.exception("StockWise password reset email failed")

        flash(
            "If a verified account exists for that email, "
            "a password reset link has been sent.",
            "success",
        )
        return redirect(url_for("login"))

    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        data = serializer.loads(
            token,
            salt="stockwise-password-reset",
            max_age=1800,
        )
    except (SignatureExpired, BadSignature):
        flash(
            "This password reset link is invalid or has expired. Please request a new one.",
            "error",
        )
        return redirect(url_for("forgot_password"))

    if not isinstance(data, dict):
        flash("Invalid password reset link. Please request a new one.", "error")
        return redirect(url_for("forgot_password"))

    user = db.session.get(User, data.get("user_id"))

    # A password change invalidates previously issued reset links.
    if (
        not user
        or not user.is_verified
        or not user.email
        or user.password_hash != data.get("password_hash")
    ):
        flash(
            "This password reset link is no longer valid. Please request a new one.",
            "error",
        )
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(password) < 8:
            flash("Your new password must be at least 8 characters.", "error")
        elif password != confirm_password:
            flash("The passwords do not match.", "error")
        else:
            user.password_hash = generate_password_hash(password)
            db.session.commit()
            flash("Your password has been reset. Please log in.", "success")
            return redirect(url_for("login"))

    return render_template("reset_password.html")
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()

        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid username or password.", "error")
            return render_template("login.html", signup=False)

        if not user.email or not user.is_verified:
            flash("Please verify your email before logging in. Use the resend verification page if needed.", "error")
            return redirect(url_for("resend_verification"))

        session.clear()
        session["user_id"] = user.id
        session["username"] = user.username
        token = create_access_token(identity=str(user.id))
        session["jwt_csrf_token"] = get_csrf_token(token)
        response = redirect(url_for("dashboard"))
        set_access_cookies(response, token)
        return response

    return render_template("login.html", signup=False)


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    response = redirect(url_for("index"))
    unset_jwt_cookies(response)
    return response


@app.route("/dashboard")
@login_required
def dashboard():
    user_id = session["user_id"]
    products = Product.query.filter_by(user_id=user_id).all()
    sales = Sale.query.filter_by(user_id=user_id).order_by(Sale.sold_at.desc()).limit(8).all()
    today = datetime.combine(date.today(), datetime.min.time())
    today_sales = Sale.query.filter(Sale.user_id == user_id, Sale.sold_at >= today).all()
    low_stock = Product.query.filter(
        Product.user_id == user_id,
        Product.quantity <= Product.reorder_level
    ).order_by(Product.quantity.asc()).limit(8).all()

    return render_template(
        "dashboard.html",
        products=products,
        recent_sales=sales,
        low_stock=low_stock,
        product_count=len(products),
        stock_units=sum(p.quantity for p in products),
        stock_value=sum(p.quantity * p.cost_price for p in products),
        today_revenue=sum(s.revenue for s in today_sales),
        today_profit=sum(s.profit for s in today_sales),
        username=session["username"]
    )


@app.route("/products")
@login_required
def products():
    search = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    availability = request.args.get("availability", "").strip()

    query = Product.query.filter_by(user_id=session["user_id"])
    if search:
        query = query.filter(
            db.or_(
                Product.name.ilike(f"%{search}%"),
                Product.sku.ilike(f"%{search}%")
            )
        )
    if category:
        query = query.filter_by(category=category)
    if availability == "out":
        query = query.filter(Product.quantity <= 0)
    elif availability == "low":
        query = query.filter(
            Product.quantity > 0,
            Product.quantity <= Product.reorder_level
        )
    elif availability == "available":
        query = query.filter(Product.quantity > Product.reorder_level)

    items = query.order_by(Product.name.asc()).all()
    categories = [row[0] for row in db.session.query(Product.category)
                  .filter(Product.user_id == session["user_id"]).distinct().all()]
    return render_template(
        "products.html", products=items, categories=categories,
        q=search, selected_category=category, availability=availability
    )


@app.route("/products/add", methods=["GET", "POST"])
@login_required
def add_product():
    if request.method == "POST":
        try:
            sku = request.form.get("sku", "").strip()
            name = request.form.get("name", "").strip()
            category = request.form.get("category", "General").strip() or "General"
            quantity = parse_number("quantity", int)
            reorder = parse_number("reorder_level", int, 5)
            cost = parse_number("cost_price", float)
            price = parse_number("selling_price", float)
            if not sku or not name:
                raise ValueError("SKU and product name are required.")
            if min(quantity, reorder, cost, price) < 0:
                raise ValueError("Values cannot be negative.")
            if Product.query.filter_by(sku=sku).first():
                raise ValueError("That SKU already exists.")
            image = save_product_image(request.files.get("image")) or ""
            product = Product(
                user_id=session["user_id"],
                sku=sku, name=name, category=category,
                description=request.form.get("description", "").strip(),
                quantity=quantity, reorder_level=reorder,
                cost_price=cost, selling_price=price, image=image
            )
            db.session.add(product)
            db.session.flush()
            db.session.add(StockMovement(
                user_id=session["user_id"],
                product_id=product.id, product_name=name,
                movement_type="Initial stock", quantity=quantity,
                note="Product created", username=session["username"]
            ))
            db.session.commit()
            flash("Product added successfully.", "success")
            return redirect(url_for("products"))
        except (ValueError, TypeError) as exc:
            db.session.rollback()
            flash(str(exc) or "Please check the entered values.", "error")
    return render_template("product_form.html", product=None)


@app.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
def edit_product(product_id):
    product = Product.query.filter_by(id=product_id, user_id=session["user_id"]).first_or_404()
    if request.method == "POST":
        try:
            sku = request.form.get("sku", "").strip()
            name = request.form.get("name", "").strip()
            duplicate = Product.query.filter(
                Product.sku == sku, Product.id != product.id
            ).first()
            quantity = parse_number("quantity", int)
            reorder = parse_number("reorder_level", int, 5)
            cost = parse_number("cost_price", float)
            price = parse_number("selling_price", float)
            if not sku or not name or duplicate:
                raise ValueError("Enter a unique SKU and product name.")
            if min(quantity, reorder, cost, price) < 0:
                raise ValueError("Values cannot be negative.")
            image = save_product_image(request.files.get("image"))
            old_quantity = product.quantity
            product.sku = sku
            product.name = name
            product.category = request.form.get("category", "General").strip() or "General"
            product.description = request.form.get("description", "").strip()
            product.quantity = quantity
            product.reorder_level = reorder
            product.cost_price = cost
            product.selling_price = price
            if image:
                product.image = image
            if old_quantity != quantity:
                db.session.add(StockMovement(
                    user_id=session["user_id"],
                    product_id=product.id, product_name=name,
                    movement_type="Stock adjustment",
                    quantity=quantity - old_quantity,
                    note="Edited product quantity",
                    username=session["username"]
                ))
            db.session.commit()
            flash("Product updated.", "success")
            return redirect(url_for("products"))
        except (ValueError, TypeError) as exc:
            db.session.rollback()
            flash(str(exc) or "Please check the entered values.", "error")
    return render_template("product_form.html", product=product)


@app.route("/products/<int:product_id>/delete", methods=["POST"])
@login_required
def delete_product(product_id):
    product = Product.query.filter_by(id=product_id, user_id=session["user_id"]).first_or_404()
    has_sales = Sale.query.filter_by(product_id=product.id, user_id=session["user_id"]).first()
    has_movements = StockMovement.query.filter_by(product_id=product.id, user_id=session["user_id"]).first()
    if has_sales or has_movements:
        flash("This product has sales or stock-movement history and cannot be deleted. Set its stock to zero instead.", "error")
    else:
        db.session.delete(product)
        db.session.commit()
        flash("Product deleted.", "success")
    return redirect(url_for("products"))


@app.route("/sales", methods=["GET", "POST"])
@login_required
def sales():
    if request.method == "POST":
        try:
            product_id = parse_number("product_id", int)
            quantity = parse_number("quantity", int)

            unit_price_input = request.form.get("unit_price", "").strip()
            if unit_price_input:
                unit_price = parse_number("unit_price", float)
            else:
                unit_price = None

            product = Product.query.filter_by(
                id=product_id, user_id=session["user_id"]
            ).first()

            if not product:
                raise ValueError("Select a valid product.")
            if quantity <= 0:
                raise ValueError("Quantity must be greater than zero.")
            if product.quantity < quantity:
                raise ValueError("Insufficient stock available.")

            sale = Sale(
                user_id=session["user_id"],
                product_id=product.id,
                product_name=product.name,
                quantity=quantity,
                unit_price=(
                    unit_price
                    if unit_price is not None
                    else product.selling_price
                ),
                unit_cost=product.cost_price,
                sold_by=session["username"]
            )
            product.quantity -= quantity
            db.session.add(sale)
            db.session.add(StockMovement(
                user_id=session["user_id"],
                product_id=product.id,
                product_name=product.name,
                movement_type="Sale",
                quantity=-quantity,
                note=f"Receipt {sale.receipt_no}",
                username=session["username"]
            ))
            db.session.commit()
            flash(f"Sale recorded. Receipt: {sale.receipt_no}", "success")
            return redirect(url_for("receipt", sale_id=sale.id))
        except (ValueError, TypeError) as exc:
            db.session.rollback()
            flash(str(exc) or "Invalid sale details.", "error")

    history = Sale.query.filter_by(user_id=session["user_id"]).order_by(
        Sale.sold_at.desc()
    ).limit(300).all()
    return render_template(
        "sales.html",
        products=Product.query.filter(
            Product.user_id == session["user_id"],
            Product.quantity > 0
        ).order_by(Product.name).all(),
        sales=history
    )

@app.route("/receipt/<int:sale_id>")
@login_required
def receipt(sale_id):
    sale = Sale.query.filter_by(id=sale_id, user_id=session["user_id"]).first_or_404()
    return render_template("receipt.html", sale=sale)


@app.route("/reports")
@login_required
def reports():
    period = request.args.get("period", "monthly")
    if period not in {"daily", "weekly", "monthly", "yearly", "all"}:
        period = "monthly"
    start = report_start(period)
    query = Sale.query.filter(Sale.user_id == session["user_id"], Sale.sold_at >= start)
    sales_rows = query.order_by(Sale.sold_at.desc()).all()
    revenue = sum(s.revenue for s in sales_rows)
    cost = sum(s.quantity * s.unit_cost for s in sales_rows)
    profit = revenue - cost
    out_of_stock = Product.query.filter(Product.user_id == session["user_id"], Product.quantity <= 0).order_by(Product.name).all()
    low_stock = Product.query.filter(
        Product.user_id == session["user_id"], Product.quantity > 0,
        Product.quantity <= Product.reorder_level
    ).order_by(Product.quantity.asc()).all()
    movements = StockMovement.query.filter_by(user_id=session["user_id"]).order_by(
        StockMovement.created_at.desc()
    ).limit(300).all()

    return render_template(
        "reports.html", period=period, sales=sales_rows,
        revenue=revenue, cost=cost, profit=profit,
        out_of_stock=out_of_stock, low_stock=low_stock,
        movements=movements
    )


@app.route("/ai-insights")
@login_required
def ai_insights():
    user_id = session["user_id"]
    products = Product.query.filter_by(user_id=user_id).all()
    sales_rows = Sale.query.filter_by(user_id=user_id).all()
    low = [p for p in products if p.quantity <= p.reorder_level]
    out = [p for p in products if p.quantity <= 0]
    best = sorted(
        products,
        key=lambda p: sum(s.quantity for s in p.sales),
        reverse=True
    )[:5]
    total_revenue = sum(s.revenue for s in sales_rows)
    total_profit = sum(s.profit for s in sales_rows)
    recommendations = []
    for product in low:
        recommendations.append(
            f"Reorder {product.name}: current stock is {product.quantity}; "
            f"reorder threshold is {product.reorder_level}."
        )
    if not recommendations:
        recommendations.append("No low-stock alerts based on your current reorder levels.")
    if out:
        recommendations.append(
            "Out of stock: " + ", ".join(p.name for p in out[:10]) + "."
        )
    if best:
        recommendations.append(
            "Top movers by recorded units sold: " +
            ", ".join(p.name for p in best if sum(s.quantity for s in p.sales) > 0)
            or "No sales have been recorded yet."
        )
    if not sales_rows:
        recommendations.append("Record sales to unlock more meaningful sales analysis.")
    return render_template(
        "ai_insights.html", products=products, low_stock=low,
        out_of_stock=out, recommendations=recommendations,
        total_revenue=total_revenue, total_profit=total_profit
    )

@app.route("/ai-ask", methods=["POST"])
@login_required
def ai_ask():
    user_id = session["user_id"]

    payload = request.get_json(silent=True) or {}
    question = payload.get("question", "")

    if not isinstance(question, str) or not question.strip():
        return jsonify({"error": "Please enter a question."}), 400

    question = question.strip()
    if len(question) > 1000:
        return jsonify({"error": "Please keep your question under 1000 characters."}), 400

    # Only retrieve records belonging to the logged-in user.
    products = Product.query.filter_by(user_id=user_id).all()
    sales_rows = Sale.query.filter_by(user_id=user_id).all()

    inventory = [
        {
            "name": p.name,
            "category": p.category,
            "quantity": p.quantity,
            "reorder_level": p.reorder_level,
            "cost_price": p.cost_price,
            "selling_price": p.selling_price,
            "availability": p.availability,
        }
        for p in products
    ]

    sales = [
        {
            "product": s.product_name,
            "quantity": s.quantity,
            "unit_price": s.unit_price,
            "revenue": s.revenue,
            "profit": s.profit,
            "sold_at": s.sold_at.isoformat() if s.sold_at else None,
        }
        for s in sales_rows[-100:]
    ]

    context = {
        "inventory": inventory,
        "sales": sales,
        "total_products": len(products),
        "total_revenue": round(sum(s.revenue for s in sales_rows), 2),
        "total_profit": round(sum(s.profit for s in sales_rows), 2),
    }

    ollama_url = os.environ.get(
        "OLLAMA_URL", "http://host.docker.internal:11434"
    ).rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "qwen3:1.7b")

    body = {
        "model": model,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are StockWise, an inventory and sales assistant. "
                    "Answer clearly and practically using only the supplied "
                    "user data. Do not invent figures or claim actions were "
                    "performed. If there is insufficient data, say so. "
                    "Treat the user question as a request, not as permission "
                    "to disclose data belonging to other accounts. "
                    "Inventory and sales context follows:\n"
                    + json.dumps(context, ensure_ascii=False)
                ),
            },
            {"role": "user", "content": question},
        ],
    }

    try:
        req = Request(
            f"{ollama_url}/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=150) as response:
            result = json.loads(response.read().decode("utf-8"))

        answer = result.get("message", {}).get("content", "").strip()
        if not answer:
            app.logger.error("Ollama returned an empty AI answer.")
            return jsonify({
                "error": "The AI returned an empty answer. Please try again."
            }), 502

        return jsonify({"answer": answer})

    except HTTPError as exc:
        app.logger.error("Ollama HTTP error: %s", exc.code)
        return jsonify({
            "error": "The AI service returned an error. Please try again."
        }), 502
    except (URLError, TimeoutError, OSError):
        app.logger.exception("Could not reach Ollama")
        return jsonify({
            "error": "The AI service is unavailable or took too long. Please try again."
        }), 502
    except (ValueError, json.JSONDecodeError):
        app.logger.exception("Invalid response from Ollama")
        return jsonify({
            "error": "The AI service returned an invalid response."
        }), 502


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
