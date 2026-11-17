import os, sqlite3, secrets, hashlib, hmac, json, csv, io
from datetime import datetime, date, timedelta
from functools import wraps
from flask import Flask, request, redirect, url_for, session, flash, render_template, make_response, send_file
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "scp.db")
SECRET = os.environ.get("SCP_SECRET_KEY")
if not SECRET:
    SECRET = secrets.token_hex(32)
    print("\n[AVISO] Chave de sessão temporária gerada. Defina SCP_SECRET_KEY para uso persistente.\n")

app = Flask(__name__)
app.secret_key = SECRET
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

ROLES = {
    "admin": "Administrador",
    "solicitante": "Solicitante",
    "aprovador": "Aprovador",
    "financeiro": "Responsável financeiro",
}

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con

def now():
    return datetime.now().isoformat(timespec="seconds")

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
          name TEXT NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS accounts(
          id INTEGER PRIMARY KEY AUTOINCREMENT, supplier TEXT NOT NULL,
          supplier_doc TEXT NOT NULL, invoice_ref TEXT NOT NULL, description TEXT NOT NULL,
          category TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
          issue_date TEXT NOT NULL, due_date TEXT NOT NULL, payment_method TEXT NOT NULL,
          requester_id INTEGER NOT NULL REFERENCES users(id),
          approver_id INTEGER NOT NULL REFERENCES users(id),
          status TEXT NOT NULL DEFAULT 'Pendente de aprovação',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          version INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS decisions(
          id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL REFERENCES accounts(id),
          user_id INTEGER NOT NULL REFERENCES users(id), decision TEXT NOT NULL,
          reason TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payments(
          id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL REFERENCES accounts(id),
          user_id INTEGER NOT NULL REFERENCES users(id), amount_cents INTEGER NOT NULL,
          paid_date TEXT NOT NULL, proof_ref TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit(
          id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER,
          user_id INTEGER NOT NULL REFERENCES users(id), action TEXT NOT NULL,
          old_value TEXT, new_value TEXT, reason TEXT, created_at TEXT NOT NULL
        );
        """)
        count = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        if count == 0:
            seeds = [
                ("admin", "Administrador SCP", "Admin@SCP2026!", "admin"),
                ("solicitante", "Usuário Solicitante", "Solicita@SCP2026!", "solicitante"),
                ("aprovador", "Gestor Aprovador", "Aprova@SCP2026!", "aprovador"),
                ("financeiro", "Responsável Financeiro", "Paga@SCP2026!", "financeiro"),
            ]
            for u,n,p,r in seeds:
                c.execute("INSERT INTO users(username,name,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                          (u,n,generate_password_hash(p),r,now()))

def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(24)
    return session["_csrf"]

@app.context_processor
def inject_globals():
    return {"csrf": csrf_token, "roles": ROLES, "current_user": current_user(), "nowdate": date.today().isoformat(), "today": date.today().isoformat()}

def current_user():
    uid = session.get("uid")
    if not uid: return None
    with db() as c:
        return c.execute("SELECT id,username,name,role,active FROM users WHERE id=?", (uid,)).fetchone()

@app.before_request
def protect_post():
    if request.method == "POST":
        token = request.form.get("_csrf", "")
        if not token or not hmac.compare_digest(token, session.get("_csrf", "")):
            return "Falha de segurança: token CSRF inválido. Volte e tente novamente.", 400

def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        u = current_user()
        if not u or not u["active"]:
            session.clear()
            return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapper

def roles_required(*allowed):
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*a, **kw):
            u = current_user()
            if u["role"] not in allowed:
                flash("Acesso negado: seu perfil não permite essa operação.", "error")
                return redirect(url_for("dashboard"))
            return fn(*a, **kw)
        return wrapper
    return deco

def audit(c, account_id, action, old=None, new=None, reason=None):
    u = current_user()
    c.execute("INSERT INTO audit(account_id,user_id,action,old_value,new_value,reason,created_at) VALUES(?,?,?,?,?,?,?)",
              (account_id, u["id"], action, json.dumps(old, ensure_ascii=False) if old is not None else None,
               json.dumps(new, ensure_ascii=False) if new is not None else None, reason, now()))

def money_to_cents(v):
    s = (v or "").strip().replace("R$", "").replace(" ", "")
    if "," in s: s = s.replace(".", "").replace(",", ".")
    return int(round(float(s) * 100))

def cents(v):
    return f"R$ {v/100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def normalize_text(value):
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())

def find_possible_duplicates(c, account, exclude_id=None):
    """Busca indícios; não declara duplicidade definitiva nem apaga registros."""
    rows = c.execute("SELECT id,supplier,supplier_doc,invoice_ref,amount_cents,due_date,status FROM accounts WHERE status NOT IN ('Rejeitado','Cancelado')").fetchall()
    doc = normalize_text(account.get("supplier_doc"))
    invoice = normalize_text(account.get("invoice_ref"))
    supplier = normalize_text(account.get("supplier"))
    matches = []
    for row in rows:
        if exclude_id is not None and row["id"] == exclude_id:
            continue
        same_doc = bool(doc) and doc == normalize_text(row["supplier_doc"])
        same_invoice = bool(invoice) and invoice == normalize_text(row["invoice_ref"])
        same_supplier_value_date = (supplier == normalize_text(row["supplier"]) and
                                     account.get("amount_cents") == row["amount_cents"] and
                                     account.get("due_date") == row["due_date"])
        if same_doc and (same_invoice or same_supplier_value_date):
            matches.append(row)
    return matches

@app.template_filter("money")
def money_filter(v): return cents(v)

@app.template_filter("datebr")
def date_filter(v):
    try: return datetime.strptime(v[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except: return v

@app.route("/")
def index():
    return redirect(url_for("dashboard") if current_user() else url_for("login"))

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        with db() as c:
            u = c.execute("SELECT * FROM users WHERE username=?", (request.form.get("username","").strip(),)).fetchone()
            if u and u["active"] and check_password_hash(u["password_hash"], request.form.get("password","")):
                session.clear()
                session["uid"] = u["id"]
                csrf_token()
                return redirect(url_for("dashboard"))
        flash("Usuário ou senha inválidos.", "error")
    return render_template("login.html")

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    u = current_user()
    with db() as c:
        if u["role"] == "admin":
            rows = c.execute("SELECT * FROM accounts ORDER BY due_date").fetchall()
        elif u["role"] == "solicitante":
            rows = c.execute("SELECT * FROM accounts WHERE requester_id=? ORDER BY due_date", (u["id"],)).fetchall()
        elif u["role"] == "aprovador":
            rows = c.execute("SELECT * FROM accounts WHERE approver_id=? ORDER BY due_date", (u["id"],)).fetchall()
        else:
            rows = c.execute("SELECT * FROM accounts WHERE status IN ('Aprovado','Pago') ORDER BY due_date").fetchall()
        today = date.today()
        limit = (today + timedelta(days=7)).isoformat()
        summary = {
            "total": len(rows),
            "pending": sum(x["status"] == "Pendente de aprovação" for x in rows),
            "approved": sum(x["status"] == "Aprovado" for x in rows),
            "paid": sum(x["status"] == "Pago" for x in rows),
            "overdue": sum(x["status"] not in ("Pago","Cancelado","Rejeitado") and x["due_date"] < today.isoformat() for x in rows),
            "due_soon": sum(x["status"] not in ("Pago","Cancelado","Rejeitado") and today.isoformat() <= x["due_date"] <= limit for x in rows),
            "approved_unpaid": sum(x["status"] == "Aprovado" for x in rows),
        }
        attention_rows = [x for x in rows if x["status"] == "Aprovado" or
                          (x["status"] not in ("Pago","Cancelado","Rejeitado") and x["due_date"] <= limit)]
        attention_rows = sorted(attention_rows, key=lambda x: x["due_date"])[:8]
    return render_template("dashboard.html", rows=rows, summary=summary, attention_rows=attention_rows, cents=cents)

@app.route("/accounts/new", methods=["GET","POST"])
@roles_required("solicitante","admin")
def account_new():
    with db() as c:
        approvers = c.execute("SELECT id,name FROM users WHERE role='aprovador' AND active=1 ORDER BY name").fetchall()
    if request.method == "POST":
        try:
            required = ["supplier","supplier_doc","invoice_ref","description","category","amount","issue_date","due_date","payment_method","approver_id"]
            missing = [x for x in required if not request.form.get(x,"").strip()]
            if missing: raise ValueError("Preencha todos os campos obrigatórios.")
            amount = money_to_cents(request.form["amount"])
            if amount <= 0: raise ValueError("O valor deve ser maior que zero.")
            if request.form["due_date"] < request.form["issue_date"]: raise ValueError("O vencimento não pode ser anterior à emissão.")
            with db() as c:
                u = current_user()
                ap = c.execute("SELECT id FROM users WHERE id=? AND role='aprovador' AND active=1", (request.form["approver_id"],)).fetchone()
                if not ap: raise ValueError("Selecione um aprovador ativo.")
                c.execute("""INSERT INTO accounts(supplier,supplier_doc,invoice_ref,description,category,amount_cents,issue_date,due_date,payment_method,requester_id,approver_id,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,'Pendente de aprovação',?,?)""",
                (request.form["supplier"].strip(),request.form["supplier_doc"].strip(),request.form["invoice_ref"].strip(),
                 request.form["description"].strip(),request.form["category"],amount,request.form["issue_date"],request.form["due_date"],
                 request.form["payment_method"],u["id"],ap["id"],now(),now()))
                aid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
                duplicate_matches = find_possible_duplicates(c, {
                    "supplier": request.form["supplier"].strip(),
                    "supplier_doc": request.form["supplier_doc"].strip(),
                    "invoice_ref": request.form["invoice_ref"].strip(),
                    "amount_cents": amount,
                    "due_date": request.form["due_date"],
                }, exclude_id=aid)
                audit(c, aid, "CADASTRO", None, {"status":"Pendente de aprovação","valor_centavos":amount,
                                                   "possiveis_duplicidades": [x["id"] for x in duplicate_matches]})
                if duplicate_matches:
                    ids = ", ".join(f"#{x['id']}" for x in duplicate_matches)
                    flash(f"ATENÇÃO: possível duplicidade com {ids}. O lançamento foi mantido, mas precisa ser conferido antes da aprovação.", "warning")
                else:
                    flash(f"Conta #{aid} cadastrada e enviada para aprovação.", "success")
            return redirect(url_for("dashboard"))
        except (ValueError, KeyError) as e:
            flash(str(e), "error")
    return render_template("account_form.html", approvers=approvers)

def visible_account(c, aid, u):
    a = c.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
    if not a: return None
    if u["role"] == "admin": return a
    if u["role"] == "solicitante" and a["requester_id"] == u["id"]: return a
    if u["role"] == "aprovador" and a["approver_id"] == u["id"]: return a
    if u["role"] == "financeiro" and a["status"] in ("Aprovado","Pago"): return a
    return None

@app.route("/accounts/<int:aid>")
@login_required
def account_detail(aid):
    u=current_user()
    with db() as c:
        a=visible_account(c,aid,u)
        if not a: return "Registro não encontrado ou acesso não autorizado.",404
        requester=c.execute("SELECT name FROM users WHERE id=?",(a["requester_id"],)).fetchone()
        approver=c.execute("SELECT name FROM users WHERE id=?",(a["approver_id"],)).fetchone()
        decisions=c.execute("""SELECT d.*,u.name FROM decisions d JOIN users u ON u.id=d.user_id WHERE account_id=? ORDER BY d.id DESC""",(aid,)).fetchall()
        payment=c.execute("""SELECT p.*,u.name FROM payments p JOIN users u ON u.id=p.user_id WHERE account_id=? ORDER BY p.id DESC""",(aid,)).fetchone()
        logs=c.execute("""SELECT a.*,u.name FROM audit a JOIN users u ON u.id=a.user_id WHERE account_id=? ORDER BY a.id ASC""",(aid,)).fetchall()
        duplicates=find_possible_duplicates(c, dict(a), exclude_id=aid)
    return render_template("detail.html", a=a, requester=requester, approver=approver, decisions=decisions, payment=payment, logs=logs, duplicates=duplicates, cents=cents)

@app.route("/accounts/<int:aid>/decision", methods=["POST"])
@roles_required("aprovador")
def decide(aid):
    decision=request.form.get("decision")
    reason=request.form.get("reason","").strip()
    if decision not in ("Aprovado","Rejeitado"): return "Decisão inválida.",400
    if decision=="Rejeitado" and not reason:
        flash("A justificativa é obrigatória para rejeitar.", "error")
        return redirect(url_for("account_detail",aid=aid))
    with db() as c:
        u=current_user(); a=c.execute("SELECT * FROM accounts WHERE id=?",(aid,)).fetchone()
        if not a or a["approver_id"] != u["id"]:
            return "Registro não encontrado ou acesso não autorizado.",404
        if a["status"] != "Pendente de aprovação":
            flash("Somente contas pendentes podem receber decisão.", "error")
        elif decision == "Aprovado" and find_possible_duplicates(c, dict(a), exclude_id=aid) and request.form.get("confirm_duplicate") != "yes":
            flash("Atenção: existe possível lançamento semelhante. Confira os registros relacionados e marque a confirmação antes de aprovar.", "warning")
        else:
            c.execute("UPDATE accounts SET status=?,updated_at=?,version=version+1 WHERE id=?",(decision,now(),aid))
            c.execute("INSERT INTO decisions(account_id,user_id,decision,reason,created_at) VALUES(?,?,?,?,?)",(aid,u["id"],decision,reason,now()))
            audit(c,aid,"DECISÃO",{"status":a["status"]},{"status":decision},reason)
            flash(f"Conta {decision.lower()} com sucesso.", "success")
    return redirect(url_for("account_detail",aid=aid))

@app.route("/accounts/<int:aid>/payment", methods=["POST"])
@roles_required("financeiro")
def payment(aid):
    try:
        amount=money_to_cents(request.form.get("amount",""))
        paid_date=request.form.get("paid_date","")
        proof=request.form.get("proof_ref","").strip()
        if amount<=0 or not paid_date or not proof: raise ValueError("Informe valor, data e comprovante/referência.")
        with db() as c:
            a=c.execute("SELECT * FROM accounts WHERE id=?",(aid,)).fetchone()
            if not a or a["status"]!="Aprovado":
                flash("Pagamento bloqueado: somente contas aprovadas podem ser pagas.", "error")
            else:
                u=current_user()
                c.execute("INSERT INTO payments(account_id,user_id,amount_cents,paid_date,proof_ref,created_at) VALUES(?,?,?,?,?,?)",(aid,u["id"],amount,paid_date,proof,now()))
                c.execute("UPDATE accounts SET status='Pago',updated_at=?,version=version+1 WHERE id=?",(now(),aid))
                audit(c,aid,"CONFIRMAÇÃO DE PAGAMENTO",{"status":"Aprovado"},{"status":"Pago","valor_pago_centavos":amount,"data":paid_date,"referencia":proof})
                flash("Pagamento registrado. O SCP não executou transferência bancária.", "success")
    except ValueError as e: flash(str(e),"error")
    return redirect(url_for("account_detail",aid=aid))

@app.route("/accounts/<int:aid>/cancel", methods=["POST"])
@roles_required("admin")
def cancel(aid):
    reason=request.form.get("reason","").strip()
    if not reason:
        flash("Justificativa obrigatória.", "error")
        return redirect(url_for("account_detail",aid=aid))
    with db() as c:
        a=c.execute("SELECT * FROM accounts WHERE id=?",(aid,)).fetchone()
        if not a or a["status"]=="Cancelado":
            flash("Conta não encontrada ou já cancelada.", "error")
        else:
            c.execute("UPDATE accounts SET status='Cancelado',updated_at=?,version=version+1 WHERE id=?",(now(),aid))
            audit(c,aid,"CANCELAMENTO",{"status":a["status"]},{"status":"Cancelado"},reason)
            flash("Conta cancelada com histórico preservado.", "success")
    return redirect(url_for("account_detail",aid=aid))

@app.route("/reports")
@roles_required("admin","financeiro","aprovador")
def reports():
    status=request.args.get("status","")
    start=request.args.get("start",""); end=request.args.get("end","")
    u=current_user()
    sql="SELECT * FROM accounts WHERE 1=1"; args=[]
    if u["role"]=="aprovador": sql+=" AND approver_id=?"; args.append(u["id"])
    if u["role"]=="financeiro": sql+=" AND status IN ('Aprovado','Pago')"
    if status: sql+=" AND status=?"; args.append(status)
    if start: sql+=" AND due_date>=?"; args.append(start)
    if end: sql+=" AND due_date<=?"; args.append(end)
    sql+=" ORDER BY due_date"
    with db() as c: rows=c.execute(sql,args).fetchall()
    return render_template("reports.html",rows=rows,status=status,start=start,end=end,total=sum(x["amount_cents"] for x in rows),cents=cents)

@app.route("/reports.csv")
@roles_required("admin","financeiro","aprovador")
def reports_csv():
    u=current_user()
    sql="SELECT a.id,a.supplier,a.invoice_ref,a.description,a.amount_cents,a.due_date,a.status FROM accounts a WHERE 1=1"; args=[]
    if u["role"]=="aprovador": sql+=" AND a.approver_id=?"; args.append(u["id"])
    if u["role"]=="financeiro": sql+=" AND a.status IN ('Aprovado','Pago')"
    with db() as c: rows=c.execute(sql+" ORDER BY a.due_date",args).fetchall()
    out=io.StringIO(); w=csv.writer(out,delimiter=";")
    w.writerow(["ID","Fornecedor","Documento","Descrição","Valor (centavos)","Vencimento","Status"])
    for r in rows: w.writerow(list(r))
    resp=make_response("\\ufeff"+out.getvalue())
    resp.headers["Content-Type"]="text/csv; charset=utf-8"
    resp.headers["Content-Disposition"]="attachment; filename=scp_relatorio.csv"
    return resp

@app.route("/users", methods=["GET","POST"])
@roles_required("admin")
def users():
    if request.method=="POST":
        try:
            username=request.form["username"].strip()
            name=request.form["name"].strip()
            password=request.form["password"]
            role=request.form["role"]
            if not username or not name or len(password)<10 or role not in ROLES: raise ValueError("Informe usuário, nome, perfil e senha com pelo menos 10 caracteres.")
            with db() as c:
                c.execute("INSERT INTO users(username,name,password_hash,role,active,created_at) VALUES(?,?,?,?,1,?)",
                          (username,name,generate_password_hash(password),role,now()))
                audit(c,None,"CRIAÇÃO DE USUÁRIO",None,{"username":username,"role":role})
            flash("Usuário criado.", "success")
        except sqlite3.IntegrityError: flash("Esse nome de usuário já existe.", "error")
        except (ValueError,KeyError) as e: flash(str(e),"error")
        return redirect(url_for("users"))
    with db() as c: rows=c.execute("SELECT id,username,name,role,active,created_at FROM users ORDER BY id").fetchall()
    return render_template("users.html",rows=rows)

@app.route("/users/<int:uid>/toggle", methods=["POST"])
@roles_required("admin")
def toggle_user(uid):
    with db() as c:
        u=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
        if not u: return "Usuário não encontrado.",404
        if u["id"]==current_user()["id"]:
            flash("Não é permitido desativar o próprio usuário.", "error")
        else:
            new=0 if u["active"] else 1
            c.execute("UPDATE users SET active=? WHERE id=?",(new,uid))
            audit(c,None,"ALTERAÇÃO DE USUÁRIO",{"active":u["active"]},{"active":new})
            flash("Situação do usuário atualizada.", "success")
    return redirect(url_for("users"))

@app.route("/audit")
@roles_required("admin")
def audit_page():
    with db() as c:
        rows=c.execute("""SELECT a.*,u.name FROM audit a JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 500""").fetchall()
    return render_template("audit.html",rows=rows)

@app.route("/backup")
@roles_required("admin")
def backup():
    path=os.path.join(BASE,"scp_backup_"+datetime.now().strftime("%Y%m%d_%H%M%S")+".db")
    with db() as c:
        dest=sqlite3.connect(path)
        c.backup(dest); dest.close()
    return send_file(path,as_attachment=True)

@app.errorhandler(500)
def error500(e):
    return render_template("error.html",message="Ocorreu um erro interno. Consulte o terminal do servidor para diagnóstico."),500

if __name__ == "__main__":
    init_db()
    print("SCP disponível em http://127.0.0.1:5000")
    app.run(host="127.0.0.1",port=5000,debug=False)
