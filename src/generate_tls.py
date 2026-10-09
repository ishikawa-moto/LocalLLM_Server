"""Generate LocalBrain CA, server certificate, and client certificate on ServerPC."""
from __future__ import annotations
import argparse, datetime as dt, ipaddress, os
from pathlib import Path
from site_settings import site_value
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT=Path(__file__).resolve().parents[1]; TLS=ROOT/'config'/'tls'
def key(): return rsa.generate_private_key(public_exponent=65537,key_size=3072)
def write_key(path,value): path.write_bytes(value.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
def write_cert(path,value): path.write_bytes(value.public_bytes(serialization.Encoding.PEM))
def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--rotate',action='store_true'); args=parser.parse_args()
    TLS.mkdir(parents=True,exist_ok=True); managed=[TLS/name for name in ('ca.pem','ca-key.pem','server.pem','server-key.pem','client.pem','client-key.pem')]
    if any(path.exists() for path in managed) and not args.rotate: raise ValueError('TLS files already exist; use --rotate only during an approved coordinated rotation')
    now=dt.datetime.now(dt.timezone.utc); ca_key=key(); ca_name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'LocalBrain private CA')])
    ca=(x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-dt.timedelta(days=1)).not_valid_after(now+dt.timedelta(days=3650)).add_extension(x509.BasicConstraints(ca=True,path_length=0),True)
        .add_extension(x509.KeyUsage(True,False,False,True,True,True,False,False,False),True).sign(ca_key,hashes.SHA256()))
    write_cert(TLS/'ca.pem',ca); write_key(TLS/'ca-key.pem',ca_key)
    def leaf(name,usage,san,cert_name,key_name):
        private=key(); builder=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,name)]))
            .issuer_name(ca.subject).public_key(private.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-dt.timedelta(days=1)).not_valid_after(now+dt.timedelta(days=825))
            .add_extension(x509.BasicConstraints(ca=False,path_length=None),True).add_extension(x509.ExtendedKeyUsage([usage]),False))
        if san: builder=builder.add_extension(x509.SubjectAlternativeName(san),False)
        write_cert(TLS/cert_name,builder.sign(ca_key,hashes.SHA256())); write_key(TLS/key_name,private)
    leaf('LocalBrain Server',ExtendedKeyUsageOID.SERVER_AUTH,[x509.IPAddress(ipaddress.ip_address(site_value('server_address', 'SERVER_HOST', ROOT))),x509.DNSName('localbrain-70')],'server.pem','server-key.pem')
    leaf('LocalBrain Client',ExtendedKeyUsageOID.CLIENT_AUTH,[],'client.pem','client-key.pem')
    if os.name=='nt':
        for path in (TLS/'ca-key.pem',TLS/'server-key.pem',TLS/'client-key.pem'): os.system(f'icacls "{path}" /inheritance:r /grant:r "%USERNAME%:(F)" "SYSTEM:(F)" >NUL')
    print(f'TLS material generated under {TLS}; private keys remain on ServerPC until encrypted export')
if __name__=='__main__': main()
