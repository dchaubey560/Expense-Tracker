import os, csv, io, json
from datetime import date, timedelta
from decimal import Decimal
from functools import wraps

from flask import (Flask, render_template, redirect, url_for, request,
                   flash, jsonify, session, Response, abort)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                          login_required, current_user)
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect
from wtforms import (StringField, PasswordField, SelectField, DecimalField,
                     TextAreaField, DateField, SubmitField)
from wtforms.validators import DataRequired, Email, EqualTo, Length, NumberRange, Optional
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import func

# ─────────────────────────────────────────────────
# APP CONFIG
# ─────────────────────────────────────────────────

app = Flask(__name__)
app.config['SECRET_KEY'] = 'flask-expense-tracker-secret-key-change-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///expense.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['WTF_CSRF_ENABLED'] = True

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'warning'

# ─────────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────────

class User(UserMixin, db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(254), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    preferred_currency = db.Column(db.String(10), default='INR')
    date_joined = db.Column(db.DateTime, default=db.func.now())
    is_admin = db.Column(db.Boolean, default=False, nullable=False)

    expenses = db.relationship('Expense', backref='user', lazy=True, cascade='all, delete-orphan')
    budgets = db.relationship('Budget', backref='user', lazy=True, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Category(db.Model):
    __tablename__ = 'category'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    icon = db.Column(db.String(50), default='fa-tag')
    color_hex = db.Column(db.String(7), default='#e74c3c')

    expenses = db.relationship('Expense', backref='category', lazy=True)
    budgets = db.relationship('Budget', backref='category', lazy=True)

    def __repr__(self):
        return self.name


class Expense(db.Model):
    __tablename__ = 'expense'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    date = db.Column(db.Date, nullable=False, default=date.today)
    description = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=db.func.now())

    def __repr__(self):
        return f'Expense ₹{self.amount} on {self.date}'


class Budget(db.Model):
    __tablename__ = 'budget'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    monthly_limit = db.Column(db.Numeric(10, 2), nullable=False)
    month = db.Column(db.Date, nullable=False)  # Always first day of month

    def get_spent(self):
        result = db.session.query(func.sum(Expense.amount)).filter(
            Expense.user_id == self.user_id,
            Expense.category_id == self.category_id,
            db.extract('year', Expense.date) == self.month.year,
            db.extract('month', Expense.date) == self.month.month
        ).scalar()
        return result or Decimal('0')

    def get_percentage(self):
        if self.monthly_limit > 0:
            return min(int((self.get_spent() / self.monthly_limit) * 100), 100)
        return 0

    def __repr__(self):
        return f'Budget: {self.category.name} {self.month.strftime("%B %Y")}'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ─────────────────────────────────────────────────
# FORMS
# ─────────────────────────────────────────────────

class RegisterForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(3, 150)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    preferred_currency = SelectField('Preferred Currency', choices=[
        ('INR', '₹ INR - Indian Rupee'),
        ('USD', '$ USD - US Dollar'),
        ('EUR', '€ EUR - Euro'),
        ('GBP', '£ GBP - British Pound'),
    ])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Create Account')


class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')


class ExpenseForm(FlaskForm):
    amount = DecimalField('Amount (₹)', validators=[DataRequired(), NumberRange(min=0.01)], places=2)
    category_id = SelectField('Category', coerce=int, validators=[Optional()])
    date = DateField('Date', validators=[DataRequired()], default=date.today)
    description = TextAreaField('Description (optional)', validators=[Optional()])
    submit = SubmitField('Save Expense')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.category_id.choices = [(0, '— Select Category —')] + [
            (c.id, c.name) for c in Category.query.order_by(Category.name).all()
        ]


class BudgetForm(FlaskForm):
    category_id = SelectField('Category', coerce=int, validators=[DataRequired()])
    monthly_limit = DecimalField('Monthly Limit (₹)', validators=[DataRequired(), NumberRange(min=1)], places=2)
    month = DateField('Month (pick any day in the month)', validators=[DataRequired()], default=date.today)
    submit = SubmitField('Save Budget')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.category_id.choices = [
            (c.id, c.name) for c in Category.query.order_by(Category.name).all()
        ]


class CategoryForm(FlaskForm):
    name = StringField('Category Name', validators=[DataRequired(), Length(2, 100)])
    icon = StringField('Font Awesome Icon Class', validators=[DataRequired()], default='fa-tag')
    color_hex = StringField('Color (hex)', validators=[DataRequired(), Length(7, 7)], default='#e74c3c')
    submit = SubmitField('Save Category')

# ─────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────

DEFAULT_CATEGORIES = [
    ('Food & Dining',    'fa-utensils',     '#e74c3c'),
    ('Rent & Housing',   'fa-home',         '#3498db'),
    ('Transportation',   'fa-car',          '#f39c12'),
    ('Entertainment',    'fa-film',         '#9b59b6'),
    ('Health & Medical', 'fa-heartbeat',    '#27ae60'),
    ('Shopping',         'fa-shopping-bag', '#e67e22'),
    ('Education',        'fa-book',         '#1abc9c'),
    ('Utilities',        'fa-bolt',         '#2980b9'),
    ('Subscriptions',    'fa-credit-card',  '#8e44ad'),
    ('Other',            'fa-tag',          '#95a5a6'),
]


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def seed_categories():
    for name, icon, color in DEFAULT_CATEGORIES:
        if not Category.query.filter_by(name=name).first():
            db.session.add(Category(name=name, icon=icon, color_hex=color))
    db.session.commit()


def check_budget_alert(user, expense):
    """Alert if budget is near/exceeded after adding an expense."""
    budgets = Budget.query.filter_by(
        user_id=user.id,
        category_id=expense.category_id
    ).all()
    for b in budgets:
        if b.month.year == expense.date.year and b.month.month == expense.date.month:
            pct = b.get_percentage()
            if pct >= 100:
                flash(f'⚠️ Budget EXCEEDED for {b.category.name}! '
                      f'Spent ₹{b.get_spent()} / Limit ₹{b.monthly_limit}', 'danger')
            elif pct >= 80:
                flash(f'🔔 {pct}% of your {b.category.name} budget used — '
                      f'₹{b.get_spent()} / ₹{b.monthly_limit}', 'warning')

# ─────────────────────────────────────────────────
# ROUTES — AUTH
# ─────────────────────────────────────────────────

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    form = RegisterForm()
    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data).first():
            flash('Username already taken. Choose another.', 'danger')
            return render_template('register.html', form=form)
        if User.query.filter_by(email=form.email.data).first():
            flash('Email already registered.', 'danger')
            return render_template('register.html', form=form)
        user = User(
            username=form.username.data,
            email=form.email.data,
            preferred_currency=form.preferred_currency.data
        )
        user.set_password(form.password.data)
        if User.query.count() == 0:
            user.is_admin = True
        db.session.add(user)
        db.session.commit()
        seed_categories()
        login_user(user)
        flash(f'Welcome, {user.username}! Start tracking your expenses.', 'success')
        return redirect(url_for('dashboard'))
    return render_template('register.html', form=form)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        flash('Invalid username or password.', 'danger')
    return render_template('login.html', form=form)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

