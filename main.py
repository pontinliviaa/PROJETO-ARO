
from flask import Flask, render_template, request, flash, redirect, url_for, session, send_file
from datetime import date, timedelta
from calendar import monthrange
from decimal import Decimal, InvalidOperation
from flask_bcrypt import Bcrypt
import fdb
import os
from uuid import uuid4
from io import BytesIO
from fpdf import FPDF
from PIL import Image, UnidentifiedImageError

# CONFIGURAÇÃO DO FLASK
app = Flask(__name__, template_folder="Templates")
bcrypt = Bcrypt(app)
app.config["SECRET_KEY"] = "Chavesdfglkjhgfdshjkjhgfdcvbmnb"
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
# CONEXÃO COM O BANCO
host = "localhost"
database = r"C:\Users\Aluno\Downloads\PROJETO-ARO\BANCO.FDB"
user = "SYSDBA"
password = "sysdba"

con = fdb.connect(
    host=host,
    database=database,
    user=user,
    password=password,
    charset="UTF8"
)

TIPO_ADM = 1
TIPO_COMUM = 7

# VERIFICAÇÃO DE SENHA FORTE
def senha_forte(senha):
    if len(senha) < 8:
        return False

    tem_maiuscula = False
    tem_minuscula = False
    tem_numero = False
    tem_simbolo = False

    for caractere in senha:
        if caractere.isupper():
            tem_maiuscula = True
        if caractere.islower():
            tem_minuscula = True
        if caractere.isdigit():
            tem_numero = True
        if caractere in "!@#%":
            tem_simbolo = True
        if caractere.isspace():
            return False

    if tem_maiuscula and tem_minuscula and tem_numero and tem_simbolo:
        return True
    else:
        return False


# VALIDAR VALORES DE RECEITAS E DESPESAS

def valor_positivo(texto):
    texto = str(texto).strip().replace(",", ".")
    valor = Decimal(texto)
    if not valor.is_finite() or valor <= 0:
        raise ValueError("O valor deve ser maior que zero.")
    if valor > Decimal("9999999999.99") or valor != round(valor, 2):
        raise ValueError("Informe um valor com até duas casas decimais.")
    return valor


# DATAS DOS LANÇAMENTOS RECORRENTES

def datas_recorrentes(data_inicial, quantidade):
    datas = []
    ano = data_inicial.year
    mes = data_inicial.month
    for numero in range(quantidade):
        dia = data_inicial.day
        ultimo_dia = monthrange(ano, mes)[1]
        if dia > ultimo_dia:
            dia = ultimo_dia
        datas.append(date(ano, mes, dia))
        mes = mes + 1
        if mes > 12:
            mes = 1
            ano = ano + 1
    return datas


# CALCULAR E ARMAZENAR O SALDO DO MÊS

