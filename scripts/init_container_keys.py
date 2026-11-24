
"""Jednorazowa inicjalizacja kluczy JWT w nazwanych wolumenach Compose."""
from pathlib import Path

from init_auth import generate_keys


if __name__ == "__main__":
    private_dir = Path("/keys/private")
    public_file = Path("/keys/public/jwt-public.pem")
    generate_keys(private_dir)
    public = (private_dir / "jwt-public.pem").read_bytes()
    if public_file.exists() and public_file.read_bytes() != public:
        raise RuntimeError("Public JWT volume does not match the private key")
    public_file.write_bytes(public)
    print("JWT keys ready")