# ─────────────────────────────────────────────────
# ROUTES — DASHBOARD
# ─────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    today = date.today()

    total_month = db.session.query(func.sum(Expense.amount)).filter(
        Expense.user_id == current_user.id,
        db.extract('year', Expense.date) == today.year,
        db.extract('month', Expense.date) == today.month
    ).scalar() or Decimal('0')

    total_today = db.session.query(func.sum(Expense.amount)).filter(
        Expense.user_id == current_user.id,
        Expense.date == today
    ).scalar() or Decimal('0')

    total_week_start = today - timedelta(days=today.weekday())
    total_week = db.session.query(func.sum(Expense.amount)).filter(
        Expense.user_id == current_user.id,
        Expense.date >= total_week_start,
        Expense.date <= today
    ).scalar() or Decimal('0')

    recent = Expense.query.filter_by(user_id=current_user.id)\
        .order_by(Expense.date.desc(), Expense.created_at.desc()).limit(5).all()

    budgets = Budget.query.filter(
        Budget.user_id == current_user.id,
        db.extract('year', Budget.month) == today.year,
        db.extract('month', Budget.month) == today.month
    ).all()

    return render_template('dashboard.html',
        total_month=total_month,
        total_today=total_today,
        total_week=total_week,
        recent_expenses=recent,
        budgets=budgets,
        current_month=today.strftime('%B %Y')
    )