def atualizar_saldo_mensal(cursor, id_usuario, data_movimento):
    ano = data_movimento.year
    mes = data_movimento.month

    cursor.execute("""
        SELECT DATA_MOVIMENTO, VALOR FROM ARO_RECEITA
        WHERE ID_USUARIO = ?
    """, (id_usuario,))
    total_receitas = 0
    for receita in cursor.fetchall():
        if receita[0].year == ano and receita[0].month == mes:
            total_receitas = total_receitas + receita[1]

    cursor.execute("""
        SELECT DATA_MOVIMENTO, VALOR FROM ARO_DESPESA
        WHERE ID_USUARIO = ?
    """, (id_usuario,))
    total_despesas = 0
    for despesa in cursor.fetchall():
        if despesa[0].year == ano and despesa[0].month == mes:
            total_despesas = total_despesas + despesa[1]

    saldo = total_receitas - total_despesas
    cursor.execute("""
        SELECT ID_SALDO FROM ARO_SALDO_MENSAL
        WHERE ID_USUARIO = ? AND ANO = ? AND MES = ?
    """, (id_usuario, ano, mes))
    registro = cursor.fetchone()
    if registro:
        cursor.execute("""
            UPDATE ARO_SALDO_MENSAL
            SET TOTAL_RECEITAS = ?, TOTAL_DESPESAS = ?, SALDO = ?
            WHERE ID_USUARIO = ? AND ANO = ? AND MES = ?
        """, (total_receitas, total_despesas, saldo, id_usuario, ano, mes))
    else:
        cursor.execute("""
            INSERT INTO ARO_SALDO_MENSAL
            (ID_USUARIO, ANO, MES, TOTAL_RECEITAS, TOTAL_DESPESAS, SALDO)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (id_usuario, ano, mes, total_receitas, total_despesas, saldo))


# COMPARAR A SENHA DIGITADA COM O HASH SALVO
def verificar_senha(senha_hash, senha):
    return bcrypt.check_password_hash(senha_hash, senha)

# VERIFICA SE O USUÁRIO ESTÁ AUTENTICADO

def usuario_autenticado():
    if "id_usuario" not in session:
        return False

    cursor = con.cursor()
    try:
        cursor.execute("""SELECT ATIVO, BLOQUEADO_LOGIN, TIPO_USUARIO, NOME FROM USUARIO
                          WHERE ID_USUARIO = ?""", (session["id_usuario"],))
        usuario = cursor.fetchone()
        if usuario:
            if usuario[0] == 1 and (usuario[1] or 0) == 0:
                session["tipo_usuario"] = usuario[2]
                session["nome_usuario"] = usuario[3]
                return True

        return False
    except Exception:
        return False
    finally:
        con.rollback()
        cursor.close()

# PÁGINA INICIAL DO SITE

@app.route("/")
def landing():
    return render_template("landing.html")

# PÁGINA DO USUÁRIO LOGADO

@app.route("/novo")
def novo():
    if not usuario_autenticado():
        session.clear()

        flash("Precisa estar logado e ativo!", "error")

        return redirect(url_for("login"))

    if "nome_usuario" not in session:
        session.clear()

        flash("Faça login novamente!", "error")

        return redirect(url_for("login"))

    if session["tipo_usuario"] == TIPO_ADM:
        return redirect(url_for("admin"))

    return render_template("inicio.html", usuario=session["nome_usuario"])

# PÁGINA DE CADASTRO

@app.route("/cadastro")
def pagina_cadastro():
    return render_template("cadastro.html")

# CADASTRAR USUÁRIO

@app.route("/cadastro", methods=["POST"])
def cadastro():
    nome = request.form.get("nome", "").strip()
    email = request.form.get("email", "").strip()
    senha = request.form.get("senha", "")
    confirmar_senha = request.form.get("confirmar_senha", "")
    tipo_usuario = TIPO_COMUM

    if not nome or not email or not senha or not confirmar_senha:
        flash("Preencha todos os campos!", "error")
        return redirect(url_for("pagina_cadastro"))

    if senha != confirmar_senha:
        flash("A senha e a confirmação de senha não são iguais!", "error")
        return redirect(url_for("pagina_cadastro"))

    if not senha_forte(senha):
        flash(
            "A senha deve ter pelo menos 8 caracteres, letras maiúsculas, minúsculas, números e um símbolo (! @ # %) e sem espaços. ",
            "error"
        )

        return redirect(url_for("pagina_cadastro"))

    cursor = con.cursor()
    try:
        cursor.execute("""SELECT ID_USUARIO FROM USUARIO
                          WHERE UPPER(EMAIL) = UPPER(?)""", (email,))

        if cursor.fetchone():
            flash("Este e-mail já está cadastrado!", "error")
            return redirect(url_for("pagina_cadastro"))
        senha_hash = bcrypt.generate_password_hash(senha).decode("utf-8")
        cursor.execute("""INSERT INTO USUARIO
                          (NOME, EMAIL, SENHA, TIPO_USUARIO, ATIVO,
                           TENTATIVAS_LOGIN, BLOQUEADO_LOGIN)
                          VALUES (?, ?, ?, ?, 1, 0, 0)""",
                       (nome, email, senha_hash, tipo_usuario))
        con.commit()
        flash("Cadastro realizado com sucesso!", "success")
        return redirect(url_for("login"))

    except Exception as e:
        con.rollback()
        flash(f"Erro ao cadastrar: {e}", "error")
        return redirect(url_for("pagina_cadastro"))
    finally:
        con.rollback()
        cursor.close()

# PÁGINA DE LOGIN

@app.route("/login")
def login():
    return render_template("login.html")

# ENTRAR NA CONTA

@app.route("/login", methods=["POST"])
def entrar_usuario():
    email = request.form.get("email", "").strip()

    senha = request.form.get("senha", "")

    if not email or not senha:
        flash("Preencha o e-mail e a senha!", "error")
        return redirect(url_for("login"))

    cursor = con.cursor()
    try:
        cursor.execute("""SELECT ID_USUARIO, NOME, SENHA, ATIVO,
                          TENTATIVAS_LOGIN, BLOQUEADO_LOGIN, TIPO_USUARIO
                          FROM USUARIO WHERE UPPER(EMAIL) = UPPER(?)""", (email,))
        usuario = cursor.fetchone()

        if not usuario:
            flash("E-mail ou senha incorretos!", "error")
            return redirect(url_for("login"))

        id_usuario, nome, senha_hash, ativo, tentativas, bloqueado, tipo_usuario = usuario

        if ativo != 1:
            flash("Usuário inativo!", "error")
            return redirect(url_for("login"))

        tentativas = tentativas or 0
        bloqueado = bloqueado or 0

        if bloqueado == 1 or tentativas >= 3:
            flash("Usuário bloqueado! Solicite o desbloqueio.", "error")
            return redirect(url_for("login"))

        if verificar_senha(senha_hash, senha):
            cursor.execute("""UPDATE USUARIO SET TENTATIVAS_LOGIN = 0
                              WHERE ID_USUARIO = ?""", (id_usuario,))
            con.commit()

            session.clear()
            session["id_usuario"] = id_usuario
            session["nome_usuario"] = nome
            session["tipo_usuario"] = tipo_usuario
            flash("Login realizado com sucesso!", "success")

            if tipo_usuario == TIPO_ADM:
                return redirect(url_for("admin"))
            return redirect(url_for("novo"))

        tentativas = tentativas + 1

        if tentativas >= 3:
            bloqueado = 1
            flash("Usuário bloqueado após 3 tentativas!", "error")
        else:
            flash(f"Senha incorreta! Restam {3 - tentativas} tentativa(s).", "error")

        cursor.execute("""UPDATE USUARIO
                          SET TENTATIVAS_LOGIN = ?, BLOQUEADO_LOGIN = ?
                          WHERE ID_USUARIO = ?""", (tentativas, bloqueado, id_usuario))
        con.commit()
        return redirect(url_for("login"))

    except Exception:
        con.rollback()
        flash("Não foi possível entrar. Verifique a conexão com o banco.", "error")
        return redirect(url_for("login"))
    finally:
        con.rollback()
        cursor.close()

# LOGOUT

@app.route("/logout", methods=["POST"])
def logout():
    if "id_usuario" not in session:
        flash("Precisa estar logado!", "error")

        return redirect(url_for("login"))

    session.clear()

    flash("Você saiu da sua conta!", "success")

    return redirect(url_for("login"))

# EDITAR USUÁRIO

@app.route("/editar_usuario")
def editar_usuario():
    if not usuario_autenticado():
        session.clear()

        flash("Precisa estar logado e ativo!", "error")

        return redirect(url_for("login"))

    cursor = con.cursor()
    try:
        cursor.execute("""SELECT ID_USUARIO, NOME, EMAIL, RECEITA_MENSAL, DESPESA_MENSAL
                          FROM USUARIO WHERE ID_USUARIO = ?""", (session["id_usuario"],))

        usuario = cursor.fetchone()

        if not usuario:
            session.clear()

            flash("Usuário não encontrado!", "error")

            return redirect(url_for("login"))

        return render_template("editar_usuario.html", usuario=usuario)
    finally:
        con.rollback()
        cursor.close()

# SALVAR EDIÇÃO DO USUÁRIO

@app.route("/salvar_edicao", methods=["POST"])
def salvar_edicao():
    if not usuario_autenticado():
        session.clear()

        flash("Precisa estar logado e ativo!", "error")

        return redirect(url_for("login"))

    nome = request.form.get("nome", "").strip()

    email = request.form.get("email", "").strip()

    senha = request.form.get("senha", "")

    confirmar_senha = request.form.get("confirmar_senha", "")


    try:
        renda_mensal = Decimal(request.form.get("renda_mensal", "0").replace(",", "."))
        despesas_mensal = Decimal(request.form.get("despesas_mensal", "0").replace(",", "."))
        for valor in (renda_mensal, despesas_mensal):
            if not valor.is_finite() or valor < 0 or valor > Decimal("9999999999.99"):
                raise ValueError("Valor mensal inválido.")
            if valor != round(valor, 2):
                raise ValueError("Use até duas casas decimais.")
    except (ValueError, InvalidOperation):
        flash("Informe renda e despesas mensais válidas, sem valores negativos e com até duas casas decimais.", "error")
        return redirect(url_for("editar_usuario"))

    if not nome or not email:
        flash("Preencha todos os campos!", "error")

        return redirect(url_for("editar_usuario"))

    if senha != confirmar_senha:
        flash("A nova senha e a confirmação não são iguais!", "error")

        return redirect(url_for("editar_usuario"))

    if senha and not senha_forte(senha):
        flash(
            "A nova senha deve ter pelo menos 8 caracteres, letras maiúsculas, minúsculas, números e um símbolo (! @ # %), sem espaços.",
            "error")

        return redirect(url_for("editar_usuario"))

    cursor = con.cursor()
    try:

        cursor.execute("""SELECT ID_USUARIO FROM USUARIO WHERE UPPER(EMAIL) = UPPER(?) AND ID_USUARIO <> ?""", (email, session["id_usuario"]))

        if cursor.fetchone():
            flash("Este e-mail já está cadastrado!", "error")

            return redirect(url_for("editar_usuario"))


        if senha:
            cursor.execute("""SELECT SENHA, SENHA_ANTERIOR1, SENHA_ANTERIOR2 FROM USUARIO WHERE ID_USUARIO = ?""", (session["id_usuario"],))

            dados = cursor.fetchone()

            if not dados:
                session.clear()

                flash("Usuário não encontrado!", "error")

                return redirect(url_for("login"))


            for senha_antiga in dados:
                if senha_antiga:
                    if verificar_senha(senha_antiga, senha):
                        flash(
                            "Você não pode reutilizar as 3 últimas senhas (a atual e as 2 anteriores)!",
                            "error"
                        )

                        return redirect(url_for("editar_usuario"))


            senha_hash = bcrypt.generate_password_hash(senha).decode("utf-8")


            cursor.execute("""UPDATE USUARIO SET NOME = ?, EMAIL = ?, RECEITA_MENSAL = ?, DESPESA_MENSAL = ?, SENHA_ANTERIOR3 = NULL, SENHA_ANTERIOR2 = SENHA_ANTERIOR1, SENHA_ANTERIOR1 = SENHA, SENHA = ? WHERE ID_USUARIO = ?""", (
                nome,
                email,
                renda_mensal,
                despesas_mensal,
                senha_hash,
                session["id_usuario"]
            ))

        else:
            cursor.execute("""UPDATE USUARIO SET NOME = ?, EMAIL = ?, RECEITA_MENSAL = ?, DESPESA_MENSAL = ? WHERE ID_USUARIO = ?""", (
                nome,
                email,
                renda_mensal,
                despesas_mensal,
                session["id_usuario"]
            ))


        con.commit()

        session["nome_usuario"] = nome

        flash("Usuário atualizado com sucesso!", "success")

        return redirect(url_for("novo"))

    except Exception as e:
        con.rollback()

        flash(f"Erro ao editar usuário: {e}", "error")

        return redirect(url_for("editar_usuario"))
    finally:
        con.rollback()
        cursor.close()

# SALDO ATUAL

@app.route("/saldo_atual")
def saldo_atual():
    if not usuario_autenticado():
        return redirect(url_for("login"))
    hoje = date.today()
    cursor = con.cursor()
    try:
        cursor.execute("SELECT VALOR FROM ARO_RECEITA WHERE ID_USUARIO = ? AND DATA_MOVIMENTO <= ?", (session["id_usuario"], hoje))
        total_receitas = Decimal("0")
        for receita in cursor.fetchall():
            total_receitas = total_receitas + receita[0]
        cursor.execute("SELECT VALOR FROM ARO_DESPESA WHERE ID_USUARIO = ? AND DATA_MOVIMENTO <= ?", (session["id_usuario"], hoje))
        total_despesas = Decimal("0")
        for despesa in cursor.fetchall():
            total_despesas = total_despesas + despesa[0]
        total_receitas = round(total_receitas, 2)
        total_despesas = round(total_despesas, 2)
        saldo = total_receitas - total_despesas
        return render_template("saldo_atual.html", total_receitas=total_receitas,
                               total_despesas=total_despesas, saldo=saldo, hoje=hoje)
    finally:
        con.rollback()
        cursor.close()


# PÁGINA DO PERFIL DO USUÁRIO

@app.route("/perfil")
def perfil():
    if not usuario_autenticado():
        flash("Precisa estar logado!", "error")

        return redirect(url_for("login"))

    cursor = con.cursor()
    try:
        cursor.execute("""SELECT ID_USUARIO, NOME, EMAIL, FOTO FROM USUARIO
            WHERE ID_USUARIO = ?""", (session["id_usuario"],))

        usuario = cursor.fetchone()

        if not usuario:
            flash("Usuário não encontrado!", "error")
            return redirect(url_for("login"))

        return render_template("perfil.html", usuario=usuario)

    finally:
        con.rollback()
        cursor.close()

# PÁGINA DO PERFIL DO ADM

@app.route("/perfil_adm")
def perfil_adm():
    if not usuario_autenticado():
        flash("Precisa estar logado!", "error")

        return redirect(url_for("login"))

    if session["tipo_usuario"] != TIPO_ADM:
        return redirect(url_for("perfil"))

    cursor = con.cursor()
    try:
        cursor.execute("""SELECT ID_USUARIO, NOME, EMAIL, FOTO FROM USUARIO
            WHERE ID_USUARIO = ?""", (session["id_usuario"],))

        usuario = cursor.fetchone()

        if not usuario:
            flash("Usuário não encontrado!", "error")
            return redirect(url_for("login"))

        return render_template("perfil_adm.html", usuario=usuario)

    finally:
        con.rollback()
        cursor.close()

# CADASTRAR, EDITAR E LISTAR RECEITAS

@app.route("/receitas", methods=["GET", "POST"])
@app.route("/receitas/<int:id_registro>/editar", methods=["GET", "POST"])
def receitas(id_registro=None):
    if not usuario_autenticado():
        return redirect(url_for("login"))

    id_usuario = session["id_usuario"]
    cursor = con.cursor()
    registro = None
    try:
        if id_registro is not None:
            cursor.execute("SELECT ID_EMPRESTIMO FROM ARO_RECEITA WHERE ID_REGISTRO = ? AND ID_USUARIO = ?", (id_registro, id_usuario))
            vinculo = cursor.fetchone()
            if vinculo and vinculo[0] is not None:
                flash("Lançamento vinculado a empréstimo: consulte o contrato em Empréstimos.", "error")
                return redirect(url_for("receitas"))
            cursor.execute("""SELECT ID_REGISTRO, DESCRICAO, VALOR, DATA_MOVIMENTO, CLASSIFICACAO
                FROM ARO_RECEITA WHERE ID_REGISTRO = ? AND ID_USUARIO = ?""", (id_registro, id_usuario))
            registro = cursor.fetchone()
            if registro is None:
                flash("Lançamento não encontrado.", "error")
                return redirect(url_for("receitas"))

        if request.method == "POST":
            descricao = request.form.get("descricao", "").strip()
            valor = valor_positivo(request.form.get("valor", ""))
            data_movimento = date.fromisoformat(request.form.get("data", ""))
            classificacao = request.form.get("classificacao", "Variavel")
            if not descricao or len(descricao) > 100:
                flash("Informe uma descrição de até 100 caracteres.", "error")
                return redirect(url_for("receitas", id_registro=id_registro))
            if classificacao != "Variavel" and classificacao != "Recorrente":
                flash("Escolha Variável ou Recorrente.", "error")
                return redirect(url_for("receitas", id_registro=id_registro))

            quantidade = 1
            if registro is None and classificacao == "Recorrente":
                quantidade = int(request.form.get("quantidade_meses", "1"))
                if quantidade < 1 or quantidade > 60:
                    flash("Escolha de 1 a 60 meses, incluindo o mês inicial.", "error")
                    return redirect(url_for("receitas"))
            datas = datas_recorrentes(data_movimento, quantidade)

            if classificacao == "Recorrente":
                cursor.execute("""SELECT ID_REGISTRO, DATA_MOVIMENTO FROM ARO_RECEITA
                    WHERE ID_USUARIO = ? AND UPPER(TRIM(DESCRICAO)) = UPPER(TRIM(?))
                    AND CLASSIFICACAO = 'Recorrente'""", (id_usuario, descricao))
                existentes = cursor.fetchall()
                for data in datas:
                    for existente in existentes:
                        mesmo_registro = registro is not None and existente[0] == registro[0]
                        if not mesmo_registro and existente[1].year == data.year and existente[1].month == data.month:
                            flash("Já existe um lançamento recorrente com esse nome no mês " + str(data.month) + "/" + str(data.year) + ". Nada foi salvo.", "error")
                            return redirect(url_for("receitas", id_registro=id_registro))

            if registro is None:
                for data in datas:
                    cursor.execute("""INSERT INTO ARO_RECEITA
                        (ID_USUARIO, DESCRICAO, VALOR, DATA_MOVIMENTO, CLASSIFICACAO)
                        VALUES (?, ?, ?, ?, ?)""", (id_usuario, descricao, valor, data, classificacao))
                    atualizar_saldo_mensal(cursor, id_usuario, data)
                mensagem = "Lançamento cadastrado com sucesso!"
                if classificacao == "Recorrente":
                    mensagem = str(quantidade) + " lançamentos programados. Cada um entra no saldo na sua data."
            else:
                cursor.execute("""UPDATE ARO_RECEITA SET DESCRICAO = ?, VALOR = ?,
                    DATA_MOVIMENTO = ?, CLASSIFICACAO = ? WHERE ID_REGISTRO = ? AND ID_USUARIO = ?""",
                    (descricao, valor, data_movimento, classificacao, id_registro, id_usuario))
                atualizar_saldo_mensal(cursor, id_usuario, data_movimento)
                if registro[3] != data_movimento:
                    atualizar_saldo_mensal(cursor, id_usuario, registro[3])
                mensagem = "Somente este lançamento foi atualizado."
            con.commit()
            flash(mensagem, "success")
            return redirect(url_for("receitas"))

        cursor.execute("""SELECT ID_REGISTRO, DESCRICAO, VALOR, DATA_MOVIMENTO, CLASSIFICACAO
            FROM ARO_RECEITA WHERE ID_USUARIO = ? ORDER BY DATA_MOVIMENTO DESC, ID_REGISTRO DESC""", (id_usuario,))
        registros = []
        agendados = []
        hoje = date.today()
        for lancamento in cursor.fetchall():
            if lancamento[3] <= hoje:
                registros.append(lancamento)
            else:
                agendados.append(lancamento)
        agendados.reverse()
        return render_template("receitas.html", registros=registros, agendados=agendados,
                               registro=registro, hoje=hoje.isoformat())
    except (ValueError, InvalidOperation):
        con.rollback()
        flash("Confira a data, o valor positivo com até duas casas decimais e a quantidade inteira de meses.", "error")
        return redirect(url_for("receitas", id_registro=id_registro))
    except Exception:
        con.rollback()
        app.logger.exception("Erro ao consultar ou salvar receitas")
        flash("Não foi possível salvar. Nenhum lançamento desta operação foi confirmado.", "error")
        return redirect(url_for("receitas"))
    finally:
        con.rollback()
        cursor.close()


# EXCLUIR RECEITA

@app.route("/receitas/<int:id_registro>/excluir", methods=["POST"])
def excluir_receita(id_registro):
    if not usuario_autenticado():
        session.clear()
        flash("Faça login para continuar.", "error")
        return redirect(url_for("login"))

    id_usuario = session["id_usuario"]
    cursor = con.cursor()

    try:
        if id_registro is not None:
            cursor.execute("SELECT ID_EMPRESTIMO FROM ARO_RECEITA WHERE ID_REGISTRO = ? AND ID_USUARIO = ?", (id_registro, id_usuario))
            vinculo = cursor.fetchone()
            if vinculo and vinculo[0] is not None:
                flash("Lançamento vinculado a empréstimo: consulte o contrato em Empréstimos.", "error")
                return redirect(url_for("receitas"))

        cursor.execute("""
            SELECT DATA_MOVIMENTO
            FROM ARO_RECEITA
            WHERE ID_REGISTRO = ? AND ID_USUARIO = ?
        """, (id_registro, id_usuario))

        registro = cursor.fetchone()

        if registro is None:
            flash("Registro não encontrado.", "error")

        else:
            cursor.execute("""
                DELETE FROM ARO_RECEITA
                WHERE ID_REGISTRO = ? AND ID_USUARIO = ?
            """, (id_registro, id_usuario))

            atualizar_saldo_mensal(cursor, id_usuario, registro[0])
            con.commit()

            flash("Receita excluída com sucesso!", "success")

    except Exception:
        con.rollback()
        app.logger.exception("Erro ao excluir receita")
        flash("Não foi possível excluir a receita.", "error")

    finally:
        con.rollback()
        cursor.close()

    return redirect(url_for("receitas"))


# CADASTRAR, EDITAR E LISTAR DESPESAS

@app.route("/despesas", methods=["GET", "POST"])
@app.route("/despesas/<int:id_registro>/editar", methods=["GET", "POST"])
def despesas(id_registro=None):
    if not usuario_autenticado():
        return redirect(url_for("login"))

    id_usuario = session["id_usuario"]
    cursor = con.cursor()
    registro = None
    try:
        if id_registro is not None:
            cursor.execute("SELECT ID_EMPRESTIMO FROM ARO_DESPESA WHERE ID_REGISTRO = ? AND ID_USUARIO = ?", (id_registro, id_usuario))
            vinculo = cursor.fetchone()
            if vinculo and vinculo[0] is not None:
                flash("Lançamento vinculado a empréstimo: consulte o contrato em Empréstimos.", "error")
                return redirect(url_for("despesas"))
            cursor.execute("""SELECT ID_REGISTRO, DESCRICAO, VALOR, DATA_MOVIMENTO, CLASSIFICACAO
                FROM ARO_DESPESA WHERE ID_REGISTRO = ? AND ID_USUARIO = ?""", (id_registro, id_usuario))
            registro = cursor.fetchone()
            if registro is None:
                flash("Lançamento não encontrado.", "error")
                return redirect(url_for("despesas"))

        if request.method == "POST":
            descricao = request.form.get("descricao", "").strip()
            valor = valor_positivo(request.form.get("valor", ""))
            data_movimento = date.fromisoformat(request.form.get("data", ""))
            classificacao = request.form.get("classificacao", "Variavel")
            if not descricao or len(descricao) > 100:
                flash("Informe uma descrição de até 100 caracteres.", "error")
                return redirect(url_for("despesas", id_registro=id_registro))
            if classificacao != "Variavel" and classificacao != "Recorrente":
                flash("Escolha Variável ou Recorrente.", "error")
                return redirect(url_for("despesas", id_registro=id_registro))

            quantidade = 1
            if registro is None and classificacao == "Recorrente":
                quantidade = int(request.form.get("quantidade_meses", "1"))
                if quantidade < 1 or quantidade > 60:
                    flash("Escolha de 1 a 60 meses, incluindo o mês inicial.", "error")
                    return redirect(url_for("despesas"))
            datas = datas_recorrentes(data_movimento, quantidade)

            if classificacao == "Recorrente":
                cursor.execute("""SELECT ID_REGISTRO, DATA_MOVIMENTO FROM ARO_DESPESA
                    WHERE ID_USUARIO = ? AND UPPER(TRIM(DESCRICAO)) = UPPER(TRIM(?))
                    AND CLASSIFICACAO = 'Recorrente'""", (id_usuario, descricao))
                existentes = cursor.fetchall()
                for data in datas:
                    for existente in existentes:
                        mesmo_registro = registro is not None and existente[0] == registro[0]
                        if not mesmo_registro and existente[1].year == data.year and existente[1].month == data.month:
                            flash("Já existe um lançamento recorrente com esse nome no mês " + str(data.month) + "/" + str(data.year) + ". Nada foi salvo.", "error")
                            return redirect(url_for("despesas", id_registro=id_registro))

            if registro is None:
                for data in datas:
                    cursor.execute("""INSERT INTO ARO_DESPESA
                        (ID_USUARIO, DESCRICAO, VALOR, DATA_MOVIMENTO, CLASSIFICACAO)
                        VALUES (?, ?, ?, ?, ?)""", (id_usuario, descricao, valor, data, classificacao))
                    atualizar_saldo_mensal(cursor, id_usuario, data)
                mensagem = "Lançamento cadastrado com sucesso!"
                if classificacao == "Recorrente":
                    mensagem = str(quantidade) + " lançamentos programados. Cada um entra no saldo na sua data."
            else:
                cursor.execute("""UPDATE ARO_DESPESA SET DESCRICAO = ?, VALOR = ?,
                    DATA_MOVIMENTO = ?, CLASSIFICACAO = ? WHERE ID_REGISTRO = ? AND ID_USUARIO = ?""",
                    (descricao, valor, data_movimento, classificacao, id_registro, id_usuario))
                atualizar_saldo_mensal(cursor, id_usuario, data_movimento)
                if registro[3] != data_movimento:
                    atualizar_saldo_mensal(cursor, id_usuario, registro[3])
                mensagem = "Somente este lançamento foi atualizado."
            con.commit()
            flash(mensagem, "success")
            return redirect(url_for("despesas"))

        cursor.execute("""SELECT ID_REGISTRO, DESCRICAO, VALOR, DATA_MOVIMENTO, CLASSIFICACAO
            FROM ARO_DESPESA WHERE ID_USUARIO = ? ORDER BY DATA_MOVIMENTO DESC, ID_REGISTRO DESC""", (id_usuario,))
        registros = []
        agendados = []
        hoje = date.today()
        for lancamento in cursor.fetchall():
            if lancamento[3] <= hoje:
                registros.append(lancamento)
            else:
                agendados.append(lancamento)
        agendados.reverse()
        return render_template("despesas.html", registros=registros, agendados=agendados,
                               registro=registro, hoje=hoje.isoformat())
    except (ValueError, InvalidOperation):
        con.rollback()
        flash("Confira a data, o valor positivo com até duas casas decimais e a quantidade inteira de meses.", "error")
        return redirect(url_for("despesas", id_registro=id_registro))
    except Exception:
        con.rollback()
        app.logger.exception("Erro ao consultar ou salvar despesas")
        flash("Não foi possível salvar. Nenhum lançamento desta operação foi confirmado.", "error")
        return redirect(url_for("despesas"))
    finally:
        con.rollback()
        cursor.close()


# EXCLUIR DESPESA

@app.route("/despesas/<int:id_registro>/excluir", methods=["POST"])
def excluir_despesa(id_registro):
    if not usuario_autenticado():
        return redirect(url_for("login"))
    id_usuario = session["id_usuario"]
    cursor = con.cursor()
    try:
        if id_registro is not None:
            cursor.execute("SELECT ID_EMPRESTIMO FROM ARO_DESPESA WHERE ID_REGISTRO = ? AND ID_USUARIO = ?", (id_registro, id_usuario))
            vinculo = cursor.fetchone()
            if vinculo and vinculo[0] is not None:
                flash("Lançamento vinculado a empréstimo: consulte o contrato em Empréstimos.", "error")
                return redirect(url_for("despesas"))

        cursor.execute("""
            SELECT DATA_MOVIMENTO FROM ARO_DESPESA
            WHERE ID_REGISTRO = ? AND ID_USUARIO = ?
        """, (id_registro, id_usuario))
        registro = cursor.fetchone()
        if registro is None:
            flash("Despesa não encontrada.", "error")
        else:
            cursor.execute("""
                DELETE FROM ARO_DESPESA
                WHERE ID_REGISTRO = ? AND ID_USUARIO = ?
            """, (id_registro, id_usuario))
            atualizar_saldo_mensal(cursor, id_usuario, registro[0])
            con.commit()
            flash("Despesa excluída com sucesso!", "success")
    except Exception as e:
        con.rollback()
        app.logger.exception("Erro ao excluir despesa: %s", e)
        flash("Não foi possível excluir a despesa.", "error")
    finally:
        con.rollback()
        cursor.close()
    return redirect(url_for("despesas"))


# PÁGINA DO ADMINISTRADOR
@app.route("/admin")
def admin():
    if not usuario_autenticado():
        session.clear()
        flash("Faça login para continuar.", "error")
        return redirect(url_for("login"))

    if session["tipo_usuario"] != TIPO_ADM:
        flash("Esta página é exclusiva do administrador.", "error")
        return redirect(url_for("novo"))

    return render_template("inicio_adm.html", usuario=session["nome_usuario"])


# LISTAR TAXAS E CARREGAR UMA TAXA PARA EDIÇÃO
@app.route("/taxas")
def pagina_taxas():
    if not usuario_autenticado():
        session.clear()
        flash("Faça login para continuar.", "error")
        return redirect(url_for("login"))

    if session["tipo_usuario"] != TIPO_ADM:
        flash("Somente o administrador pode acessar as taxas.", "error")
        return redirect(url_for("novo"))

    cursor = con.cursor()
    try:
        id_edicao = request.args.get("editar")
        editar = None

        if id_edicao is not None:
            cursor.execute("""
                SELECT ID_TAXA, PERCENTUAL, DATA_REFERENCIA
                FROM ARO_TAXA WHERE ID_TAXA = ?
            """, (id_edicao,))
            editar = cursor.fetchone()
            if editar is None:
                flash("Taxa não encontrada.", "error")
                return redirect(url_for("pagina_taxas"))

        cursor.execute("""
            SELECT ID_TAXA, PERCENTUAL, DATA_REFERENCIA
            FROM ARO_TAXA ORDER BY DATA_REFERENCIA DESC
        """)
        taxas = cursor.fetchall()
        return render_template("taxas.html", taxas=taxas, editar=editar)
    except Exception as e:
        con.rollback()
        app.logger.exception("Erro ao consultar taxas: %s", e)
        flash("Não foi possível consultar as taxas.", "error")
        return redirect(url_for("admin"))
    finally:
        con.rollback()
        cursor.close()


# CADASTRAR TAXA COM A DATA ATUAL DO BANCO
@app.route("/taxas/cadastrar", methods=["POST"])
def cadastrar_taxa():
    if not usuario_autenticado():
        session.clear()
        flash("Faça login para continuar.", "error")
        return redirect(url_for("login"))
    if session["tipo_usuario"] != TIPO_ADM:
        flash("Somente o administrador pode cadastrar taxas.", "error")
        return redirect(url_for("novo"))

    try:
        percentual = valor_positivo(request.form.get("percentual", ""))
        if percentual > 100:
            raise ValueError("Percentual acima do permitido.")
    except (ValueError, InvalidOperation):
        flash("Informe uma taxa maior que zero e até 100, com até duas casas decimais.", "error")
        return redirect(url_for("pagina_taxas"))

    cursor = con.cursor()
    try:
        cursor.execute("""
            SELECT ID_TAXA FROM ARO_TAXA
            WHERE DATA_REFERENCIA = CURRENT_DATE
        """)
        if cursor.fetchone():
            flash("Já existe uma taxa cadastrada para a data de hoje.", "error")
            return redirect(url_for("pagina_taxas"))

        cursor.execute("""
            INSERT INTO ARO_TAXA (ID_USUARIO, PERCENTUAL, DATA_REFERENCIA)
            VALUES (?, ?, CURRENT_DATE)
        """, (session["id_usuario"], percentual))
        con.commit()
        flash("Taxa cadastrada com sucesso!", "success")
    except Exception as e:
        con.rollback()
        app.logger.exception("Erro ao cadastrar taxa: %s", e)
        flash("Não foi possível cadastrar. Confira se já existe uma taxa hoje.", "error")
    finally:
        con.rollback()
        cursor.close()
    return redirect(url_for("pagina_taxas"))


# EDITAR SOMENTE O PERCENTUAL, SEM ALTERAR A DATA
@app.route("/taxas/editar/<int:id_taxa>", methods=["POST"])
def editar_taxa(id_taxa):
    if not usuario_autenticado():
        session.clear()
        flash("Faça login para continuar.", "error")
        return redirect(url_for("login"))
    if session["tipo_usuario"] != TIPO_ADM:
        flash("Somente o administrador pode editar taxas.", "error")
        return redirect(url_for("novo"))

    try:
        percentual = valor_positivo(request.form.get("percentual", ""))
        if percentual > 100:
            raise ValueError("Percentual acima do permitido.")
    except (ValueError, InvalidOperation):
        flash("Informe uma taxa maior que zero e até 100, com até duas casas decimais.", "error")
        return redirect(url_for("pagina_taxas", editar=id_taxa))

    cursor = con.cursor()
    try:
        cursor.execute("""
            UPDATE ARO_TAXA SET PERCENTUAL = ? WHERE ID_TAXA = ?
        """, (percentual, id_taxa))
        if cursor.rowcount == 0:
            flash("Taxa não encontrada.", "error")
            return redirect(url_for("pagina_taxas"))
        con.commit()
        flash("Taxa atualizada com sucesso!", "success")
    except Exception as e:
        con.rollback()
        app.logger.exception("Erro ao editar taxa: %s", e)
        flash("Não foi possível editar a taxa.", "error")
    finally:
        con.rollback()
        cursor.close()
    return redirect(url_for("pagina_taxas"))


# EXCLUIR TAXA
@app.route("/taxas/excluir/<int:id_taxa>", methods=["POST"])
def excluir_taxa(id_taxa):
    if not usuario_autenticado():
        session.clear()
        flash("Faça login para continuar.", "error")
        return redirect(url_for("login"))
    if session["tipo_usuario"] != TIPO_ADM:
        flash("Somente o administrador pode excluir taxas.", "error")
        return redirect(url_for("novo"))

    cursor = con.cursor()
    try:
        cursor.execute("DELETE FROM ARO_TAXA WHERE ID_TAXA = ?", (id_taxa,))
        if cursor.rowcount == 0:
            flash("Taxa não encontrada.", "error")
            return redirect(url_for("pagina_taxas"))
        con.commit()
        flash("Taxa excluída com sucesso!", "success")
    except Exception as e:
        con.rollback()
        app.logger.exception("Erro ao excluir taxa: %s", e)
        flash("Não foi possível excluir a taxa.", "error")
    finally:
        con.rollback()
        cursor.close()
    return redirect(url_for("pagina_taxas"))


# FOTO DO PERFIL

@app.route("/perfil/foto", methods=["POST"])
def salvar_foto():
    if not usuario_autenticado():
        return redirect(url_for("login"))
    arquivo = request.files.get("foto")
    if not arquivo or not arquivo.filename:
        flash("Escolha uma imagem PNG ou JPG.", "error")
        return redirect(url_for("perfil"))
    caminho = None
    cursor = con.cursor()
    try:
        imagem = Image.open(arquivo.stream)
        if imagem.format not in ("PNG", "JPEG"):
            raise ValueError("Formato inválido.")
        imagem.load()
        imagem.thumbnail((800, 800))
        nome = "foto_" + uuid4().hex + ".png"
        caminho = os.path.join(app.static_folder, "uploads", nome)
        imagem.save(caminho, "PNG")
        cursor.execute("SELECT FOTO FROM USUARIO WHERE ID_USUARIO = ?", (session["id_usuario"],))
        antiga = cursor.fetchone()[0]
        cursor.execute("UPDATE USUARIO SET FOTO = ? WHERE ID_USUARIO = ?", (nome, session["id_usuario"]))
        con.commit()
        if antiga and antiga.startswith("foto_") and os.path.basename(antiga) == antiga:
            antigo_caminho = os.path.join(app.static_folder, "uploads", antiga)
            if os.path.isfile(antigo_caminho):
                try:
                    os.remove(antigo_caminho)
                except OSError:
                    app.logger.exception("A foto antiga não pôde ser removida")
        flash("Foto atualizada!", "success")
    except (ValueError, UnidentifiedImageError, OSError, Image.DecompressionBombError):
        con.rollback()
        if caminho and os.path.isfile(caminho):
            os.remove(caminho)
        flash("Envie uma imagem PNG ou JPG válida, de até 5 MB.", "error")
    except Exception:
        con.rollback()
        if caminho and os.path.isfile(caminho):
            os.remove(caminho)
        app.logger.exception("Erro ao salvar foto")
        flash("Não foi possível salvar a foto.", "error")
    finally:
        cursor.close()
    return redirect(url_for("perfil"))


@app.errorhandler(413)
def arquivo_grande(erro):
    flash("O arquivo deve ter até 5 MB.", "error")
    return redirect(url_for("perfil"))


# LIMITES DE PARCELAS DO ADMINISTRADOR

@app.route("/limites-parcelas", methods=["GET", "POST"])
@app.route("/limites-parcelas/<int:id_limite>/editar", methods=["GET", "POST"])
def limites_parcelas(id_limite=None):
    if not usuario_autenticado():
        return redirect(url_for("login"))
    if session["tipo_usuario"] != TIPO_ADM:
        flash("Somente o administrador pode alterar os limites de parcelas.", "error")
        return redirect(url_for("novo"))

    cursor = con.cursor()
    editar = None
    try:
        if id_limite is not None:
            cursor.execute("""SELECT ID_LIMITE, VALOR_MAXIMO, QUANTIDADE_MAXIMA
                              FROM ARO_LIMITE_PARCELAS WHERE ID_LIMITE = ?""", (id_limite,))
            editar = cursor.fetchone()
            if editar is None:
                flash("Limite não encontrado.", "error")
                return redirect(url_for("limites_parcelas"))

        if request.method == "POST":
            valor_maximo = valor_positivo(request.form.get("valor_maximo", ""))
            quantidade_maxima = int(request.form.get("quantidade_maxima", ""))
            if valor_maximo > Decimal("1000000"):
                raise ValueError("O valor máximo da faixa deve ser de até R$ 1.000.000.")
            if quantidade_maxima < 1 or quantidade_maxima > 60:
                raise ValueError("Escolha de 1 a 60 parcelas.")

            cursor.execute("SELECT ID_LIMITE FROM ARO_LIMITE_PARCELAS WHERE VALOR_MAXIMO = ?", (valor_maximo,))
            repetido = cursor.fetchone()
            if repetido and repetido[0] != id_limite:
                flash("Já existe uma faixa com esse valor máximo. Edite a faixa existente.", "error")
                return redirect(url_for("limites_parcelas", id_limite=id_limite))

            if editar is None:
                cursor.execute("""INSERT INTO ARO_LIMITE_PARCELAS (VALOR_MAXIMO, QUANTIDADE_MAXIMA)
                                  VALUES (?, ?)""", (valor_maximo, quantidade_maxima))
            else:
                cursor.execute("""UPDATE ARO_LIMITE_PARCELAS
                                  SET VALOR_MAXIMO = ?, QUANTIDADE_MAXIMA = ?
                                  WHERE ID_LIMITE = ?""", (valor_maximo, quantidade_maxima, id_limite))
            con.commit()
            flash("Limite de parcelas salvo!", "success")
            return redirect(url_for("limites_parcelas"))

        cursor.execute("""SELECT ID_LIMITE, VALOR_MAXIMO, QUANTIDADE_MAXIMA
                          FROM ARO_LIMITE_PARCELAS ORDER BY VALOR_MAXIMO""")
        limites = cursor.fetchall()
        return render_template("limites_parcelas.html", limites=limites, editar=editar)
    except (ValueError, InvalidOperation):
        con.rollback()
        flash("Informe um valor positivo de até R$ 1.000.000, com até duas casas decimais, e de 1 a 60 parcelas inteiras.", "error")
        return redirect(url_for("limites_parcelas", id_limite=id_limite))
    except Exception:
        con.rollback()
        app.logger.exception("Erro ao salvar ou consultar limites de parcelas")
        flash("Não foi possível salvar ou consultar os limites.", "error")
        return redirect(url_for("admin"))
    finally:
        con.rollback()
        cursor.close()


@app.route("/limites-parcelas/<int:id_limite>/excluir", methods=["POST"])
def excluir_limite_parcelas(id_limite):
    if not usuario_autenticado():
        return redirect(url_for("login"))
    if session["tipo_usuario"] != TIPO_ADM:
        flash("Somente o administrador pode excluir limites de parcelas.", "error")
        return redirect(url_for("novo"))

    cursor = con.cursor()
    try:
        cursor.execute("DELETE FROM ARO_LIMITE_PARCELAS WHERE ID_LIMITE = ?", (id_limite,))
        if cursor.rowcount == 0:
            flash("Limite não encontrado.", "error")
        else:
            con.commit()
            flash("Limite de parcelas excluído!", "success")
    except Exception:
        con.rollback()
        app.logger.exception("Erro ao excluir limite de parcelas")
        flash("Não foi possível excluir o limite.", "error")
    finally:
        con.rollback()
        cursor.close()
    return redirect(url_for("limites_parcelas"))


# CONSULTAR O LIMITE PELO VALOR DO EMPRÉSTIMO

def consultar_limite_parcelas(cursor, valor):
    cursor.execute("""SELECT FIRST 1 QUANTIDADE_MAXIMA FROM ARO_LIMITE_PARCELAS
                      WHERE VALOR_MAXIMO >= ? ORDER BY VALOR_MAXIMO""", (valor,))
    limite = cursor.fetchone()
    if limite is None:
        raise ValueError("O administrador precisa cadastrar uma faixa de parcelas que cubra esse valor.")
    return limite[0]


# SIMULAÇÃO DO EMPRÉSTIMO

def calcular_emprestimo(cursor, valor, quantidade):
    maximo_parcelas = consultar_limite_parcelas(cursor, valor)
    if quantidade > maximo_parcelas:
        raise ValueError("Para esse valor, o administrador permite no máximo " + str(maximo_parcelas) + " parcela(s).")
    cursor.execute("SELECT FIRST 1 PERCENTUAL FROM ARO_TAXA WHERE DATA_REFERENCIA <= CURRENT_DATE ORDER BY DATA_REFERENCIA DESC, ID_TAXA DESC")
    taxa = cursor.fetchone()
    if taxa is None:
        raise ValueError("O administrador precisa cadastrar uma taxa antes da simulação.")
    percentual = taxa[0]
    juros = round(valor * percentual / 100 * quantidade, 2)
    total = valor + juros
    parcela = (total / quantidade).quantize(Decimal("0.01"), rounding="ROUND_DOWN")
    ultima_parcela = total - parcela * (quantidade - 1)
    if parcela <= 0:
        raise ValueError("O valor é muito pequeno para essa quantidade de parcelas.")
    hoje = date.today()
    cursor.execute("SELECT DATA_MOVIMENTO, VALOR FROM ARO_RECEITA WHERE ID_USUARIO = ? AND ID_EMPRESTIMO IS NULL", (session["id_usuario"],))
    receitas_mes = Decimal("0")
    for receita in cursor.fetchall():
        if receita[0].year == hoje.year and receita[0].month == hoje.month:
            receitas_mes = receitas_mes + receita[1]
    cursor.execute("SELECT DATA_MOVIMENTO, VALOR FROM ARO_DESPESA WHERE ID_USUARIO = ?", (session["id_usuario"],))
    despesas_mes = Decimal("0")
    for despesa in cursor.fetchall():
        if despesa[0].year == hoje.year and despesa[0].month == hoje.month:
            despesas_mes = despesas_mes + despesa[1]
    sobra = receitas_mes - despesas_mes
    if ultima_parcela > sobra:
        risco = "Alto"
    elif ultima_parcela > receitas_mes * Decimal("0.30"):
        risco = "Médio"
    else:
        risco = "Baixo"
    return {"valor": valor, "quantidade": quantidade, "percentual": percentual,
            "juros": juros, "total": total, "parcela": parcela,
            "ultima_parcela": ultima_parcela, "sobra": sobra, "risco": risco,
            "maximo_parcelas": maximo_parcelas}


@app.route("/emprestimos/simular", methods=["GET", "POST"])
def simular_emprestimo():
    if not usuario_autenticado():
        return redirect(url_for("login"))
    simulacao = None
    limites = []
    if request.method == "POST":
        session.pop("simulacao", None)
        cursor = con.cursor()
        try:
            valor = valor_positivo(request.form.get("valor", ""))
            quantidade = int(request.form.get("quantidade", ""))
            if valor > Decimal("1000000") or quantidade < 1 or quantidade > 60:
                raise ValueError("Informe até R$ 1.000.000 e de 1 a 60 parcelas.")
            simulacao = calcular_emprestimo(cursor, valor, quantidade)
            session["simulacao"] = {"valor": str(valor), "quantidade": quantidade,
                                    "percentual": str(simulacao["percentual"]), "codigo": uuid4().hex}
        except (ValueError, InvalidOperation) as erro:
            flash(str(erro), "error")
        except Exception:
            app.logger.exception("Erro ao simular empréstimo")
            flash("Não foi possível simular o empréstimo.", "error")
        finally:
            con.rollback()
            cursor.close()
    cursor = con.cursor()
    try:
        cursor.execute("""SELECT VALOR_MAXIMO, QUANTIDADE_MAXIMA
                          FROM ARO_LIMITE_PARCELAS ORDER BY VALOR_MAXIMO""")
        limites = cursor.fetchall()
    finally:
        con.rollback()
        cursor.close()
    return render_template("simular_emprestimo.html", simulacao=simulacao, limites=limites)


# CONFIRMAR EMPRÉSTIMO, RECEITA E PARCELAS

@app.route("/emprestimos/confirmar", methods=["POST"])
def confirmar_emprestimo():
    if not usuario_autenticado():
        return redirect(url_for("login"))
    dados = session.get("simulacao")
    if not dados:
        flash("Faça uma simulação antes de confirmar.", "error")
        return redirect(url_for("simular_emprestimo"))
    cursor = con.cursor()
    try:
        cursor.execute("SELECT ID_EMPRESTIMO FROM ARO_EMPRESTIMO WHERE CODIGO = ?", (dados["codigo"],))
        if cursor.fetchone():
            session.pop("simulacao", None)
            flash("Este empréstimo já foi confirmado.", "error")
            return redirect(url_for("emprestimos"))
        simulacao = calcular_emprestimo(cursor, Decimal(dados["valor"]), dados["quantidade"])
        if simulacao["percentual"] != Decimal(dados["percentual"]):
            session.pop("simulacao", None)
            flash("A taxa mudou. Simule novamente antes de confirmar.", "error")
            return redirect(url_for("simular_emprestimo"))
        if simulacao["risco"] == "Alto":
            flash("Empréstimo não permitido: a parcela ultrapassa a sobra do mês atual.", "error")
            return redirect(url_for("simular_emprestimo"))
        hoje = date.today()
        cursor.execute("""INSERT INTO ARO_EMPRESTIMO
            (ID_USUARIO, VALOR, PERCENTUAL, QUANTIDADE, TOTAL, PARCELA, ULTIMA_PARCELA, DATA_EMPRESTIMO, RISCO, CODIGO)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session["id_usuario"], simulacao["valor"], simulacao["percentual"], simulacao["quantidade"],
             simulacao["total"], simulacao["parcela"], simulacao["ultima_parcela"], hoje, simulacao["risco"], dados["codigo"]))
        cursor.execute("SELECT ID_EMPRESTIMO FROM ARO_EMPRESTIMO WHERE CODIGO = ?", (dados["codigo"],))
        id_emprestimo = cursor.fetchone()[0]
        descricao = "Empréstimo " + str(id_emprestimo)
        cursor.execute("""INSERT INTO ARO_RECEITA (ID_USUARIO, DESCRICAO, VALOR, DATA_MOVIMENTO, ID_EMPRESTIMO, CLASSIFICACAO)
            VALUES (?, ?, ?, ?, ?, 'Variavel')""", (session["id_usuario"], descricao, simulacao["valor"], hoje, id_emprestimo))
        atualizar_saldo_mensal(cursor, session["id_usuario"], hoje)
        for numero in range(1, simulacao["quantidade"] + 1):
            vencimento = hoje + timedelta(days=30 * numero)
            valor_parcela = simulacao["parcela"]
            if numero == simulacao["quantidade"]:
                valor_parcela = simulacao["ultima_parcela"]
            descricao_parcela = descricao + " - parcela " + str(numero) + "/" + str(simulacao["quantidade"])
            cursor.execute("""INSERT INTO ARO_DESPESA
                (ID_USUARIO, DESCRICAO, VALOR, DATA_MOVIMENTO, CLASSIFICACAO, ID_EMPRESTIMO)
                VALUES (?, ?, ?, ?, 'Recorrente', ?)""",
                (session["id_usuario"], descricao_parcela, valor_parcela, vencimento, id_emprestimo))
            atualizar_saldo_mensal(cursor, session["id_usuario"], vencimento)
        con.commit()
        session.pop("simulacao", None)
        flash("Empréstimo confirmado! A receita e todas as parcelas foram cadastradas.", "success")
    except (ValueError, InvalidOperation) as erro:
        con.rollback()
        flash(str(erro), "error")
    except Exception:
        con.rollback()
        app.logger.exception("Erro ao confirmar empréstimo")
        flash("Não foi possível confirmar. Nenhum lançamento foi confirmado.", "error")
    finally:
        con.rollback()
        cursor.close()
    return redirect(url_for("emprestimos"))


