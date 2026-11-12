
from flask import Flask, render_template, request, flash, redirect, url_for, session, send_file
from datetime import date, timedelta
from calendar import monthrange
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
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
database = r"C:\Users\Aluno\Downloads\ARO_risco_corrigido\ARO-main\BANCO.FDB"
user = "SYSDBA"
password = "sysdba"

con = fdb.connect(
    host=host,
    database=database,
    user=user,
    password=password,
    charset="UTF8"
)

# Compatibilidade com o Word: taxas por exercicio e historico de simulacoes.
# A migracao e incremental: nunca apaga os emprestimos ou taxas existentes.
def preparar_banco_credito():
    cursor = con.cursor()
    try:
        def campo_existe(tabela, campo):
            cursor.execute("""SELECT COUNT(*) FROM RDB$RELATION_FIELDS
                WHERE TRIM(RDB$RELATION_NAME) = ? AND TRIM(RDB$FIELD_NAME) = ?""",
                (tabela, campo))
            return cursor.fetchone()[0] > 0

        if not campo_existe("ARO_TAXA", "ANO_EXERCICIO"):
            cursor.execute("ALTER TABLE ARO_TAXA ADD ANO_EXERCICIO INTEGER")
            con.commit()
        cursor.execute("""UPDATE ARO_TAXA SET ANO_EXERCICIO = EXTRACT(YEAR FROM DATA_REFERENCIA)
            WHERE ANO_EXERCICIO IS NULL""")
        con.commit()

        # A restricao antiga de uma taxa por dia impede dois exercicios
        # cadastrados na mesma data; removemos SOMENTE essa UNIQUE.
        cursor.execute("""SELECT TRIM(c.RDB$CONSTRAINT_NAME)
            FROM RDB$RELATION_CONSTRAINTS c
            JOIN RDB$INDEX_SEGMENTS seg ON seg.RDB$INDEX_NAME = c.RDB$INDEX_NAME
            WHERE TRIM(c.RDB$RELATION_NAME) = 'ARO_TAXA'
              AND c.RDB$CONSTRAINT_TYPE = 'UNIQUE'
              AND TRIM(seg.RDB$FIELD_NAME) = 'DATA_REFERENCIA'""")
        for (constraint_name,) in cursor.fetchall():
            constraint_safe = constraint_name.replace('"', '""')
            cursor.execute('ALTER TABLE ARO_TAXA DROP CONSTRAINT "' + constraint_safe + '"')
            con.commit()

        if not campo_existe("ARO_EMPRESTIMO", "ANO_EXERCICIO"):
            cursor.execute("ALTER TABLE ARO_EMPRESTIMO ADD ANO_EXERCICIO INTEGER")
            con.commit()
        cursor.execute("""UPDATE ARO_EMPRESTIMO SET ANO_EXERCICIO = EXTRACT(YEAR FROM DATA_EMPRESTIMO)
            WHERE ANO_EXERCICIO IS NULL""")
        con.commit()

        cursor.execute("""SELECT COUNT(*) FROM RDB$RELATIONS
            WHERE TRIM(RDB$RELATION_NAME) = 'ARO_SIMULACAO'""")
        if not cursor.fetchone()[0]:
            cursor.execute("""CREATE TABLE ARO_SIMULACAO (
                CODIGO VARCHAR(32) NOT NULL PRIMARY KEY,
                ID_USUARIO INTEGER NOT NULL,
                ANO_EXERCICIO INTEGER NOT NULL,
                VALOR NUMERIC(15,2) NOT NULL,
                QUANTIDADE INTEGER NOT NULL,
                PERCENTUAL NUMERIC(9,2) NOT NULL,
                PARCELA NUMERIC(15,2) NOT NULL,
                TOTAL NUMERIC(15,2) NOT NULL,
                COMPROMETIMENTO NUMERIC(9,2) NOT NULL,
                RISCO VARCHAR(10) NOT NULL,
                DATA_SIMULACAO TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                SITUACAO VARCHAR(16) DEFAULT 'Simulação' NOT NULL
            )""")
            con.commit()
    except Exception:
        con.rollback()
        app.logger.exception("Falha na atualizacao do banco para os requisitos do Word")
        raise RuntimeError("Nao foi possivel preparar o banco. Faca backup do BANCO.FDB e confira permissoes / esquema.")
    finally:
        cursor.close()