# ─────────────────────────────────────────────────
# ROUTES — CHART JSON ENDPOINTS
# ─────────────────────────────────────────────────

@app.route('/api/chart/pie')
@login_required
def chart_pie():
    today = date.today()
    rows = db.session.query(Category.name, func.sum(Expense.amount))\
        .join(Expense, Expense.category_id == Category.id)\
        .filter(
            Expense.user_id == current_user.id,
            db.extract('year', Expense.date) == today.year,
            db.extract('month', Expense.date) == today.month
        ).group_by(Category.name).all()

    data = [['Category', 'Amount']] + [[r[0], float(r[1])] for r in rows]
    return jsonify({'data': data})


@app.route('/api/chart/line')
@login_required
def chart_line():
    today = date.today()
    chart_data = [['Month', 'Expenses']]
    for i in range(5, -1, -1):
        m = (today.replace(day=1) - timedelta(days=i * 28)).replace(day=1)
        total = db.session.query(func.sum(Expense.amount)).filter(
            Expense.user_id == current_user.id,
            db.extract('year', Expense.date) == m.year,
            db.extract('month', Expense.date) == m.month
        ).scalar() or 0
        chart_data.append([m.strftime('%b %Y'), float(total)])
    return jsonify({'data': chart_data})


@app.route('/api/chart/bar')
@login_required
def chart_bar():
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    chart_data = [['Day', 'Spent']]
    for i in range(7):
        day = week_start + timedelta(days=i)
        total = db.session.query(func.sum(Expense.amount)).filter(
            Expense.user_id == current_user.id,
            Expense.date == day
        ).scalar() or 0
        chart_data.append([day.strftime('%A'), float(total)])
    return jsonify({'data': chart_data})

# ─────────────────────────────────────────────────
# ROUTES — EXPENSES
# ─────────────────────────────────────────────────

@app.route('/expenses')
@login_required
def expenses():
    query = Expense.query.filter_by(user_id=current_user.id)
    category_id = request.args.get('category')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    if category_id:
        query = query.filter_by(category_id=int(category_id))
    if date_from:
        query = query.filter(Expense.date >= date_from)
    if date_to:
        query = query.filter(Expense.date <= date_to)

    exps = query.order_by(Expense.date.desc(), Expense.created_at.desc()).all()
    categories = Category.query.order_by(Category.name).all()
    return render_template('expenses.html', expenses=exps, categories=categories)


@app.route('/expenses/add', methods=['GET', 'POST'])
@login_required
def add_expense():
    form = ExpenseForm()
    if form.validate_on_submit():
        exp = Expense(
            user_id=current_user.id,
            amount=form.amount.data,
            category_id=form.category_id.data if form.category_id.data != 0 else None,
            date=form.date.data,
            description=form.description.data or ''
        )
        db.session.add(exp)
        db.session.commit()
        check_budget_alert(current_user, exp)
        flash('Expense added successfully!', 'success')
        return redirect(url_for('expenses'))
    return render_template('expense_form.html', form=form, action='Add')