# LISTAR EMPRÉSTIMOS

@app.route("/emprestimos")
def emprestimos():
    if not usuario_autenticado():
        return redirect(url_for("login"))
    cursor = con.cursor()
    try:
        cursor.execute("""SELECT ID_EMPRESTIMO, VALOR, PERCENTUAL, QUANTIDADE, TOTAL,
            PARCELA, ULTIMA_PARCELA, DATA_EMPRESTIMO, RISCO FROM ARO_EMPRESTIMO
            WHERE ID_USUARIO = ? ORDER BY ID_EMPRESTIMO DESC""", (session["id_usuario"],))
        registros = cursor.fetchall()
        return render_template("emprestimos.html", registros=registros)
    finally:
        con.rollback()
        cursor.close()


# EXTRATO COM FILTRO DE DATAS

def consultar_extrato(cursor, inicio, fim):
    if inicio > fim:
        raise ValueError("A data inicial deve ser anterior ou igual à final.")
    cursor.execute("""SELECT DATA_MOVIMENTO, DESCRICAO, VALOR FROM ARO_RECEITA
        WHERE ID_USUARIO = ? AND DATA_MOVIMENTO BETWEEN ? AND ?""", (session["id_usuario"], inicio, fim))
    movimentos = []
    for receita in cursor.fetchall():
        movimentos.append([receita[0], receita[1], receita[2], Decimal("0")])
    cursor.execute("""SELECT DATA_MOVIMENTO, DESCRICAO, VALOR FROM ARO_DESPESA
        WHERE ID_USUARIO = ? AND DATA_MOVIMENTO BETWEEN ? AND ?""", (session["id_usuario"], inicio, fim))
    for despesa in cursor.fetchall():
        movimentos.append([despesa[0], despesa[1], Decimal("0"), despesa[2]])
    movimentos.sort()
    cursor.execute("SELECT VALOR FROM ARO_RECEITA WHERE ID_USUARIO = ? AND DATA_MOVIMENTO < ?", (session["id_usuario"], inicio))
    saldo = Decimal("0")
    for receita in cursor.fetchall():
        saldo = saldo + receita[0]
    cursor.execute("SELECT VALOR FROM ARO_DESPESA WHERE ID_USUARIO = ? AND DATA_MOVIMENTO < ?", (session["id_usuario"], inicio))
    for despesa in cursor.fetchall():
        saldo = saldo - despesa[0]
    saldo_anterior = saldo
    for movimento in movimentos:
        saldo = saldo + movimento[2] - movimento[3]
        movimento.append(saldo)
    return movimentos, saldo_anterior, saldo


