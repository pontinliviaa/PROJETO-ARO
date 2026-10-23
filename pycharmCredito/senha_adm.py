from flask_bcrypt import generate_password_hash
senha_hash = generate_password_hash("Admin@123").decode("utf-8")
print(senha_hash)