@app.route('/expenses/edit/<int:pk>', methods=['GET', 'POST'])
@login_required
def edit_expense(pk):
    exp = Expense.query.filter_by(id=pk, user_id=current_user.id).first_or_404()
    form = ExpenseForm(obj=exp)
    if form.validate_on_submit():
        exp.amount = form.amount.data
        exp.category_id = form.category_id.data if form.category_id.data != 0 else None
        exp.date = form.date.data
        exp.description = form.description.data or ''
        db.session.commit()
        flash('Expense updated!', 'success')
        return redirect(url_for('expenses'))
    if exp.category_id:
        form.category_id.data = exp.category_id
    return render_template('expense_form.html', form=form, action='Edit')


@app.route('/expenses/delete/<int:pk>', methods=['GET', 'POST'])
@login_required
def delete_expense(pk):
    exp = Expense.query.filter_by(id=pk, user_id=current_user.id).first_or_404()
    if request.method == 'POST':
        db.session.delete(exp)
        db.session.commit()
        flash('Expense deleted.', 'info')
        return redirect(url_for('expenses'))
    return render_template('confirm_delete.html', obj=exp, type='Expense')

# ─────────────────────────────────────────────────
# ROUTES — BUDGETS
# ─────────────────────────────────────────────────

@app.route('/budgets')
@login_required
def budgets():
    all_budgets = Budget.query.filter_by(user_id=current_user.id)\
        .order_by(Budget.month.desc()).all()
    return render_template('budgets.html', budgets=all_budgets)


@app.route('/budgets/add', methods=['GET', 'POST'])
@login_required
def add_budget():
    form = BudgetForm()
    if form.validate_on_submit():
        month_first = form.month.data.replace(day=1)
        budget = Budget(
            user_id=current_user.id,
            category_id=form.category_id.data,
            monthly_limit=form.monthly_limit.data,
            month=month_first
        )
        db.session.add(budget)
        db.session.commit()
        flash('Budget set successfully!', 'success')
        return redirect(url_for('budgets'))
    return render_template('budget_form.html', form=form, action='Set')


@app.route('/budgets/delete/<int:pk>', methods=['POST'])
@login_required
def delete_budget(pk):
    budget = Budget.query.filter_by(id=pk, user_id=current_user.id).first_or_404()
    db.session.delete(budget)
    db.session.commit()
    flash('Budget removed.', 'info')
    return redirect(url_for('budgets'))

# ─────────────────────────────────────────────────
# ROUTES — EXPORT
# ─────────────────────────────────────────────────

@app.route('/export/csv')
@login_required
def export_csv():
    exps = Expense.query.filter_by(user_id=current_user.id)\
        .order_by(Expense.date.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Category', 'Amount (INR)', 'Description'])
    for e in exps:
        writer.writerow([
            e.date,
            e.category.name if e.category else 'N/A',
            float(e.amount), e.description
        ])
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=expenses_{date.today()}.csv'}
    )


@app.route('/export/pdf')
@login_required
def export_pdf():
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
    except ImportError:
        flash('ReportLab not installed. Run: pip install reportlab', 'danger')
        return redirect(url_for('dashboard'))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []

    today = date.today()
    elements.append(Paragraph('Expense Report', styles['Title']))
    elements.append(Paragraph(
        f'Generated: {today.strftime("%d %B %Y")}  |  User: {current_user.username}',
        styles['Normal']
    ))
    elements.append(Spacer(1, 0.3 * inch))

    total = db.session.query(func.sum(Expense.amount)).filter(
        Expense.user_id == current_user.id).scalar() or 0

    elements.append(Paragraph(f'Total Expenses (All Time): ₹{total}', styles['Normal']))
    elements.append(Spacer(1, 0.2 * inch))

    exps = Expense.query.filter_by(user_id=current_user.id)\
        .order_by(Expense.date.desc()).limit(50).all()

    data = [['Date', 'Category', 'Amount (₹)', 'Description']]
    for e in exps:
        data.append([
            str(e.date),
            e.category.name if e.category else 'N/A',
            str(e.amount),
            (e.description or '')[:40]
        ])

    table = Table(data, colWidths=[1*inch, 1.4*inch, 1*inch, 3*inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e74c3c')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
    ]))
    elements.append(table)
    doc.build(elements)
    buffer.seek(0)
    return Response(
        buffer.getvalue(),
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename=expenses_{today}.pdf'}
    )