@app.route("/movimentacao")
@app.route("/historico")
def extrato():
    if not usuario_autenticado():
        return redirect(url_for("login"))
    cursor = con.cursor()
    hoje = date.today()
    inicio = request.args.get("inicio", hoje.replace(day=1).isoformat())
    fim = request.args.get("fim", hoje.isoformat())
    movimentos = []
    anterior = Decimal("0")
    saldo = Decimal("0")
    try:
        movimentos, anterior, saldo = consultar_extrato(cursor, date.fromisoformat(inicio), date.fromisoformat(fim))
    except ValueError:
        flash("Informe um período válido: data inicial até a data final.", "error")
    finally:
        con.rollback()
        cursor.close()
    return render_template("historico.html", movimentos=movimentos, anterior=anterior, saldo=saldo, inicio=inicio, fim=fim)


# RELATÓRIOS EM PDF

def novo_pdf(titulo):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 12, titulo, ln=True)
    pdf.set_font("Arial", "", 10)
    pdf.cell(0, 8, "Conta: " + session["nome_usuario"].encode("latin-1", "replace").decode("latin-1"), ln=True)
    return pdf


def baixar_pdf(pdf, nome):
    conteudo = pdf.output(dest="S").encode("latin-1")
    return send_file(BytesIO(conteudo), mimetype="application/pdf", as_attachment=True, download_name=nome)