preparar_banco_credito()

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


def acesso_administrador():
    return usuario_autenticado() and session.get("tipo_usuario") == TIPO_ADM


@app.route("/admin/usuarios")
def admin_usuarios():
    if not acesso_administrador():
        flash("Acesso exclusivo do administrador.", "error")
        return redirect(url_for("login") if not usuario_autenticado() else url_for("novo"))
    cursor = con.cursor()
    try:
        cursor.execute("""SELECT ID_USUARIO, NOME, EMAIL, RECEITA_MENSAL, DESPESA_MENSAL
                          FROM USUARIO WHERE TIPO_USUARIO = ?
                          ORDER BY NOME, ID_USUARIO""", (TIPO_COMUM,))
        usuarios = cursor.fetchall()
        return render_template("admin_usuarios.html", usuarios=usuarios)
    finally:
        con.rollback()
        cursor.close()


@app.route("/admin/usuarios/cadastrar", methods=["POST"])
def admin_cadastrar_usuario():
    if not acesso_administrador():
        flash("Acesso exclusivo do administrador.", "error")
        return redirect(url_for("login") if not usuario_autenticado() else url_for("novo"))
    cursor = con.cursor()
    try:
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip()
        senha = request.form.get("senha", "")
        confirmacao = request.form.get("confirmar_senha", "")
        if not nome or not email or not senha:
            raise ValueError("Preencha nome, e-mail e senha.")
        if senha != confirmacao:
            raise ValueError("As senhas não coincidem.")
        if not senha_forte(senha):
            raise ValueError("A senha precisa ter 8 caracteres, maiúscula, minúscula, número e símbolo (! @ # %), sem espaços.")
        receita = Decimal(request.form.get("receita_mensal", "0").strip().replace(",", ".") or "0")
        despesa = Decimal(request.form.get("despesa_mensal", "0").strip().replace(",", ".") or "0")
        if not receita.is_finite() or not despesa.is_finite() or receita < 0 or despesa < 0 or receita > 9999999999 or despesa > 9999999999:
            raise ValueError("Informe renda e despesas válidas, sem valores negativos.")
        if receita != round(receita, 2) or despesa != round(despesa, 2):
            raise ValueError("Use no máximo duas casas decimais.")
        cursor.execute("SELECT ID_USUARIO FROM USUARIO WHERE UPPER(EMAIL) = UPPER(?)", (email,))
        if cursor.fetchone():
            raise ValueError("Este e-mail já está cadastrado.")
        senha_hash = bcrypt.generate_password_hash(senha).decode("utf-8")
        cursor.execute("""INSERT INTO USUARIO
            (NOME, EMAIL, SENHA, TIPO_USUARIO, ATIVO, TENTATIVAS_LOGIN,
             BLOQUEADO_LOGIN, RECEITA_MENSAL, DESPESA_MENSAL)
            VALUES (?, ?, ?, ?, 1, 0, 0, ?, ?)""",
            (nome, email, senha_hash, TIPO_COMUM, receita, despesa))
        con.commit()
        flash("Usuário cadastrado com sucesso!", "success")
    except (ValueError, InvalidOperation) as erro:
        con.rollback()
        flash(str(erro), "error")
    except Exception:
        con.rollback()
        app.logger.exception("Erro ao cadastrar usuário pelo administrador")
        flash("Não foi possível cadastrar o usuário.", "error")
    finally:
        con.rollback()
        cursor.close()
    return redirect(url_for("admin_usuarios"))


