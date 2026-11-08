"""
Génère un hash bcrypt pour users.json / GATEWAY_USERS.

Usage:
    python -m gateway.hash_password            # saisie masquée
    python -m gateway.hash_password 'monMdp'   # en argument (visible dans l'historique shell)

Exemple d'entrée users.json:
    {"username": "admin", "password_hash": "<sortie de ce script>", "roles": ["admin"]}
"""
import getpass
import sys

import bcrypt


def main() -> None:
    if len(sys.argv) > 1:
        pwd = sys.argv[1]
    else:
        pwd = getpass.getpass("Mot de passe: ")
        if pwd != getpass.getpass("Confirmer: "):
            print("Les mots de passe ne correspondent pas.", file=sys.stderr)
            sys.exit(1)
    if len(pwd) < 8:
        print("Refusé: 8 caractères minimum.", file=sys.stderr)
        sys.exit(1)
    print(bcrypt.hashpw(pwd.encode(), bcrypt.gensalt()).decode())


if __name__ == "__main__":
    main()