# ─────────────────────────────────────────────────
# ROUTES — ADMIN PANEL
# ─────────────────────────────────────────────────

@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    total_users = User.query.count()
    total_expenses = Expense.query.count()
    total_spent = db.session.query(func.sum(Expense.amount)).scalar() or 0
    total_budgets = Budget.query.count()
    recent_users = User.query.order_by(User.date_joined.desc()).limit(5).all()
    recent_exps = Expense.query.order_by(Expense.created_at.desc()).limit(10).all()
    return render_template('admin_dashboard.html',
        total_users=total_users,
        total_expenses=total_expenses,
        total_spent=total_spent,
        total_budgets=total_budgets,
        recent_users=recent_users,
        recent_exps=recent_exps,
    )


@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    users = User.query.order_by(User.date_joined.desc()).all()
    return render_template('admin_users.html', users=users)


@app.route('/admin/users/<int:pk>/toggle-admin', methods=['POST'])
@login_required
@admin_required
def admin_toggle_admin(pk):
    user = User.query.get_or_404(pk)
    if user.id == current_user.id:
        flash("You cannot change your own admin status.", 'warning')
    else:
        user.is_admin = not user.is_admin
        db.session.commit()
        flash(f"{'Admin granted to' if user.is_admin else 'Admin revoked from'} {user.username}.", 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:pk>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_user(pk):
    user = User.query.get_or_404(pk)
    if user.id == current_user.id:
        flash("You cannot delete yourself.", 'danger')
    else:
        db.session.delete(user)
        db.session.commit()
        flash(f"User '{user.username}' deleted.", 'info')
    return redirect(url_for('admin_users'))


@app.route('/admin/expenses')
@login_required
@admin_required
def admin_expenses():
    exps = Expense.query.order_by(Expense.date.desc(), Expense.created_at.desc()).all()
    return render_template('admin_expenses.html', expenses=exps)


@app.route('/admin/expenses/<int:pk>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_expense(pk):
    exp = Expense.query.get_or_404(pk)
    db.session.delete(exp)
    db.session.commit()
    flash('Expense deleted.', 'info')
    return redirect(url_for('admin_expenses'))


@app.route('/admin/categories')
@login_required
@admin_required
def admin_categories():
    cats = Category.query.order_by(Category.name).all()
    form = CategoryForm()
    return render_template('admin_categories.html', categories=cats, form=form)


@app.route('/admin/categories/add', methods=['POST'])
@login_required
@admin_required
def admin_add_category():
    form = CategoryForm()
    if form.validate_on_submit():
        if Category.query.filter_by(name=form.name.data).first():
            flash('Category already exists.', 'warning')
        else:
            cat = Category(name=form.name.data, icon=form.icon.data, color_hex=form.color_hex.data)
            db.session.add(cat)
            db.session.commit()
            flash(f"Category '{cat.name}' added.", 'success')
    else:
        for field, errs in form.errors.items():
            for e in errs:
                flash(f"{field}: {e}", 'danger')
    return redirect(url_for('admin_categories'))


@app.route('/admin/categories/<int:pk>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_category(pk):
    cat = Category.query.get_or_404(pk)
    if cat.expenses:
        flash(f"Cannot delete '{cat.name}' — it has expenses linked.", 'danger')
    else:
        db.session.delete(cat)
        db.session.commit()
        flash(f"Category '{cat.name}' deleted.", 'info')
    return redirect(url_for('admin_categories'))

# ─────────────────────────────────────────────────
# CONTEXT PROCESSOR
# ─────────────────────────────────────────────────

@app.context_processor
def inject_now():
    return {'now': date.today()}

# ─────────────────────────────────────────────────
# INIT DB & RUN
# ─────────────────────────────────────────────────

with app.app_context():
    db.create_all()
    seed_categories()

if __name__ == '__main__':
    app.run(debug=True)
