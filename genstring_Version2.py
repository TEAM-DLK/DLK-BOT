# small helper to generate a random assistant-style string (not a real session generator)
import secrets
import base64

def gen_token(nbytes=32):
    return base64.urlsafe_b64encode(secrets.token_bytes(nbytes)).decode('utf-8').rstrip('=')

if __name__ == "__main__":
    print("Random token (example):")
    print(gen_token())