@app.route("/admin/usuarios/<int:id_usuario>/emprestimos", methods=["GET", "POST"])
def admin_emprestimos_usuario(id_usuario):
    # Exclusivo do administrador; nunca utiliza a conta do admin para os lançamentos.
    if not acesso_administrador():
        flash("Acesso exclusivo do administrador.", "error")
        return redirect(url_for("login") if not usuario_autenticado() else url_for("novo"))
    cursor = con.cursor()
    simulacao = None
    chave = "admin_simulacao_emprestimo"
    try:
        cursor.execute("""SELECT NOME, EMAIL FROM USUARIO
                          WHERE ID_USUARIO = ? AND TIPO_USUARIO = ? AND ATIVO = 1""",
                       (id_usuario, TIPO_COMUM))
        usuario = cursor.fetchone()
        if usuario is None:
            session.pop(chave, None)
            flash("Usuário não encontrado ou inativo.", "error")
            return redirect(url_for("admin_usuarios"))

        if request.method == "POST":
            acao = request.form.get("acao")
            if acao == "simular":
                session.pop(chave, None)
                valor = valor_positivo(request.form.get("valor", ""))
                quantidade = int(request.form.get("quantidade", ""))
                if valor > Decimal("1000000") or quantidade < 1:
                    raise ValueError("Informe até R$ 1.000.000 e uma quantidade positiva de parcelas.")
                ano_exercicio = int(request.form.get("ano_exercicio", date.today().year))
                simulacao = calcular_emprestimo(cursor, valor, quantidade, id_usuario, ano_exercicio)
                codigo = uuid4().hex
                registrar_simulacao(cursor, codigo, id_usuario, simulacao)
                con.commit()
                if simulacao["risco"] != "Alto":
                    session[chave] = {"id_usuario": id_usuario,
                                      "valor": str(valor), "quantidade": quantidade,
                                      "ano_exercicio": ano_exercicio,
                                      "percentual": str(simulacao["percentual"]),
                                      "codigo": codigo}
            elif acao == "confirmar":
                dados = session.get(chave)
                if not dados or dados.get("id_usuario") != id_usuario:
                    raise ValueError("Faça uma simulação para este usuário antes de confirmar.")
                codigo = dados["codigo"]
                cursor.execute("SELECT ID_EMPRESTIMO FROM ARO_EMPRESTIMO WHERE CODIGO = ?", (codigo,))
                if cursor.fetchone():
                    session.pop(chave, None)
                    raise ValueError("Este empréstimo já foi confirmado.")
                simulacao = calcular_emprestimo(cursor, Decimal(dados["valor"]), int(dados["quantidade"]),
                                              id_usuario, dados["ano_exercicio"])
                if simulacao["percentual"] != Decimal(dados["percentual"]):
                    session.pop(chave, None)
                    raise ValueError("A taxa mudou. Simule novamente antes de confirmar.")
                if simulacao["risco"] == "Alto":
                    session.pop(chave, None)
                    raise ValueError("Empréstimo não permitido: risco alto para este usuário.")
                hoje = date.today()
                cursor.execute("""INSERT INTO ARO_EMPRESTIMO
                    (ID_USUARIO, VALOR, PERCENTUAL, QUANTIDADE, TOTAL, PARCELA,
                     ULTIMA_PARCELA, DATA_EMPRESTIMO, RISCO, CODIGO, ANO_EXERCICIO)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (id_usuario, simulacao["valor"], simulacao["percentual"], simulacao["quantidade"],
                     simulacao["total"], simulacao["parcela"], simulacao["ultima_parcela"],
                     hoje, simulacao["risco"], codigo, simulacao["ano_exercicio"]))
                cursor.execute("SELECT ID_EMPRESTIMO FROM ARO_EMPRESTIMO WHERE CODIGO = ?", (codigo,))
                id_emprestimo = cursor.fetchone()[0]
                descricao = "Empréstimo " + str(id_emprestimo)
                cursor.execute("""INSERT INTO ARO_RECEITA
                    (ID_USUARIO, DESCRICAO, VALOR, DATA_MOVIMENTO, ID_EMPRESTIMO, CLASSIFICACAO)
                    VALUES (?, ?, ?, ?, ?, 'Variavel')""",
                    (id_usuario, descricao, simulacao["valor"], hoje, id_emprestimo))
                atualizar_saldo_mensal(cursor, id_usuario, hoje)
                for numero in range(1, simulacao["quantidade"] + 1):
                    vencimento = hoje + timedelta(days=30 * numero)
                    valor_parcela = (simulacao["ultima_parcela"] if numero == simulacao["quantidade"]
                                     else simulacao["parcela"])
                    descricao_parcela = descricao + " - parcela " + str(numero) + "/" + str(simulacao["quantidade"])
                    cursor.execute("""INSERT INTO ARO_DESPESA
                        (ID_USUARIO, DESCRICAO, VALOR, DATA_MOVIMENTO, CLASSIFICACAO, ID_EMPRESTIMO)
                        VALUES (?, ?, ?, ?, 'Recorrente', ?)""",
                        (id_usuario, descricao_parcela, valor_parcela, vencimento, id_emprestimo))
                    atualizar_saldo_mensal(cursor, id_usuario, vencimento)
                cursor.execute("UPDATE ARO_SIMULACAO SET SITUACAO = 'Confirmado' WHERE CODIGO = ? AND ID_USUARIO = ?",
                               (codigo, id_usuario))
                con.commit()
                session.pop(chave, None)
                flash("Empréstimo registrado para " + usuario[0] + ". Receita e parcelas geradas.", "success")
                return redirect(url_for("admin_emprestimos_usuario", id_usuario=id_usuario))
            else:
                raise ValueError("Ação inválida.")

        cursor.execute("""SELECT ID_EMPRESTIMO, VALOR, PERCENTUAL, QUANTIDADE,
            TOTAL, PARCELA, ULTIMA_PARCELA, DATA_EMPRESTIMO, RISCO, ANO_EXERCICIO
            FROM ARO_EMPRESTIMO WHERE ID_USUARIO = ? ORDER BY ID_EMPRESTIMO DESC""", (id_usuario,))
        registros = cursor.fetchall()
        return render_template("admin_emprestimos_usuario.html", usuario=usuario,
                               registros=registros, id_usuario=id_usuario, simulacao=simulacao, ano_atual=date.today().year)
    except (ValueError, InvalidOperation) as erro:
        con.rollback()
        session.pop(chave, None)
        flash(str(erro), "error")
    except Exception:
        con.rollback()
        session.pop(chave, None)
        app.logger.exception("Erro na gestão de empréstimo pelo administrador")
        flash("Não foi possível processar o empréstimo.", "error")
    finally:
        con.rollback()
        cursor.close()
    return redirect(url_for("admin_emprestimos_usuario", id_usuario=id_usuario))


# LISTAR TAXAS E CARREGAR UMA TAXA PARA EDIÇÃO
@app.route("/taxas")
def pagina_taxas():
    if not acesso_administrador():
        flash("Somente o administrador pode acessar as taxas.", "error")
        return redirect(url_for("login") if not usuario_autenticado() else url_for("novo"))
    cursor = con.cursor()
    try:
        editar = None
        if request.args.get("editar"):
            cursor.execute("""SELECT ID_TAXA, PERCENTUAL, DATA_REFERENCIA, ANO_EXERCICIO
                FROM ARO_TAXA WHERE ID_TAXA = ?""", (request.args["editar"],))
            editar = cursor.fetchone()
        cursor.execute("""SELECT ID_TAXA, PERCENTUAL, DATA_REFERENCIA, ANO_EXERCICIO
            FROM ARO_TAXA ORDER BY ANO_EXERCICIO DESC, DATA_REFERENCIA DESC, ID_TAXA DESC""")
        return render_template("taxas.html", taxas=cursor.fetchall(), editar=editar, ano_atual=date.today().year)
    except Exception:
        app.logger.exception("Erro ao consultar taxas")
        flash("Não foi possível consultar as taxas.", "error")
        return redirect(url_for("admin"))
    finally:
        con.rollback()
        cursor.close()


def ano_exercicio_formulario():
    try:
        ano = int(request.form.get("ano_exercicio", ""))
    except (ValueError, TypeError):
        raise ValueError("Informe um ano de exercício válido.")
    if ano < 2000 or ano > 2100:
        raise ValueError("O exercício deve estar entre 2000 e 2100.")
    return ano


@app.route("/taxas/cadastrar", methods=["POST"])
def cadastrar_taxa():
    if not acesso_administrador():
        return redirect(url_for("login") if not usuario_autenticado() else url_for("novo"))
    cursor = con.cursor()
    try:
        percentual = valor_positivo(request.form.get("percentual", ""))
        ano = ano_exercicio_formulario()
        if percentual > 100:
            raise ValueError("Percentual máximo de 100%.")
        cursor.execute("SELECT ID_TAXA FROM ARO_TAXA WHERE ANO_EXERCICIO = ?", (ano,))
        if cursor.fetchone():
            raise ValueError("Já existe uma taxa para este exercício. Edite a taxa existente.")
        cursor.execute("""INSERT INTO ARO_TAXA (ID_USUARIO, PERCENTUAL, DATA_REFERENCIA, ANO_EXERCICIO)
            VALUES (?, ?, CURRENT_DATE, ?)""", (session["id_usuario"], percentual, ano))
        con.commit()
        flash("Taxa anual cadastrada com sucesso!", "success")
    except (ValueError, InvalidOperation) as erro:
        con.rollback()
        flash(str(erro), "error")
    except Exception:
        con.rollback()
        app.logger.exception("Erro ao cadastrar taxa")
        flash("Não foi possível cadastrar a taxa.", "error")
    finally:
        con.rollback()
        cursor.close()
    return redirect(url_for("pagina_taxas"))


@app.route("/taxas/editar/<int:id_taxa>", methods=["POST"])
def editar_taxa(id_taxa):
    if not acesso_administrador():
        return redirect(url_for("login") if not usuario_autenticado() else url_for("novo"))
    cursor = con.cursor()
    try:
        percentual = valor_positivo(request.form.get("percentual", ""))
        ano = ano_exercicio_formulario()
        if percentual > 100:
            raise ValueError("Percentual máximo de 100%.")
        cursor.execute("SELECT ID_TAXA FROM ARO_TAXA WHERE ANO_EXERCICIO = ? AND ID_TAXA <> ?", (ano, id_taxa))
        if cursor.fetchone():
            raise ValueError("Já existe outra taxa para esse exercício.")
        cursor.execute("UPDATE ARO_TAXA SET PERCENTUAL = ?, ANO_EXERCICIO = ? WHERE ID_TAXA = ?",
                       (percentual, ano, id_taxa))
        if cursor.rowcount == 0:
            raise ValueError("Taxa não encontrada.")
        con.commit()
        flash("Taxa atualizada com sucesso!", "success")
    except (ValueError, InvalidOperation) as erro:
        con.rollback()
        flash(str(erro), "error")
    except Exception:
        con.rollback()
        app.logger.exception("Erro ao editar taxa")
        flash("Não foi possível editar a taxa.", "error")
    finally:
        con.rollback()
        cursor.close()
    return redirect(url_for("pagina_taxas"))


@app.route("/taxas/excluir/<int:id_taxa>", methods=["POST"])
def excluir_taxa(id_taxa):
    if not acesso_administrador():
        return redirect(url_for("login") if not usuario_autenticado() else url_for("novo"))
    cursor = con.cursor()
    try:
        cursor.execute("DELETE FROM ARO_TAXA WHERE ID_TAXA = ?", (id_taxa,))
        if cursor.rowcount == 0:
            raise ValueError("Taxa não encontrada.")
        con.commit()
        flash("Taxa excluída com sucesso!", "success")
    except ValueError as erro:
        con.rollback()
        flash(str(erro), "error")
    except Exception:
        con.rollback()
        app.logger.exception("Erro ao excluir taxa")
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
        flash("Não foi possível salvar a foto.", "error")
    finally:
        cursor.close()
    return redirect(url_for("perfil"))


@app.errorhandler(413)
def arquivo_grande(erro):
    flash("O arquivo deve ter até 5 MB.", "error")
    return redirect(url_for("perfil"))


# SIMULAÇÃO DO EMPRÉSTIMO

def calcular_emprestimo(cursor, valor, quantidade, id_usuario=None, ano_exercicio=None):
    """Calcula Tabela Price e comprometimento da renda liquida, conforme o Word."""
    if id_usuario is None:
        id_usuario = session["id_usuario"]
    if ano_exercicio is None:
        ano_exercicio = date.today().year
    ano_exercicio = int(ano_exercicio)
    if ano_exercicio < 2000 or ano_exercicio > 2100:
        raise ValueError("Ano de exercício inválido.")
    if quantidade < 1 or quantidade > 60:
        raise ValueError("Informe de 1 a 60 parcelas.")
    cursor.execute("""SELECT FIRST 1 PERCENTUAL FROM ARO_TAXA
        WHERE ANO_EXERCICIO = ? ORDER BY DATA_REFERENCIA DESC, ID_TAXA DESC""", (ano_exercicio,))
    taxa = cursor.fetchone()
    if taxa is None:
        raise ValueError("Não existe taxa cadastrada para o exercício " + str(ano_exercicio) + ".")
    percentual = Decimal(str(taxa[0]))
    valor = Decimal(str(valor))
    i = percentual / Decimal("100")
    # Tabela Price: parcela = PV*i/(1-(1+i)^(-n)); com taxa zero, PV/n.
    parcela_exata = (valor * i / (1 - (1 + i) ** (-quantidade))) if i else valor / quantidade
    parcela = parcela_exata.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total = (parcela * quantidade).quantize(Decimal("0.01"))
    juros = total - valor
    ultima_parcela = parcela
    if parcela < Decimal("30"):
        raise ValueError("Cada parcela deve ser de pelo menos R$ 30. Diminua o número de parcelas ou aumente o valor.")
    hoje = date.today()
    cursor.execute("""SELECT DATA_MOVIMENTO, VALOR FROM ARO_RECEITA
        WHERE ID_USUARIO = ? AND ID_EMPRESTIMO IS NULL""", (id_usuario,))
    receitas_mes = Decimal("0")
    tem_receita_mes = False
    for receita in cursor.fetchall():
        if receita[0].year == hoje.year and receita[0].month == hoje.month:
            receitas_mes += Decimal(str(receita[1]))
            tem_receita_mes = True
    cursor.execute("SELECT DATA_MOVIMENTO, VALOR FROM ARO_DESPESA WHERE ID_USUARIO = ?", (id_usuario,))
    despesas_mes = Decimal("0")
    tem_despesa_mes = False
    for despesa in cursor.fetchall():
        if despesa[0].year == hoje.year and despesa[0].month == hoje.month:
            despesas_mes += Decimal(str(despesa[1]))
            tem_despesa_mes = True
    cursor.execute("SELECT RECEITA_MENSAL, DESPESA_MENSAL FROM USUARIO WHERE ID_USUARIO = ?", (id_usuario,))
    usuario = cursor.fetchone()
    if usuario is None:
        raise ValueError("Usuário não encontrado.")
    origem_receitas = "Lançamentos do mês"
    origem_despesas = "Lançamentos do mês"
    if not tem_receita_mes:
        receitas_mes = Decimal(str(usuario[0] or 0))
        origem_receitas = "Renda mensal do perfil"
    if not tem_despesa_mes:
        despesas_mes = Decimal(str(usuario[1] or 0))
        origem_despesas = "Despesas mensais do perfil"
    if receitas_mes <= 0:
        raise ValueError("Cadastre uma receita ou informe renda mensal no perfil para simular.")
    sobra = receitas_mes - despesas_mes
    # O Word define renda liquida como receitas menos despesas.
    # Se a renda liquida e zero/negativa, a operacao e risco alto.
    comprometimento = ((parcela / sobra) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if sobra > 0 else Decimal("100")
    if comprometimento <= Decimal("20") and sobra > 0:
        risco = "Baixo"
    elif comprometimento <= Decimal("35") and sobra > 0:
        risco = "Médio"
    else:
        risco = "Alto"
    return {"valor": valor, "quantidade": quantidade, "percentual": percentual,
            "juros": juros, "total": total, "parcela": parcela,
            "ultima_parcela": ultima_parcela, "sobra": sobra, "risco": risco,
            "comprometimento": comprometimento, "ano_exercicio": ano_exercicio,
            "receitas_mes": receitas_mes, "despesas_mes": despesas_mes,
            "origem_receitas": origem_receitas, "origem_despesas": origem_despesas,
            "mes_referencia": hoje.strftime("%m/%Y")}


def registrar_simulacao(cursor, codigo, id_usuario, simulacao):
    cursor.execute("""INSERT INTO ARO_SIMULACAO
        (CODIGO, ID_USUARIO, ANO_EXERCICIO, VALOR, QUANTIDADE, PERCENTUAL,
         PARCELA, TOTAL, COMPROMETIMENTO, RISCO, SITUACAO)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Simulação')""",
        (codigo, id_usuario, simulacao["ano_exercicio"], simulacao["valor"],
         simulacao["quantidade"], simulacao["percentual"], simulacao["parcela"],
         simulacao["total"], simulacao["comprometimento"], simulacao["risco"]))


