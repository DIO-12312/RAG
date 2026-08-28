"""Generate development/test Search Guard TLS material without demo credentials."""

from __future__ import annotations

import argparse
import secrets
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

NODE_DN: Final = {
    NameOID.COMMON_NAME: "elasticsearch",
    NameOID.ORGANIZATIONAL_UNIT_NAME: "RAG",
    NameOID.ORGANIZATION_NAME: "RAG",
    NameOID.LOCALITY_NAME: "Local",
    NameOID.COUNTRY_NAME: "CN",
}
ADMIN_DN: Final = {
    NameOID.COMMON_NAME: "sg_admin",
    NameOID.ORGANIZATIONAL_UNIT_NAME: "RAG",
    NameOID.ORGANIZATION_NAME: "RAG",
    NameOID.LOCALITY_NAME: "Local",
    NameOID.COUNTRY_NAME: "CN",
}
NODE_FILES: Final = (
    "ca.pem",
    "node.pem",
    "node-key.pem",
    "admin.pem",
    "admin-key.pem",
    "rag_mvp_password",
)
CLIENT_FILES: Final = ("ca.pem", "rag_mvp_password")


def _name(values: dict[NameOID, str]) -> x509.Name:
    return x509.Name([x509.NameAttribute(oid, value) for oid, value in values.items()])


def _write_private(path: Path, key: rsa.RSAPrivateKey) -> None:
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    path.chmod(0o600)


def _write_public(path: Path, certificate: x509.Certificate) -> None:
    path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    path.chmod(0o644)


def _build_ca(now: datetime) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "rag-search-guard-local-ca")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return key, certificate


def _build_leaf(
    *,
    values: dict[NameOID, str],
    ca_key: rsa.RSAPrivateKey,
    ca_certificate: x509.Certificate,
    now: datetime,
    usages: list[x509.ObjectIdentifier],
    names: list[x509.GeneralName] | None = None,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(values))
        .issuer_name(ca_certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage(usages), critical=False)
    )
    if names is not None:
        builder = builder.add_extension(x509.SubjectAlternativeName(names), critical=False)
    return key, builder.sign(ca_key, hashes.SHA256())


def _required_paths(
    node_output: Path, client_output: Path
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    return (
        tuple(node_output / name for name in NODE_FILES),
        tuple(client_output / name for name in CLIENT_FILES),
    )


def _validate_existing(node_output: Path, client_output: Path) -> bool:
    node_paths, client_paths = _required_paths(node_output, client_output)
    return all(path.is_file() for path in (*node_paths, *client_paths))


def _validate_production(node_output: Path, client_output: Path) -> bool:
    if not _validate_existing(node_output, client_output):
        return False
    private_paths = (
        node_output / "node-key.pem",
        node_output / "admin-key.pem",
        client_output / "rag_mvp_password",
    )
    if any(stat.S_IMODE(path.stat().st_mode) & 0o077 for path in private_paths):
        return False
    try:
        node_certificate = x509.load_pem_x509_certificate((node_output / "node.pem").read_bytes())
        admin_certificate = x509.load_pem_x509_certificate((node_output / "admin.pem").read_bytes())
    except ValueError:
        return False
    return node_certificate.subject == _name(NODE_DN) and admin_certificate.subject == _name(
        ADMIN_DN
    )


def generate(node_output: Path, client_output: Path) -> None:
    node_output.mkdir(mode=0o700, parents=True, exist_ok=True)
    client_output.mkdir(mode=0o700, parents=True, exist_ok=True)
    now = datetime.now(UTC)
    ca_key, ca_certificate = _build_ca(now)
    node_key, node_certificate = _build_leaf(
        values=NODE_DN,
        ca_key=ca_key,
        ca_certificate=ca_certificate,
        now=now,
        usages=[ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH],
        names=[x509.DNSName("elasticsearch"), x509.DNSName("localhost")],
    )
    admin_key, admin_certificate = _build_leaf(
        values=ADMIN_DN,
        ca_key=ca_key,
        ca_certificate=ca_certificate,
        now=now,
        usages=[ExtendedKeyUsageOID.CLIENT_AUTH],
    )
    _write_public(node_output / "ca.pem", ca_certificate)
    _write_public(client_output / "ca.pem", ca_certificate)
    _write_public(node_output / "node.pem", node_certificate)
    _write_private(node_output / "node-key.pem", node_key)
    _write_public(node_output / "admin.pem", admin_certificate)
    _write_private(node_output / "admin-key.pem", admin_key)
    password = secrets.token_urlsafe(32) + "\n"
    for password_path in (
        node_output / "rag_mvp_password",
        client_output / "rag_mvp_password",
    ):
        password_path.write_text(password, encoding="utf-8")
        password_path.chmod(0o600)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="prepare Search Guard TLS material")
    parser.add_argument(
        "--environment", choices=("development", "test", "production"), required=True
    )
    parser.add_argument("--node-output", type=Path, required=True)
    parser.add_argument("--client-output", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.environment == "production":
        if _validate_production(arguments.node_output, arguments.client_output):
            return 0
        print("missing required Search Guard material", file=sys.stderr)
        return 2
    if not _validate_existing(arguments.node_output, arguments.client_output):
        generate(arguments.node_output, arguments.client_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
