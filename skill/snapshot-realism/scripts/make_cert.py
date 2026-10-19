#!/usr/bin/env python3
"""Make a private signing certificate for realism.py --sign.

Writes two files into FOLDER:
  signer-chain.pem  the signing certificate, then the private root that issued it
  signer-key.pem    the signing certificate's private key, unencrypted: keep it private

C2PA refuses a certificate that signs itself, so this makes a root and a
signing certificate issued by it (EC P-256, for ES256). The root is on no
C2PA trust list, so validators show the signer as unknown or untrusted. The
signature still verifies: anyone can check that the file has not changed
since it was signed and read the input's own Content Credentials inside it.
For a signer validators recognize, use a certificate from a certificate
authority on the C2PA trust list instead.

There is no timestamp, so a validator checks the signature against the
certificate's dates: it reads as expired after --days (default 1825, five
years).

Usage:
  python make_cert.py FOLDER [--name "Your Name"] [--days 1825]

Needs the cryptography package, which c2pa-python installs.
"""
import argparse
import datetime
import os
import sys

CHAIN = "signer-chain.pem"
KEY = "signer-key.pem"


def make(name="snapshot-realism signer", days=1825, key=None):
    """(certificate chain PEM, private key PEM) for a signing certificate issued by a new private root.

    key is the signing certificate's private key (a cryptography key object);
    the default is a new EC P-256 key.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    def usage(sign=False, certs=False):
        return x509.KeyUsage(digital_signature=sign, content_commitment=False, key_encipherment=False,
                             data_encipherment=False, key_agreement=False, key_cert_sign=certs, crl_sign=certs,
                             encipher_only=False, decipher_only=False)

    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    start = now - datetime.timedelta(hours=1)
    root_key = ec.generate_private_key(ec.SECP256R1())
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name + " root"),
                           x509.NameAttribute(NameOID.ORGANIZATION_NAME, name)])
    root = (x509.CertificateBuilder().subject_name(root_name).issuer_name(root_name)
            .public_key(root_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(start).not_valid_after(now + datetime.timedelta(days=days + 1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(usage(certs=True), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()), critical=False)
            .sign(root_key, hashes.SHA256()))
    key = key or ec.generate_private_key(ec.SECP256R1())
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name),
                           x509.NameAttribute(NameOID.ORGANIZATION_NAME, name)])
    leaf = (x509.CertificateBuilder().subject_name(leaf_name).issuer_name(root_name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(start).not_valid_after(now + datetime.timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(usage(sign=True), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()), critical=False)
            .sign(root_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    chain = leaf.public_bytes(pem) + root.public_bytes(pem)
    private = key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return chain, private


def main(argv=None):
    ap = argparse.ArgumentParser(description="Make a private C2PA signing certificate for realism.py --sign.")
    ap.add_argument("folder", help="existing folder to write signer-chain.pem and signer-key.pem into")
    ap.add_argument("--name", default="snapshot-realism signer",
                    help="signer name validators show (default: snapshot-realism signer)")
    ap.add_argument("--days", type=int, default=1825, help="days the certificate is valid, 1 to 36500 (default 1825)")
    args = ap.parse_args(argv)
    if not 1 <= args.days <= 36500:
        ap.error("--days must be between 1 and 36500")
    if not args.name.strip():
        ap.error("--name must not be empty")
    if not os.path.isdir(args.folder):
        ap.error("folder does not exist: %s" % args.folder)
    paths = [os.path.join(args.folder, n) for n in (CHAIN, KEY)]
    existing = [p for p in paths if os.path.exists(p)]
    if existing:
        ap.error("%s already exists; move it or choose another folder" % existing[0])
    try:
        chain, private = make(args.name.strip(), args.days)
    except ImportError:
        print("error: make_cert.py needs cryptography: python -m pip install c2pa-python (it installs "
              "cryptography)", file=sys.stderr)
        return 1
    for path, data, mode in ((paths[0], chain, 0o644), (paths[1], private, 0o600)):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    print("wrote %s (certificate chain) and %s (private key; keep it private)" % tuple(paths))
    print("sign with: python realism.py INPUT --origin ... --preset ... --sign %s %s" % tuple(paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