@app.route("/emprestimos/simular", methods=["GET", "POST"])
def simular_emprestimo():
    if not usuario_autenticado():
        return redirect(url_for("login"))
    simulacao = None
    if request.method == "POST":
        session.pop("simulacao", None)
        cursor = con.cursor()
        try:
            valor = valor_positivo(request.form.get("valor", ""))
            quantidade = int(request.form.get("quantidade", ""))
            if valor > Decimal("1000000") or quantidade < 1:
                raise ValueError("Informe até R$ 1.000.000 e uma quantidade inteira de parcelas maior que zero.")
            ano_exercicio = int(request.form.get("ano_exercicio", date.today().year))
            simulacao = calcular_emprestimo(cursor, valor, quantidade, ano_exercicio=ano_exercicio)
            codigo = uuid4().hex
            registrar_simulacao(cursor, codigo, session["id_usuario"], simulacao)
            con.commit()
            session["simulacao"] = {"valor": str(valor), "quantidade": quantidade,
                                    "ano_exercicio": ano_exercicio,
                                    "percentual": str(simulacao["percentual"]), "codigo": codigo}
        except (ValueError, InvalidOperation) as erro:
            flash(str(erro), "error")
        except Exception:
            flash("Não foi possível simular o empréstimo.", "error")
        finally:
            con.rollback()
            cursor.close()
    return render_template("simular_emprestimo.html", simulacao=simulacao, ano_atual=date.today().year)


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
        simulacao = calcular_emprestimo(cursor, Decimal(dados["valor"]), dados["quantidade"],
                                       ano_exercicio=dados["ano_exercicio"])
        if simulacao["percentual"] != Decimal(dados["percentual"]):
            session.pop("simulacao", None)
            flash("A taxa mudou. Simule novamente antes de confirmar.", "error")
            return redirect(url_for("simular_emprestimo"))
        if simulacao["risco"] == "Alto":
            flash("Empréstimo não permitido: comprometimento da renda acima de 35%.", "error")
            return redirect(url_for("simular_emprestimo"))
        hoje = date.today()
        cursor.execute("""INSERT INTO ARO_EMPRESTIMO
            (ID_USUARIO, VALOR, PERCENTUAL, QUANTIDADE, TOTAL, PARCELA, ULTIMA_PARCELA, DATA_EMPRESTIMO, RISCO, CODIGO, ANO_EXERCICIO)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session["id_usuario"], simulacao["valor"], simulacao["percentual"], simulacao["quantidade"],
             simulacao["total"], simulacao["parcela"], simulacao["ultima_parcela"], hoje, simulacao["risco"], dados["codigo"], simulacao["ano_exercicio"]))
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
        cursor.execute("UPDATE ARO_SIMULACAO SET SITUACAO = 'Confirmado' WHERE CODIGO = ? AND ID_USUARIO = ?",
                       (dados["codigo"], session["id_usuario"]))
        con.commit()
        session.pop("simulacao", None)
        flash("Empréstimo confirmado! A receita e todas as parcelas foram cadastradas.", "success")
    except (ValueError, InvalidOperation) as erro:
        con.rollback()
        flash(str(erro), "error")
    except Exception:
        con.rollback()
        flash("Não foi possível confirmar. Nenhum lançamento foi confirmado.", "error")
    finally:
        con.rollback()
        cursor.close()
    return redirect(url_for("emprestimos"))


# HISTORICO PERMANENTE DE SIMULACOES (confirmadas e nao confirmadas)
@app.route("/emprestimos/simulacoes")
def historico_simulacoes():
    if not usuario_autenticado():
        return redirect(url_for("login"))
    cursor = con.cursor()
    try:
        cursor.execute("""SELECT DATA_SIMULACAO, ANO_EXERCICIO, VALOR, QUANTIDADE,
            PERCENTUAL, PARCELA, TOTAL, COMPROMETIMENTO, RISCO, SITUACAO
            FROM ARO_SIMULACAO WHERE ID_USUARIO = ? ORDER BY DATA_SIMULACAO DESC""",
            (session["id_usuario"],))
        registros = cursor.fetchall()
        return render_template("historico_simulacoes.html", registros=registros)
    finally:
        con.rollback()
        cursor.close()


# LISTAR EMPRÉSTIMOS

@app.route("/emprestimos")
def emprestimos():
    if not usuario_autenticado():
        return redirect(url_for("login"))
    cursor = con.cursor()
    try:
        cursor.execute("""SELECT ID_EMPRESTIMO, VALOR, PERCENTUAL, QUANTIDADE, TOTAL,
            PARCELA, ULTIMA_PARCELA, DATA_EMPRESTIMO, RISCO, ANO_EXERCICIO FROM ARO_EMPRESTIMO
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
    conteudo = pdf.output(dest="S")
    conteudo = conteudo.encode("latin-1") if isinstance(conteudo, str) else bytes(conteudo)
    return send_file(BytesIO(conteudo), mimetype="application/pdf", as_attachment=True, download_name=nome)


@app.route("/emprestimos/relatorio")
def relatorio_emprestimos():
    if not usuario_autenticado():
        return redirect(url_for("login"))
    cursor = con.cursor()
    try:
        cursor.execute("""SELECT ID_EMPRESTIMO, VALOR, PERCENTUAL, QUANTIDADE, TOTAL,
            PARCELA, ULTIMA_PARCELA, DATA_EMPRESTIMO, RISCO, ANO_EXERCICIO FROM ARO_EMPRESTIMO
            WHERE ID_USUARIO = ? ORDER BY ID_EMPRESTIMO DESC""", (session["id_usuario"],))
        pdf = novo_pdf("ARO - Emprestimos realizados")
        registros = cursor.fetchall()
        if not registros:
            pdf.cell(0, 10, "Nenhum emprestimo confirmado.", ln=True)
        for item in registros:
            texto = ("Emprestimo " + str(item[0]) + " | Data: " + str(item[7]) +
                     " | Valor: R$ " + str(item[1]) + " | Exercicio: " + str(item[9]) + " | Taxa mensal: " + str(item[2]) + "%\n" +
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