@app.route("/emprestimos/relatorio")
def relatorio_emprestimos():
    if not usuario_autenticado():
        return redirect(url_for("login"))
    cursor = con.cursor()
    try:
        cursor.execute("""SELECT ID_EMPRESTIMO, VALOR, PERCENTUAL, QUANTIDADE, TOTAL,
            PARCELA, ULTIMA_PARCELA, DATA_EMPRESTIMO, RISCO FROM ARO_EMPRESTIMO
            WHERE ID_USUARIO = ? ORDER BY ID_EMPRESTIMO DESC""", (session["id_usuario"],))
        pdf = novo_pdf("ARO - Emprestimos realizados")
        registros = cursor.fetchall()
        if not registros:
            pdf.cell(0, 10, "Nenhum emprestimo confirmado.", ln=True)
        for item in registros:
            texto = ("Emprestimo " + str(item[0]) + " | Data: " + str(item[7]) +
                     " | Valor: R$ " + str(item[1]) + " | Taxa mensal: " + str(item[2]) + "%\n" +
                     "Parcelas: " + str(item[3]) + " | Total: R$ " + str(item[4]) +
                     " | Parcela: R$ " + str(item[5]) + " | Ultima: R$ " + str(item[6]) + "\n")
            pdf.multi_cell(0, 7, texto)
            pdf.ln(4)
        return baixar_pdf(pdf, "emprestimos_aro.pdf")
    finally:
        con.rollback()
        cursor.close()


