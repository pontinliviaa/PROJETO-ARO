from flask_bcrypt import generate_password_hash
senha_hash = generate_password_hash("Adm@123").decode("utf-8")
print(senha_hash)