
from flask import Flask, render_template, request, flash, redirect, url_for, session
from datetime import date
from decimal import Decimal, InvalidOperation
from flask_bcrypt import Bcrypt
import os
import fdb

# CONFIGURAÇÃO DO FLASK
app = Flask(__name__, template_folder="Templates")
bcrypt = Bcrypt(app)
app.config["SECRET_KEY"] = "Chavesdfglkjhgfdshjkjhgfdcvbmnb"
# CONEXÃO COM O BANCO
host = "localhost"
# Encontra BANCO.FDB na pasta do projeto, mesmo ao mudar o ZIP de lugar.
database = r"C:\Users\Aluno\Downloads\PROJETO-ARO-main\PROJETO-ARO-main\BANCO.FDB"
user = "SYSDBA"
password = "sysdba"

con = fdb.connect(
    host=host,
    database=database,
    user=user,
    password=password,
    charset="UTF8"
)

# TIPOS DE CONTA: o cadastro do site sempre cria usuário comum.
# O administrador é inserido pelo SQL, com TIPO_USUARIO = 1.
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

    # Verifica cada caractere da senha.
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
    # Aceita 100.50 ou 100,50, sem permitir valores negativos ou zero.
    texto = str(texto).strip().replace(",", ".")
    valor = Decimal(texto)
    if not valor.is_finite() or valor <= 0:
        raise ValueError("O valor deve ser maior que zero.")
    # O banco guarda até duas casas decimais e este limite de valor.
    if valor > Decimal("9999999999.99") or valor != round(valor, 2):
        raise ValueError("Informe um valor com até duas casas decimais.")
    return valor


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
        # Consulta somente a conta que está na sessão.
        cursor.execute("""SELECT ATIVO, BLOQUEADO_LOGIN, TIPO_USUARIO, NOME FROM USUARIO
                          WHERE ID_USUARIO = ?""", (session["id_usuario"],))
        usuario = cursor.fetchone()
        if usuario:
            if usuario[0] == 1 and (usuario[1] or 0) == 0:
                # Atualiza o tipo a partir do banco, sem confiar em campos do formulário.
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

    # Cada tipo de conta abre sua página inicial.
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

    tipo_usuario = TIPO_COMUM  # Não recebe o tipo pelo formulário.

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
        # VERIFICA E-MAIL REPETIDO

        cursor.execute("""SELECT ID_USUARIO FROM USUARIO WHERE UPPER(EMAIL) = UPPER(?)""", (email,))

        if cursor.fetchone():
            flash("Este e-mail já está cadastrado!", "error")

            return redirect(url_for("pagina_cadastro"))

        # CRIPTOGRAFA A SENHA

        senha_hash = bcrypt.generate_password_hash(senha).decode("utf-8")

        # SALVA O USUÁRIO NO BANCO

        cursor.execute("""INSERT INTO USUARIO (NOME, EMAIL, SENHA, TIPO_USUARIO, ATIVO, TENTATIVAS_LOGIN, BLOQUEADO_LOGIN) VALUES (?, ?, ?, ?, 1, 0, 0)""", (
            nome,
            email,
            senha_hash,
            tipo_usuario
        ))

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
        # Busca os dados e as tentativas da conta informada.
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

        # Se acertar a senha, zera apenas as tentativas desta conta.
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

        # Se errar a senha, soma uma tentativa.
        tentativas = tentativas + 1

        if tentativas >= 3:
            bloqueado = 1
            flash("Usuário bloqueado após 3 tentativas!", "error")
        else:
            flash(f"Senha incorreta! Restam {3 - tentativas} tentativa(s).", "error")

        # O ID no WHERE garante que somente esta conta será alterada.
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
        # Encerra a consulta, inclusive quando o login é recusado.
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

    # Estes campos já existem na tela de edição e na tabela USUARIO.
    # São informações do perfil, separadas dos lançamentos da sprint.
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
        # VERIFICA E-MAIL REPETIDO

        cursor.execute("""SELECT ID_USUARIO FROM USUARIO WHERE UPPER(EMAIL) = UPPER(?) AND ID_USUARIO <> ?""", (email, session["id_usuario"]))

        if cursor.fetchone():
            flash("Este e-mail já está cadastrado!", "error")

            return redirect(url_for("editar_usuario"))

        # ATUALIZA OS DADOS

        if senha:
            cursor.execute("""SELECT SENHA, SENHA_ANTERIOR1, SENHA_ANTERIOR2, SENHA_ANTERIOR3 FROM USUARIO WHERE ID_USUARIO = ?""", (session["id_usuario"],))

            dados = cursor.fetchone()

            if not dados:
                session.clear()

                flash("Usuário não encontrado!", "error")

                return redirect(url_for("login"))

            # VERIFICA AS SENHAS ANTERIORES

            for senha_antiga in dados:
                if senha_antiga:
                    if verificar_senha(senha_antiga, senha):
                        flash(
                            "Você não pode reutilizar a senha atual nem as 3 anteriores!",
                            "error"
                        )

                        return redirect(url_for("editar_usuario"))

            # CRIPTOGRAFA A NOVA SENHA

            senha_hash = bcrypt.generate_password_hash(senha).decode("utf-8")

            # ATUALIZA OS DADOS E O HISTÓRICO

            cursor.execute("""UPDATE USUARIO SET NOME = ?, EMAIL = ?, RECEITA_MENSAL = ?, DESPESA_MENSAL = ?, SENHA_ANTERIOR3 = SENHA_ANTERIOR2, SENHA_ANTERIOR2 = SENHA_ANTERIOR1, SENHA_ANTERIOR1 = SENHA, SENHA = ? WHERE ID_USUARIO = ?""", (
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

        # SALVA NO BANCO

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
        session.clear()
        flash("Faça login para acessar esta página!", "error")
        return redirect(url_for("login"))

    return render_template("saldo_atual.html")


# PÁGINA DO PERFIL DO USUÁRIO

@app.route("/perfil")
def perfil():
    if not usuario_autenticado():
        flash("Precisa estar logado!", "error")

        return redirect(url_for("login"))

    cursor = con.cursor()
    try:
        cursor.execute("""SELECT ID_USUARIO, NOME, EMAIL FROM USUARIO
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

    cursor = con.cursor()
    try:
        cursor.execute("""SELECT ID_USUARIO, NOME, EMAIL FROM USUARIO
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
    # Verifica se o usuário está logado.
    if not usuario_autenticado():
        session.clear()
        flash("Faça login para continuar.", "error")
        return redirect(url_for("login"))

    id_usuario = session["id_usuario"]
    cursor = con.cursor()
    registro = None

    try:
        # Busca a receita que será editada.
        if id_registro is not None:
            cursor.execute("""
                SELECT ID_REGISTRO, DESCRICAO, VALOR, DATA_MOVIMENTO
                FROM ARO_RECEITA
                WHERE ID_REGISTRO = ? AND ID_USUARIO = ?
            """, (id_registro, id_usuario))

            registro = cursor.fetchone()

            if registro is None:
                flash("Registro não encontrado.", "error")
                return redirect(url_for("receitas"))

        # Recebe os dados enviados pelo formulário.
        if request.method == "POST":
            descricao = request.form.get("descricao", "").strip()
            valor = valor_positivo(request.form.get("valor", ""))
            data_movimento = date.fromisoformat(
                request.form.get("data", "")
            )

            if descricao == "" or len(descricao) > 100:
                flash("Informe uma descrição de até 100 caracteres.", "error")
                return redirect(
                    url_for("receitas", id_registro=id_registro)
                )

            if registro is None:
                # Cadastra uma nova receita.
                cursor.execute("""
                    INSERT INTO ARO_RECEITA
                    (ID_USUARIO, DESCRICAO, VALOR, DATA_MOVIMENTO)
                    VALUES (?, ?, ?, ?)
                """, (id_usuario, descricao, valor, data_movimento))

                mensagem = "Receita cadastrada com sucesso!"

            else:
                # Atualiza a receita do usuário.
                cursor.execute("""
                    UPDATE ARO_RECEITA
                    SET DESCRICAO = ?, VALOR = ?, DATA_MOVIMENTO = ?
                    WHERE ID_REGISTRO = ? AND ID_USUARIO = ?
                """, (
                    descricao, valor, data_movimento,
                    id_registro, id_usuario
                ))

                mensagem = "Receita atualizada com sucesso!"

            # Atualiza o saldo do mês após salvar a receita.
            atualizar_saldo_mensal(cursor, id_usuario, data_movimento)
            if registro and registro[3] != data_movimento:
                atualizar_saldo_mensal(cursor, id_usuario, registro[3])

            # Salva a receita no banco.
            con.commit()

            flash(mensagem, "success")
            return redirect(url_for("receitas"))

        # Lista as receitas do usuário.
        cursor.execute("""
            SELECT ID_REGISTRO, DESCRICAO, VALOR, DATA_MOVIMENTO
            FROM ARO_RECEITA
            WHERE ID_USUARIO = ?
            ORDER BY DATA_MOVIMENTO DESC, ID_REGISTRO DESC
        """, (id_usuario,))

        registros = cursor.fetchall()

        return render_template(
            "receitas.html",
            registros=registros,
            registro=registro,
            hoje=date.today().isoformat()
        )

    except (ValueError, InvalidOperation):
        con.rollback()
        flash("Confira a data e informe um valor positivo com até duas casas decimais.", "error")
        return redirect(url_for("receitas", id_registro=id_registro))

    except Exception as e:
        con.rollback()
        # Mostra o erro real no terminal para facilitar a correção.
        app.logger.exception("Erro na página de receitas: %s", e)
        flash("Ocorreu um erro na página de receitas. Confira o terminal.", "error")
        # Evita voltar à mesma página quando o erro ocorre ao abri-la.
        return redirect(url_for("novo"))

    finally:
        # Encerra qualquer transação pendente e fecha o cursor.
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
        # Busca a receita do usuário.
        cursor.execute("""
            SELECT DATA_MOVIMENTO
            FROM ARO_RECEITA
            WHERE ID_REGISTRO = ? AND ID_USUARIO = ?
        """, (id_registro, id_usuario))

        registro = cursor.fetchone()

        if registro is None:
            flash("Registro não encontrado.", "error")

        else:
            # Exclui apenas a receita pertencente ao usuário.
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
        flash("Faça login para continuar.", "error")
        return redirect(url_for("login"))

    id_usuario = session["id_usuario"]
    cursor = con.cursor()
    registro = None
    try:
        if id_registro is not None:
            cursor.execute("""
                SELECT ID_REGISTRO, DESCRICAO, VALOR, DATA_MOVIMENTO, CLASSIFICACAO
                FROM ARO_DESPESA WHERE ID_REGISTRO = ? AND ID_USUARIO = ?
            """, (id_registro, id_usuario))
            registro = cursor.fetchone()
            if registro is None:
                flash("Despesa não encontrada.", "error")
                return redirect(url_for("despesas"))

        if request.method == "POST":
            descricao = request.form.get("descricao", "").strip()
            valor = valor_positivo(request.form.get("valor", ""))
            classificacao = request.form.get("classificacao", "")
            data_movimento = date.fromisoformat(request.form.get("data", ""))

            if not descricao or len(descricao) > 100:
                flash("Informe uma descrição e um valor positivo válido.", "error")
                return redirect(url_for("despesas"))
            if classificacao != "Fixa" and classificacao != "Variavel":
                flash("Escolha a classificação Fixa ou Variável.", "error")
                return redirect(url_for("despesas"))

            if registro is None:
                cursor.execute("""
                    INSERT INTO ARO_DESPESA
                    (ID_USUARIO, DESCRICAO, VALOR, DATA_MOVIMENTO, CLASSIFICACAO)
                    VALUES (?, ?, ?, ?, ?)
                """, (id_usuario, descricao, valor, data_movimento, classificacao))
                mensagem = "Despesa cadastrada com sucesso!"
            else:
                cursor.execute("""
                    UPDATE ARO_DESPESA
                    SET DESCRICAO = ?, VALOR = ?, DATA_MOVIMENTO = ?, CLASSIFICACAO = ?
                    WHERE ID_REGISTRO = ? AND ID_USUARIO = ?
                """, (descricao, valor, data_movimento, classificacao, id_registro, id_usuario))
                mensagem = "Despesa atualizada com sucesso!"

            # Atualiza o saldo mensal
            atualizar_saldo_mensal(cursor, id_usuario, data_movimento)
            if registro and registro[3] != data_movimento:
                atualizar_saldo_mensal(cursor, id_usuario, registro[3])
            con.commit()
            flash(mensagem, "success")
            return redirect(url_for("despesas"))

        cursor.execute("""
            SELECT ID_REGISTRO, DESCRICAO, VALOR, DATA_MOVIMENTO, CLASSIFICACAO
            FROM ARO_DESPESA WHERE ID_USUARIO = ?
            ORDER BY DATA_MOVIMENTO DESC, ID_REGISTRO DESC
        """, (id_usuario,))
        registros = cursor.fetchall()
        return render_template("despesas.html", registros=registros, registro=registro,
                               hoje=date.today().isoformat())

    except (ValueError, InvalidOperation):
        con.rollback()
        flash("Confira a data e informe um valor positivo com até duas casas decimais.", "error")
        return redirect(url_for("despesas", id_registro=id_registro))
    except Exception as e:
        con.rollback()
        app.logger.exception("Erro nas despesas: %s", e)
        flash("Erro ao consultar ou salvar despesas. Veja o terminal.", "error")
        return redirect(url_for("novo"))
    finally:
        con.rollback()
        cursor.close()


@app.route("/despesas/<int:id_registro>/excluir", methods=["POST"])
def excluir_despesa(id_registro):
    if not usuario_autenticado():
        return redirect(url_for("login"))
    id_usuario = session["id_usuario"]
    cursor = con.cursor()
    try:
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


# CONSULTAR SALDO MENSAL
@app.route("/saldos_mensais")
def saldos_mensais():
    if not usuario_autenticado():
        return redirect(url_for("login"))
    cursor = con.cursor()
    try:
        cursor.execute("""
            SELECT ANO, MES, TOTAL_RECEITAS, TOTAL_DESPESAS, SALDO
            FROM ARO_SALDO_MENSAL WHERE ID_USUARIO = ?
            ORDER BY ANO DESC, MES DESC
        """, (session["id_usuario"],))
        saldos = cursor.fetchall()
        return render_template("saldos_mensais.html", saldos=saldos)
    except Exception as e:
        app.logger.exception("Erro ao listar saldo mensal: %s", e)
        flash("Erro ao listar saldo mensal. Veja o terminal.", "error")
        return redirect(url_for("novo"))
    finally:
        con.rollback()
        cursor.close()


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

    # A verificação também fica no Python para impedir acesso pela URL.
    if session["tipo_usuario"] != TIPO_ADM:
        flash("Somente o administrador pode acessar as taxas.", "error")
        return redirect(url_for("novo"))

    cursor = con.cursor()
    try:
        id_edicao = request.args.get("editar", type=int)
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
        # A tabela ARO_TAXA aceita percentual maior que 0 e até 100.
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

        # A data não vem do formulário: CURRENT_DATE usa a data do banco.
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


if __name__ == "__main__":
    app.run(debug=True)