@app.route("/extrato/relatorio")
def relatorio_extrato():
    if not usuario_autenticado():
        return redirect(url_for("login"))
    cursor = con.cursor()
    try:
        inicio = date.fromisoformat(request.args.get("inicio", ""))
        fim = date.fromisoformat(request.args.get("fim", ""))
        movimentos, anterior, saldo = consultar_extrato(cursor, inicio, fim)
        pdf = novo_pdf("ARO - Extrato de lancamentos")
        pdf.cell(0, 8, "Periodo: " + str(inicio) + " a " + str(fim), ln=True)
        pdf.cell(0, 8, "Saldo anterior: R$ " + str(anterior), ln=True)
        for item in movimentos:
            texto = (str(item[0]) + " | " + item[1] + "\nEntrada: R$ " + str(item[2]) +
                     " | Saida: R$ " + str(item[3]) + " | Saldo: R$ " + str(item[4]))
            pdf.multi_cell(0, 7, texto.encode("latin-1", "replace").decode("latin-1"))
            pdf.ln(3)
        pdf.cell(0, 10, "Saldo final: R$ " + str(saldo), ln=True)
        return baixar_pdf(pdf, "extrato_aro.pdf")
    except ValueError:
        flash("Informe datas válidas para gerar o relatório.", "error")
        return redirect(url_for("extrato"))
    finally:
        con.rollback()
        cursor.close()


if __name__ == "__main__":
    app.run(debug=True)
